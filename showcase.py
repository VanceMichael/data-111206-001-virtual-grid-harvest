"""真实无头浏览器端到端演示。

只通过浏览器实际渲染的目录 DOM 与详情弹层取数，不读取站点内部数组或数据接口。
分五段展示：
1. 虚拟滚动的 DOM 节点复用（同一 recycled 节点先后代表不同记录）；
2. 同名展商按 reference 区分为不同主体，完整覆盖全部逻辑行；
3. 滚动中插入新展商导致代际变化：旧代际作废、有限重试后按新代际重采，不拼接；
4. 中途关闭浏览器后的持久化续采：重新验证代际、已发布详情不重复；
5. 重试预算耗尽时挂起并给出未完成原因（不声称完整）。

每个场景使用独立的本机 portal 实例，避免上一段推进的页面代际影响下一段。
"""
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from portal import serve
from raw_store import RawStore
from progress_store import ProgressStore
from collector import collect_catalog, CollectionInterrupted, CatalogCollectionSuspended


def section(title):
    print('\n' + '=' * 72)
    print(title)
    print('=' * 72)


def open_stores(root):
    return (RawStore(root / 'raw.sqlite3'),
            ProgressStore(root / 'progress.sqlite3'))


def show_node_reuse():
    section('1. 节点复用：虚拟滚动回收 DOM，同一节点先后代表不同逻辑记录')
    with serve() as base, sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 720})
            page.goto(base, wait_until='domcontentloaded')
            page.locator('#viewport[data-state="ready"]').wait_for()
            node = page.locator('#recycled-0')
            first_ref = node.locator('button').get_attribute('data-reference')
            first_index = node.get_attribute('data-row-index')
            page.locator('#viewport').evaluate('(node) => {node.scrollTop=520;}')
            # 等实际渲染状态，不做固定睡眠
            page.wait_for_function(
                'document.querySelector("#recycled-0").dataset.rowIndex === "10"')
            later_ref = node.locator('button').get_attribute('data-reference')
            later_index = node.get_attribute('data-row-index')
            print(f'同一节点 #recycled-0：滚动前代表逻辑行 {first_index}（{first_ref}），'
                  f'滚动后代表逻辑行 {later_index}（{later_ref}）')
            print(f'节点 DOM 身份未变、记录已变：{first_ref != later_ref}；'
                  '因此采集只信点击时刻的 data-reference，不缓存旧节点')
            node.locator('button').click()
            page.locator('#detail[open]').wait_for(state='attached')
            print('详情弹层归属校验：',
                  page.locator('#detail').get_attribute('data-reference') == later_ref)
        finally:
            browser.close()


def show_full_and_same_name(root):
    section('2. 完整采集 + 同名展商：17 个逻辑行全覆盖，同名企业按 reference 区分')
    with serve() as base:
        raw, progress = open_stores(root / 'full')
        try:
            result = collect_catalog(base, progress, raw, job_id='showcase-full')
        finally:
            raw.close()
            progress.close()
    print(f"代际 {result['final_revision']}，声明 {result['declared_total']} 行，"
          f"实采 {len(result['catalog'])} 行，发布详情 {result['publication_count']} 条")
    print('同名主体统计：', result['same_name_subjects'])
    for row in result['catalog']:
        if row['name'] == '合成同名企业':
            print(f"  逻辑行 {row['row_index']}: {row['reference']} / {row['booth']}")
    evidence = result['end_evidence']
    print('到尾证据：滚动到底=%s，内容高度=%s，逻辑行覆盖=%s，尾屏复读一致=%s，代际未变=%s'
          % (evidence['at_bottom'], evidence['height_matches_declared_total'],
             evidence['missing_logical_rows'] == [], evidence['tail_stable'],
             evidence['revision_unchanged']))


def show_mutation(root):
    section('3. 插入变更：滚动中代际 1→2，旧代际作废不拼接，有限重试后整代重采')
    with serve() as base:
        raw, progress = open_stores(root / 'mutation')
        try:
            result = collect_catalog(base + '?mutate=1', progress, raw,
                                     job_id='showcase-mut')
        finally:
            raw.close()
            progress.close()
    print('尝试记录（按代际隔离）：')
    for attempt in result['attempts']:
        print(f"  尝试 {attempt['attempt_no']} 代际 {attempt['revision']} -> "
              f"{attempt['status']} {attempt['reason'] or ''}")
    print(f"最终名单只来自代际 {result['final_revision']}，共 "
          f"{len(result['catalog'])} 行（含插入的 EXH-NEW），"
          f"新发布 {result['published_new']} 条、跳过已发布 {result['published_skipped']} 条")


def show_restart(root):
    section('4. 中途重启：浏览器关闭后从持久化记录续采，复验代际且不重复发布')
    with serve() as base:
        raw, progress = open_stores(root / 'restart')
        try:
            try:
                collect_catalog(base, progress, raw,
                                job_id='showcase-restart', stop_after=4)
            except CollectionInterrupted as exc:
                print('首次运行被中断：', exc.message)
            before = progress.summary('showcase-restart')
            print('持久化状态：status=%s，已发布 %d 条，尝试覆盖行 %s'
                  % (before['status'], before['publication_count'],
                     before['attempts'][0]['covered']))
            # 重新打开浏览器（collect_catalog 内部新建 Playwright 会话）后续采
            result = collect_catalog(base, progress, raw, job_id='showcase-restart')
            print('续采结果：status=%s，resumed=%s，新发布 %d 条，跳过已发布 %d 条，台账总数 %d'
                  % (result['status'], result['resumed'], result['published_new'],
                     result['published_skipped'], result['publication_count']))
        finally:
            raw.close()
            progress.close()


def show_suspension(root):
    section('5. 挂起策略：重试预算耗尽时给原因，不把不同代际拼成名单')
    with serve() as base:
        raw, progress = open_stores(root / 'suspend')
        try:
            result = collect_catalog(base + '?mutate=1', progress, raw,
                                     job_id='showcase-susp', max_attempts=1)
            print('挂起原因：', result['suspension_reason'])
            print('未完成说明：', result['suspension_detail'])
            print('作业状态：', result['status'], '；尝试记录：',
                  [(a['attempt_no'], a['revision'], a['status'], a['reason'])
                   for a in result['attempts']])
            try:
                collect_catalog(base + '?mutate=1', progress, raw,
                                job_id='showcase-susp', max_attempts=1)
            except CatalogCollectionSuspended as exc:
                print('再次调用同一挂起作业被拒绝：', exc.reason)
        finally:
            raw.close()
            progress.close()


def main():
    workdir = Path(tempfile.mkdtemp(prefix='showcase-'))
    print('演示原文与进度库目录：', workdir)
    try:
        show_node_reuse()
        show_full_and_same_name(workdir)
        show_mutation(workdir)
        show_restart(workdir)
        show_suspension(workdir)
        section('演示结束：原始观察在 raw.sqlite3，作业/代际/台账在 *-progress.sqlite3')
        print('可用 python3 app.py progress --output <目录>/raw.sqlite3 --job-id <作业> 复查')
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    main()
