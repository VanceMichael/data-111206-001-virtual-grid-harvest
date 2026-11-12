import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from raw_store import RawStore
from progress_store import ProgressStore
from collector import collect_catalog, CatalogCollectionSuspended, CollectionInterrupted


def demo(store):
    observed_at = datetime.now(timezone.utc).isoformat()
    from browser_source import first_screen
    from portal import serve
    with serve() as base:
        observed = first_screen(base)
    encoded = json.dumps(observed, ensure_ascii=False).encode()
    digest = store.save('exhibitor:first-screen', encoded, {'kind': 'browser-observation'}, observed_at)
    return {'说明': '仅当前首屏，不代表完整目录', '代际': observed['revision'], '声明总行数': observed['declared_total'], '可见行数': len(observed['rows']), '原文摘要': digest}


def _open_stores(output):
    raw = RawStore(output)
    progress_path = str(Path(output).with_name(Path(output).stem + '-progress.sqlite3'))
    progress = ProgressStore(progress_path)
    return raw, progress


def run_collect(args):
    from portal import serve
    raw, progress = _open_stores(args.output)
    try:
        if args.url:
            result = collect_catalog(args.url, progress, raw, job_id=args.job_id,
                                     max_attempts=args.max_attempts)
        else:
            with serve() as base:
                result = collect_catalog(base + ('?mutate=1' if args.mutate else ''),
                                         progress, raw, job_id=args.job_id,
                                         max_attempts=args.max_attempts,
                                         stop_after=args.stop_after)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('suspended') else 0
    except CatalogCollectionSuspended as exc:
        print(json.dumps({'suspended': True, 'reason': exc.reason, 'detail': exc.detail},
                         ensure_ascii=False, indent=2))
        return 2
    except CollectionInterrupted as exc:
        print(json.dumps({'interrupted': True, 'detail': exc.message,
                          'steps_completed': exc.steps,
                          'covered_logical_rows': exc.covered,
                          'revision': exc.revision}, ensure_ascii=False, indent=2))
        return 3
    finally:
        raw.close()
        progress.close()


def run_progress(args):
    _, progress = _open_stores(args.output)
    try:
        if args.job_id:
            result = progress.summary(args.job_id)
            if result is None:
                print(json.dumps({'error': f'作业 {args.job_id} 不存在'}, ensure_ascii=False))
                return 1
        else:
            result = {'jobs': progress.list_jobs()}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        progress.close()


def main():
    parser = argparse.ArgumentParser(description='本地原文采集与全量目录采集')
    sub = parser.add_subparsers(dest='command', required=True)

    demo_cmd = sub.add_parser('demo', help='读取首屏（保留原有单屏查看能力）')
    demo_cmd.add_argument('--output', default='output/raw.sqlite3')

    collect = sub.add_parser('collect', help='浏览器全量目录采集（可恢复）')
    collect.add_argument('--output', default='output/raw.sqlite3')
    collect.add_argument('--job-id', default=None, help='作业 ID；传入已有 ID 即续采')
    collect.add_argument('--max-attempts', type=int, default=3, help='代际变化重试上界')
    collect.add_argument('--stop-after', type=int, default=None,
                         help='采集 N 个逻辑行后中断（用于演示中途重启，进度已持久化）')
    collect.add_argument('--url', default=None, help='外部目录地址；缺省启动本机 portal')
    collect.add_argument('--mutate', action='store_true', help='本机页面滚动中模拟插入（?mutate=1）')

    progress_cmd = sub.add_parser('progress', help='查看采集进度与发布台账')
    progress_cmd.add_argument('--output', default='output/raw.sqlite3')
    progress_cmd.add_argument('--job-id', default=None, help='缺省列出全部作业')

    args = parser.parse_args()
    if args.command == 'demo':
        store = RawStore(args.output)
        try:
            print(json.dumps(demo(store), ensure_ascii=False, indent=2))
        finally:
            store.close()
    elif args.command == 'collect':
        raise SystemExit(run_collect(args))
    elif args.command == 'progress':
        raise SystemExit(run_progress(args))


if __name__ == '__main__':
    main()
