#!/usr/bin/env python3
import argparse, json, subprocess
from pathlib import Path
import yaml

ROOT = Path('/Users/okonfu/.openclaw/workspace')
MCP = ROOT / 'skills/internship-scout/scripts/mcp_call.py'


def run_mcp(tool, args):
    return subprocess.check_output(['python3', str(MCP), tool, json.dumps(args, ensure_ascii=False)], text=True).strip()


def read_jd_once(url: str) -> str:
    run_mcp('chrome_navigate', {'url': url})
    js = """
(() => {
  const el = document.querySelector('.job-sec-text');
  return el ? el.innerText : '';
})()
""".strip()
    return run_mcp('chrome_javascript', {'code': js}).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--yaml', required=True)
    ap.add_argument('--limit', type=int, default=5)
    ap.add_argument('--retry-empty', type=int, default=1)
    args = ap.parse_args()

    limit = max(1, min(5, args.limit))
    p = Path(args.yaml)
    data = yaml.safe_load(p.read_text(encoding='utf-8')) or {'internships': []}
    items = data.get('internships', [])

    targets = [it for it in items if isinstance(it, dict) and not (it.get('jd_full') or '').strip()][:limit]
    ok = 0
    for it in targets:
        url = it.get('url','')
        if not url:
            continue
        jd = read_jd_once(url)
        if not jd and args.retry_empty > 0:
            jd = read_jd_once(url)
        if jd:
            it['jd_full'] = jd
            ok += 1
        else:
            it['fetch_error'] = 'empty_job_sec_text'

    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'fetched={ok} attempted={len(targets)}')


if __name__ == '__main__':
    main()
