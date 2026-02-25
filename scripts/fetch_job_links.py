#!/usr/bin/env python3
"""
fetch_job_links.py — 通过 BOSS直聘内部 API 抓取职位列表，写入 internships.yaml。
只写结构字段（title/company/salary/url 等），不写 jd_full/jd_summary（留给后续节点）。
"""
import argparse, json, re, subprocess, time, random
from datetime import date
from pathlib import Path
import yaml

ROOT = Path.home() / '.openclaw/workspace'
MCP  = ROOT / 'skills/internship-scout/scripts/mcp_call.py'

# 默认兜底值，优先从 internship-prefs.md 读取
DEFAULT_BIG_TECH = {
    '字节', '抖音', 'tiktok', '阿里', '淘宝', '天猫', '腾讯', '百度', '美团', '京东',
    '华为', '小米', '网易', 'bilibili', '哔哩', '滴滴', '快手', '拼多多', '蚂蚁',
    '微软', '谷歌', 'google', 'microsoft', 'apple', '苹果',
}

DEFAULT_NON_TECH = {
    '销售', '运营', '市场', '客服', '行政', '财务', '人事', 'hr', '猎头', '外包', '派遣',
}

CITY_CODES = {
    '全国': '100010000', '北京': '101010100', '上海': '101020100',
    '杭州': '101210100', '深圳': '101280600', '广州': '101280100',
    '成都': '101270100', '武汉': '101200100', '南京': '101190100',
}

SCALE_CODES = {'20-99人': '302', '100-499人': '303'}


def run_mcp(tool, args_dict):
    out = subprocess.check_output(
        ['python3', str(MCP), tool, json.dumps(args_dict, ensure_ascii=False)],
        text=True, stderr=subprocess.DEVNULL,
    )
    return out.strip()


def parse_list(txt: str, pattern: str, default: str) -> list[str]:
    m = re.search(pattern, txt)
    raw = m.group(1).strip() if m else default
    return [x.strip() for x in re.split(r'[,，、]', raw) if x.strip()]


def parse_prefs(path: Path):
    txt = path.read_text(encoding='utf-8')

    def find(pattern, default=''):
        m = re.search(pattern, txt)
        return m.group(1).strip() if m else default

    queries   = parse_list(txt, r'搜索词[^:：]*[:：]\s*(.+)', 'agent')
    cities    = parse_list(txt, r'目标城市[^:：]*[:：]\s*(.+)', '全国')
    min_sal   = int(find(r'日薪下限[^:：]*[:：]\s*(\d+)', '150'))
    scales    = parse_list(txt, r'公司规模[^:：]*[:：]\s*(.+)', '20-99人')
    extra_exc = parse_list(txt, r'排除关键词[^:：]*[:：]\s*(.+)', '')

    # 大厂排除：从 prefs 读取
    # - 留空 → 使用内置默认列表
    # - 填具体公司名 → 只排除填写的公司
    # - 填「无」或「不限」→ 不排除任何公司
    big_tech_raw = parse_list(txt, r'大厂排除[^:：]*[:：]\s*(.+)', '')
    if not big_tech_raw:
        big_tech = DEFAULT_BIG_TECH
    elif set(big_tech_raw) & {'无', '不限'}:
        big_tech = set()
    else:
        big_tech = {x.lower() for x in big_tech_raw}

    # 非技术岗排除：从 prefs 读取，同上逻辑
    non_tech_raw = parse_list(txt, r'非技术岗排除[^:：]*[:：]\s*(.+)', '')
    if not non_tech_raw:
        non_tech = DEFAULT_NON_TECH
    elif set(non_tech_raw) & {'无', '不限'}:
        non_tech = set()
    else:
        non_tech = {x.lower() for x in non_tech_raw}

    city_codes  = [CITY_CODES.get(c, '100010000') for c in cities]
    scale_codes = [SCALE_CODES[s] for s in scales if s in SCALE_CODES] or ['302']
    return queries, city_codes, min_sal, scale_codes, extra_exc, big_tech, non_tech


def is_excluded(job: dict, min_sal: int, extra_exc: list,
                big_tech: set, non_tech: set) -> bool:
    name    = (job.get('jobName') or '').lower()
    company = (job.get('brandName') or '').lower()
    sal_str = job.get('salaryDesc') or ''

    if any(k in company for k in big_tech):
        return True
    if any(k in name for k in non_tech):
        return True
    if any(k.lower() in name or k.lower() in company for k in extra_exc):
        return True

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

    queries, city_codes, min_sal, scale_codes, extra_exc, big_tech, non_tech = parse_prefs(prefs_path)

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
                raw    = run_mcp('chrome_javascript', {'code': js})
                data_j = extract_json(raw)
                jobs   = ((data_j.get('zpData') or {}).get('jobList') or []) if isinstance(data_j, dict) else []

                for job in jobs:
                    eid = job.get('encryptJobId', '')
                    if not eid:
                        continue
                    url = f'https://www.zhipin.com/job_detail/{eid}.html'
                    if url in by_url:
                        continue
                    if is_excluded(job, min_sal, extra_exc, big_tech, non_tech):
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
                        'jd_full':        '',
                        'jd_summary':     '',
                        'tags':           [],
                        'jd_quality':     '',
                        'notion_page_id': '',
                    }
                    items.append(rec)
                    by_url[url] = rec
                    added += 1

                # 每次搜索之间随机延迟 1-3s，避免触发风控
                delay = random.uniform(1.0, 3.0)
                print(f'  sleeping {delay:.1f}s...')
                time.sleep(delay)

    yaml_path.write_text(yaml.dump(items, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'added={added} total={len(items)}')


if __name__ == '__main__':
    main()
