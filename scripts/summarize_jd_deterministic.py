#!/usr/bin/env python3
import argparse, re
from pathlib import Path
import yaml

NOISE = ['【工作职责】','【任职要求】','【我们提供】','岗位职责','职位描述']


def summarize(text: str, mn: int, mx: int) -> str:
    t = text or ''
    for n in NOISE:
        t = t.replace(n, '')
    t = re.sub(r'\s+', '', t)
    segs = re.split(r'[。；\n]|\d+、', t)
    segs = [s.strip('：:，, ') for s in segs if s.strip()]
    out = ''
    for s in segs:
        if len(s) < 8:
            continue
        out = (out + '，' + s).strip('，') if out else s
        if len(out) >= mn:
            break
    if not out:
        out = t[:mx]
    if len(out) < mn:
        out = (out + '，参与核心业务与跨团队协作')[:mn]
    if len(out) > mx:
        out = out[:mx]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--yaml', required=True)
    ap.add_argument('--min-len', type=int, default=30)
    ap.add_argument('--max-len', type=int, default=50)
    args = ap.parse_args()

    p = Path(args.yaml)
    data = yaml.safe_load(p.read_text(encoding='utf-8')) or {'internships': []}
    cnt = 0
    for it in data.get('internships', []):
        if not isinstance(it, dict):
            continue
        jd = it.get('jd_full', '') or ''
        if not jd:
            continue
        it['jd_summary'] = summarize(jd, args.min_len, args.max_len)
        cnt += 1

    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'summarized={cnt}')


if __name__ == '__main__':
    main()
