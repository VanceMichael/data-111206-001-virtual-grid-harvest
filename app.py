import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from raw_store import RawStore


def demo(store):
    observed_at = datetime.now(timezone.utc).isoformat()
    from browser_source import first_screen
    from portal import serve
    with serve() as base:
        observed = first_screen(base)
    encoded = json.dumps(observed, ensure_ascii=False).encode()
    digest = store.save('exhibitor:first-screen', encoded, {'kind': 'browser-observation'}, observed_at)
    return {'说明': '仅当前首屏，不代表完整目录', '代际': observed['revision'], '声明总行数': observed['declared_total'], '可见行数': len(observed['rows']), '原文摘要': digest}


def main():
    parser = argparse.ArgumentParser(description='本地原文采集演示')
    parser.add_argument('command', choices=['demo'])
    parser.add_argument('--output', default='output/raw.sqlite3')
    args = parser.parse_args()
    store = RawStore(args.output)
    try:
        result = demo(store)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
