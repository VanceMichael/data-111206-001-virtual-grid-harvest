"""浏览器全量目录采集。

数据来源只有真实无头浏览器渲染出的目录 DOM 与详情弹层：
不读取模拟站点内部数组，也不调用 /seed、/advance 之类的数据接口。

虚拟滚动会回收 DOM 节点，因此本模块以页面代际（data-revision）+
逻辑行号（data-row-index）+ 记录主键（data-reference）三元组定位记录：
- 同名展商 reference 不同，视为不同主体；
- 每次观察都核对当前代际，代际变化立即中止当前尝试，绝不跨代际拼接；
- 到尾必须同时满足滚动到底、内容高度等于声明总行高、逻辑行 0..N-1
  全部覆盖且尾屏两次读取一致，单纯一屏重复或滚动条到底不算完整；
- 滚动等待全部基于页面实际状态（wait_for_function），不使用固定睡眠。
"""
import json
import uuid
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from progress_store import utc_now

ROW_HEIGHT = 52
POOL_SIZE = 5
REVISION_CHANGED = 'revision-changed'
REASON_REVALIDATION = 'resume-revision-mismatch'
REASON_BUDGET = 'retry-budget-exhausted'


class CatalogCollectionSuspended(Exception):
    """重试预算耗尽或恢复时代际已变，作业挂起（不产出完整名单）。"""

    def __init__(self, reason, detail):
        super().__init__(f'{reason}: {detail}')
        self.reason = reason
        self.detail = detail


class CollectionInterrupted(Exception):
    """采集在完成前被中断（如演示用 stop_after 或浏览器关闭），进度已落盘可续采。"""

    def __init__(self, message, hook):
        super().__init__(message)
        self.message = message
        self.steps = hook.steps
        self.covered = sorted(hook.covered)
        self.revision = hook.revision


def _utc_iso():
    return datetime.now(timezone.utc).isoformat()


def _viewport_state(page):
    node = page.locator('#viewport')
    return {
        'revision': node.get_attribute('data-revision'),
        'declared_total': int(node.get_attribute('data-total')),
        'state': node.get_attribute('data-state'),
    }


def _snapshot_rows(page):
    """读取当前视口内真实可见的目录行（固定分组行不在 #viewport 内，不会混入）。"""
    locator = page.locator('.row:not([hidden])')
    rows = []
    for index in range(locator.count()):
        row = locator.nth(index)
        rows.append({
            'row_index': int(row.get_attribute('data-row-index')),
            'reference': row.locator('button').get_attribute('data-reference'),
            'text': row.locator('span').inner_text(),
        })
    return rows


def _scroll_to_row(page, target):
    """滚动到逻辑行 target 并等待该行真正渲染出来（按实际页面状态等待）。"""
    page.locator('#viewport').evaluate(
        '(node, target) => { node.scrollTop = target * 52; }', target)
    page.wait_for_function(
        """([target, revision]) => {
            const viewport = document.querySelector('#viewport');
            if (viewport.dataset.state !== 'ready') return false;
            if (viewport.dataset.revision !== revision) return true;
            return Boolean(document.querySelector(
                `.row:not([hidden])[data-row-index="${target}"]`));
        }""",
        arg=[target, _viewport_state(page)['revision']],
    )


def _open_detail_for(page, reference, max_attempts=3):
    """打开 reference 对应详情。

    节点可能已被回收并代表另一条记录，因此每次都重新滚动定位、
    以点击时刻按钮上的 data-reference 为准，并核对弹层实际归属；
    不一致就重新定位重试，而不是沿用旧节点。
    """
    row_locator = page.locator(f'.row:not([hidden]) button[data-reference="{reference}"]')
    for _ in range(max_attempts):
        if row_locator.count() > 0:
            row = row_locator.first.locator('xpath=parent::div')
            target_index = int(row.get_attribute('data-row-index'))
            _scroll_to_row(page, target_index)
            if row_locator.count() == 0:
                continue
            row_locator.first.click()
            page.locator('#detail[open]').wait_for(state='attached')
            actual = page.locator('#detail').get_attribute('data-reference')
            if actual == reference:
                detail = {
                    'reference': actual,
                    'name': page.locator('#detail h2').inner_text(),
                    'body': page.locator('#detail p').inner_text(),
                }
                page.locator('#close').click()
                page.locator('#detail:not([open])').wait_for(state='attached')
                return detail
            # 弹层归属与预期不符：节点已被复用，关掉重来
            page.locator('#close').click()
            page.locator('#detail:not([open])').wait_for(state='attached')
    raise RuntimeError(f'详情弹层无法稳定定位到 {reference}（DOM 节点复用冲突）')


def _read_tail_twice(page, revision, declared_total):
    """尾屏复读：两次读取的逻辑行与 reference 必须完全一致。"""
    last_target = max(0, declared_total - POOL_SIZE)

    def read_tail():
        _scroll_to_row(page, last_target)
        state = _viewport_state(page)
        if state['revision'] != revision:
            raise _RevisionChanged()
        return {(row['row_index'], row['reference']) for row in _snapshot_rows(page)}

    first = read_tail()
    second = read_tail()
    return first == second, sorted(first), sorted(second)


class _RevisionChanged(Exception):
    pass


def _run_attempt(page, base_revision, declared_total, progress, raw_store, job_id,
                 attempt_no, stop_after, resumed_covered):
    covered = set(resumed_covered)
    published_new = 0
    published_skipped = 0
    target = 0
    steps = 0
    while target < declared_total:
        _scroll_to_row(page, target)
        state = _viewport_state(page)
        if state['state'] != 'ready':
            raise RuntimeError('目录未处于 ready 状态')
        if state['revision'] != base_revision:
            raise _RevisionChanged()
        declared_total = state['declared_total']
        visible = _snapshot_rows(page)
        observed_at = _utc_iso()
        for row in visible:
            idx = row['row_index']
            if idx >= declared_total:
                # 渲染中可能短暂出现越界行，忽略后下一轮重新观察
                continue
            covered.add(idx)
            text_parts = [part.strip() for part in row['text'].split('|')]
            name = text_parts[0] if text_parts else ''
            country = text_parts[1] if len(text_parts) > 1 else ''
            booth = text_parts[2] if len(text_parts) > 2 else ''
            progress.save_row(job_id, base_revision, idx, row['reference'],
                              name, country, booth, observed_at)

        # 目标行详情：以当前 DOM 上的 reference 为准，关闭后不缓存旧节点
        target_row = next((row for row in visible if row['row_index'] == target), None)
        if target_row is None:
            # 目标行未渲染成功，下一轮重试同一目标，不前进
            progress.save_attempt(job_id, attempt_no, base_revision,
                                  declared_total, covered, 'running')
            continue
        reference = target_row['reference']
        if progress.is_published(job_id, reference):
            # 恢复续采：已发布详情不重复发布
            published_skipped += 1
        else:
            detail = _open_detail_for(page, reference)
            detail_encoded = json.dumps(
                {'detail': detail, 'revision': base_revision, 'row_index': target},
                ensure_ascii=False).encode()
            digest = raw_store.save(
                f'exhibitor:detail:{reference}', detail_encoded,
                {'kind': 'browser-detail', 'revision': base_revision,
                 'row_index': target, 'job_id': job_id}, observed_at)
            if progress.add_publication(job_id, reference, base_revision,
                                        target, digest, observed_at):
                published_new += 1

        steps += 1
        progress.save_attempt(job_id, attempt_no, base_revision,
                              declared_total, covered, 'running')
        if stop_after is not None and steps >= stop_after:
            raise _StoppedByHook(steps, covered, base_revision, declared_total)
        target += 1

    # ---- 到尾判定：多重证据，缺一不可 ----
    end_metrics = page.locator('#viewport').evaluate(
        '(node) => ({scrollTop: node.scrollTop, scrollHeight: node.scrollHeight,'
        ' clientHeight: node.clientHeight})')
    content_height = page.locator('#content').evaluate('(node) => node.style.height')
    at_bottom = end_metrics['scrollTop'] + end_metrics['clientHeight'] >= end_metrics['scrollHeight'] - 1
    height_ok = content_height == f'{declared_total * ROW_HEIGHT}px'
    expected = set(range(declared_total))
    coverage_ok = covered >= expected
    tail_stable, tail_a, tail_b = _read_tail_twice(page, base_revision, declared_total)
    end_state = _viewport_state(page)
    revision_ok = end_state['revision'] == base_revision and end_state['declared_total'] == declared_total

    complete = at_bottom and height_ok and coverage_ok and tail_stable and revision_ok
    evidence = {
        'revision': base_revision,
        'declared_total': declared_total,
        'covered_logical_rows': sorted(covered),
        'missing_logical_rows': sorted(expected - covered),
        'scroll': end_metrics,
        'at_bottom': at_bottom,
        'content_height': content_height,
        'height_matches_declared_total': height_ok,
        'tail_first_read': tail_a,
        'tail_second_read': tail_b,
        'tail_stable': tail_stable,
        'revision_unchanged': revision_ok,
    }
    if not complete:
        raise RuntimeError(f'到尾证据不足: {evidence}')
    return evidence, published_new, published_skipped


class _StoppedByHook(Exception):
    def __init__(self, steps, covered, revision, declared_total):
        self.steps = steps
        self.covered = covered
        self.revision = revision
        self.declared_total = declared_total


def collect_catalog(url, progress, raw_store, job_id=None, max_attempts=3,
                    stop_after=None):
    """执行一次可恢复的全量采集，返回结构化结果。

    max_attempts 是重试上界；任一代际采集失败都不会与其他代际拼接。
    stop_after 用于演示中途重启：完成指定步数后抛出异常，但进度已落盘。
    """
    job_id = job_id or f'job-{uuid.uuid4().hex[:12]}'
    job = progress.get_job(job_id)
    if job is None:
        job = progress.create_job(job_id, url, max_attempts)
    if job['status'] == 'completed':
        result = _completed_result(progress, job_id, resumed=True,
                                   published_new=0, published_skipped=0)
        rows = progress.get_rows(job_id, job['final_revision'])
        names = {}
        for row in rows:
            names[row['name']] = names.get(row['name'], 0) + 1
        result['catalog'] = rows
        result['same_name_subjects'] = {name: count for name, count in names.items() if count > 1}
        result['end_evidence'] = None  # 本次会话未重新采集，证据以已完成尝试的记录为准
        return result
    if job['status'] == 'suspended':
        raise CatalogCollectionSuspended(job['reason'], '作业已挂起，需人工介入后重建作业')

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 720})
            page.goto(url, wait_until='domcontentloaded')
            page.locator('#viewport[data-state="ready"]').wait_for()

            prior = progress.latest_attempt(job_id)
            if prior is not None and prior['status'] == 'running':
                # 恢复续采：必须重新验证页面代际
                live = _viewport_state(page)
                if live['revision'] != prior['revision']:
                    reason = (f"持久化代际 {prior['revision']} 与当前页面代际 "
                              f"{live['revision']} 不一致，旧尝试作废不拼接")
                    progress.save_attempt(job_id, prior['attempt_no'], prior['revision'],
                                          prior['declared_total'], prior['covered'],
                                          'abandoned', REASON_REVALIDATION)
                    if job['attempts_used'] >= max_attempts:
                        return _suspend(progress, job_id, REASON_BUDGET, reason)
                    attempt_no = prior['attempt_no'] + 1
                else:
                    attempt_no = prior['attempt_no']
            else:
                attempt_no = (job['attempts_used'] or 0) + 1

            while True:
                live = _viewport_state(page)
                base_revision = live['revision']
                declared_total = live['declared_total']
                resumed = progress.get_attempt(job_id, attempt_no)
                resumed_covered = set(resumed['covered']) if resumed else set()
                try:
                    evidence, published_new, published_skipped = _run_attempt(
                        page, base_revision, declared_total, progress, raw_store,
                        job_id, attempt_no, stop_after, resumed_covered)
                except _RevisionChanged:
                    live = _viewport_state(page)
                    detail = (f'尝试 {attempt_no} 在代际 {base_revision} 采集中检测到页面代际'
                              f'变为 {live["revision"]}，该代际结果作废，不与其他代际拼接')
                    progress.save_attempt(job_id, attempt_no, base_revision,
                                          declared_total, resumed_covered, 'aborted',
                                          REVISION_CHANGED)
                    if attempt_no >= max_attempts:
                        return _suspend(progress, job_id, REASON_BUDGET, detail)
                    attempt_no += 1
                    stop_after = None  # 重试不再注入中断钩子
                    continue
                except _StoppedByHook as hook:
                    progress.save_attempt(job_id, attempt_no, base_revision,
                                          hook.declared_total, hook.covered, 'running',
                                          f'stopped-after-{hook.steps}-steps')
                    raise CollectionInterrupted(
                        f'完成 {hook.steps} 个逻辑行的详情采集后中断；已观察覆盖逻辑行 '
                        f'{sorted(hook.covered)}（视口内预渲染），页面代际 {hook.revision}；'
                        f'进度与发布台账已持久化，可用同一 --job-id 续采', hook)
                progress.save_attempt(job_id, attempt_no, base_revision,
                                      declared_total, evidence['covered_logical_rows'],
                                      'completed')
                progress.update_job(job_id, status='completed',
                                    final_revision=base_revision,
                                    declared_total=declared_total)
                rows = progress.get_rows(job_id, base_revision)
                names = {}
                for row in rows:
                    names.setdefault(row['name'], 0)
                    names[row['name']] += 1
                same_name = {name: count for name, count in names.items() if count > 1}
                result = _completed_result(progress, job_id, resumed=bool(resumed),
                                           published_new=published_new,
                                           published_skipped=published_skipped)
                result['catalog'] = rows
                result['same_name_subjects'] = same_name
                result['end_evidence'] = evidence
                return result
        finally:
            browser.close()


def _suspend(progress, job_id, reason, detail):
    latest = progress.latest_attempt(job_id)
    revision = latest['revision'] if latest else None
    progress.update_job(job_id, status='suspended', reason=f'{reason}: {detail}',
                        final_revision=revision)
    summary = progress.summary(job_id)
    summary['suspended'] = True
    summary['suspension_reason'] = reason
    summary['suspension_detail'] = detail
    return summary


def _completed_result(progress, job_id, resumed, published_new, published_skipped):
    summary = progress.summary(job_id)
    summary['suspended'] = False
    summary['resumed'] = resumed
    summary['published_new'] = published_new
    summary['published_skipped'] = published_skipped
    return summary
