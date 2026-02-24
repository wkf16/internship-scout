#!/usr/bin/env python3
"""
fetch_job_links.py — 通过 BOSS直聘内部 API 抓取职位列表，写入 internships.yaml。
只写结构字段（title/company/salary/url 等），不写 jd_full/jd_summary（留给后续节点）。
"""
import argparse, json, re, subprocess, sys
from datetime import date
from pathlib import Path
import yaml

ROOT = Path('/Users/okonfu/.openclaw/workspace')
MCP  = ROOT / 'skills/internship-scout/scripts/mcp_call.py'

BIG_TECH = {'字节','抖音','tiktok','阿里','淘宝','天猫','腾讯','百度','美团','京东',
            '华为','小米','网易','bilibili','哔哩','滴滴','快手','拼多多','蚂蚁',
            '微软','谷歌','google','microsoft','apple','苹果'}

NON_TECH  = {'销售','运营','市场','客服','行政','财务','人事','hr','猎头','外包','派遣'}

CITY_CODES = {
    '全国': '100010000', '北京': '101010100', '上海': '101020100',
    '杭州': '101210100', '深圳': '101280600', '广州': '101280100',
    '成都': '101270100', '武汉': '101200100', '南京': '101190100',
}

SCALE_CODES = {'20-99人': '302', '100-499人': '303'}


def run_mcp(tool, args_dict):
    out = subprocess.check_output(
        ['python3', str(MCP), tool, json.dumps(args_dict, ensure_ascii=False)],
        text=True, stderr=subprocess.DEVNULL
    )
    return out.strip()


def parse_prefs(path: Path):
    txt = path.read_text(encoding='utf-8')
    def find(pattern, default):
        m = re.search(pattern, txt)
        return m.group(1).strip() if m else default

    queries   = [x.strip() for x in find(r'搜索词[^:：]*[:：]\s*(.+)', 'agent').split(',') if x.strip()]
    cities    = [x.strip() for x in find(r'目标城市[^:：]*[:：]\s*(.+)', '全国').split('/') if x.strip()]
    min_sal   = int(find(r'日薪下限[^:：]*[:：]\s*(\d+)', '150'))
    scales    = [x.strip() for x in find(r'公司规模[^:：]*[:：]\s*(.+)', '20-99人').split('/') if x.strip()]
    extra_exc = [x.strip() for x in find(r'排除关键词[^:：]*[:：]\s*(.+)', '').split(',') if x.strip()]

    city_codes  = [CITY_CODES.get(c, '100010000') for c in cities]
    scale_codes = [SCALE_CODES[s] for s in scales if s in SCALE_CODES] or ['302']
    return queries, city_codes, min_sal, scale_codes, extra_exc


def is_excluded(job: dict, min_sal: int, extra_exc: list) -> bool:
    name    = (job.get('jobName') or '').lower()
    company = (job.get('brandName') or '').lower()
    sal_str = job.get('salaryDesc') or ''

    if any(k in company for k in BIG_TECH):
        return True
    if any(k in name for k in NON_TECH):
        return True
    if any(k.lower() in name or k.lower() in company for k in extra_exc):
        return True

    # 薪资过滤：取下限
    m = re.search(r'(\d+)', sal_str)
    if m and int(m.group(1)) < min_sal:
        return True

    return False


def extract_json(s: str):
    m = re.search(r'\{.*\}', s, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prefs', required=True)
    ap.add_argument('--yaml',  required=True)
    args = ap.parse_args()

    prefs_path = Path(args.prefs)
    yaml_path  = Path(args.yaml)

    data  = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or []
    items = data if isinstance(data, list) else []
    by_url = {it.get('url'): it for it in items if isinstance(it, dict) and it.get('url')}

    queries, city_codes, min_sal, scale_codes, extra_exc = parse_prefs(prefs_path)

    # 激活 cookie
    run_mcp('chrome_navigate', {'url': 'https://www.zhipin.com/web/geek/job?query=agent&city=100010000'})

    added = 0
    for q in queries:
        for city in city_codes:
            for scale in scale_codes:
                js = (
                    f"(async()=>{{const r=await fetch(`/wapi/zpgeek/search/joblist.json"
                    f"?query=${{encodeURIComponent('{q}')}}&city={city}&page=1&pageSize=30"
                    f"&jobType=4&scale={scale}`,{{credentials:'include'}});"
                    f"return JSON.stringify(await r.json());}})();"
                )
                raw  = run_mcp('chrome_javascript', {'code': js})
                data_j = extract_json(raw)
                jobs = ((data_j.get('zpData') or {}).get('jobList') or []) if isinstance(data_j, dict) else []

                for job in jobs:
                    eid = job.get('encryptJobId', '')
                    if not eid:
                        continue
                    url = f'https://www.zhipin.com/job_detail/{eid}.html'
                    if url in by_url:
                        continue
                    if is_excluded(job, min_sal, extra_exc):
                        continue

                    rec = {
                        'collected_at':   str(date.today()),
                        'company':        (job.get('brandName') or '').strip(),
                        'title':          job.get('jobName', ''),
                        'salary':         job.get('salaryDesc', ''),
                        'location':       job.get('cityName', ''),
                        'company_size':   job.get('brandScaleName', ''),
                        'funding_stage':  job.get('brandIndustry', ''),
                        'job_type':       '实习',
                        'source':         'boss直聘',
                        'url':            url,
                        'status':         'pending',
                        # 以下字段留空，由后续节点填写
                        'jd_full':        '',
                        'jd_summary':     '',
                        'tags':           [],
                        'jd_quality':     '',
                        'notion_page_id': '',
                    }
                    items.append(rec)
                    by_url[url] = rec
                    added += 1

    yaml_path.write_text(yaml.dump(items, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'added={added} total={len(items)}')


if __name__ == '__main__':
    main()
