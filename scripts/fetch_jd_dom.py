#!/usr/bin/env python3
"""
fetch_jd_dom.py — 通过 Chrome MCP 抓取 BOSS直聘 JD 详情页原始文本。

硬阻断规则：
- jd_full 为空 → 写 fetch_error，exit(1) 让 lobster failFast 捕获
- 不做任何摘要/推断，只写原始 DOM 文本
"""
import argparse, json, subprocess, sys
from pathlib import Path
import yaml

ROOT = Path('/Users/okonfu/.openclaw/workspace')
MCP  = ROOT / 'skills/internship-scout/scripts/mcp_call.py'

JS_EXTRACT = """
(() => {
  const el = document.querySelector('.job-sec-text');
  return el ? el.innerText.trim() : '';
})()
""".strip()


def run_mcp(tool, args_dict):
    return subprocess.check_output(
        ['python3', str(MCP), tool, json.dumps(args_dict, ensure_ascii=False)],
        text=True, stderr=subprocess.DEVNULL
    ).strip()


def fetch_jd(url: str, retries: int = 1) -> str:
    run_mcp('chrome_navigate', {'url': url})
    for _ in range(retries + 1):
        jd = run_mcp('chrome_javascript', {'code': JS_EXTRACT})
        if jd:
            return jd
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--yaml',    required=True)
    ap.add_argument('--limit',   type=int, default=5)
    ap.add_argument('--retries', type=int, default=1)
    args = ap.parse_args()

    limit = max(1, min(5, args.limit))  # 硬上限 5
    p     = Path(args.yaml)
    items = yaml.safe_load(p.read_text(encoding='utf-8')) or []

    # 只处理 jd_full 为空且没有 fetch_error 的条目
    targets = [
        it for it in items
        if isinstance(it, dict)
        and not (it.get('jd_full') or '').strip()
        and not it.get('fetch_error')
    ][:limit]

    failed = 0
    for it in targets:
        url = it.get('url', '')
        if not url:
            continue

        jd = fetch_jd(url, retries=args.retries)

        if not jd:
            # 硬阻断：标记错误，不写任何内容
            it['fetch_error'] = 'empty_job_sec_text'
            print(f'FETCH_FAILED: {url}', file=sys.stderr)
            failed += 1
        else:
            it['jd_full'] = jd
            # 确保没有残留的 fetch_error
            it.pop('fetch_error', None)

    p.write_text(yaml.dump(items, allow_unicode=True, sort_keys=False), encoding='utf-8')

    if failed:
        print(f'fetched={len(targets)-failed} failed={failed}')
        sys.exit(1)  # 触发 lobster failFast

    print(f'fetched={len(targets)} failed=0')


if __name__ == '__main__':
    main()
