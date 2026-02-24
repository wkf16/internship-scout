#!/usr/bin/env python3
import argparse, json, re, subprocess
from pathlib import Path
import yaml

ROOT = Path('/Users/okonfu/.openclaw/workspace')
MCP = ROOT / 'skills/internship-scout/scripts/mcp_call.py'


def run_mcp(tool, args):
    out = subprocess.check_output(['python3', str(MCP), tool, json.dumps(args, ensure_ascii=False)], text=True)
    return out.strip()


def parse_prefs(path: Path):
    txt = path.read_text(encoding='utf-8')
    q = re.search(r'搜索词（query 参数）:\s*(.+)', txt)
    c = re.search(r'BOSS city codes:\s*(.+)', txt)
    queries = [x.strip() for x in (q.group(1) if q else 'agent').split(',') if x.strip()]
    cities = [x.strip() for x in (c.group(1) if c else '100010000').split(',') if x.strip()]
    return queries, cities


def extract_json(s: str):
    s = s.strip()
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
    ap.add_argument('--yaml', required=True)
    args = ap.parse_args()

    prefs = Path(args.prefs)
    ypath = Path(args.yaml)
    data = yaml.safe_load(ypath.read_text(encoding='utf-8')) or {'internships': []}
    items = data.get('internships', [])
    by_url = {it.get('url'): it for it in items if isinstance(it, dict) and it.get('url')}

    queries, cities = parse_prefs(prefs)

    # 激活 cookie
    run_mcp('chrome_navigate', {'url': 'https://www.zhipin.com/web/geek/job?query=agent&city=100010000'})

    added = 0
    for q in queries:
        for city in cities:
            js = f"""
(async () => {{
  const u = `/wapi/zpgeek/search/joblist.json?query=${{encodeURIComponent('{q}')}}&city={city}&page=1&pageSize=30&jobType=4&scale=302`;
  const r = await fetch(u, {{credentials:'include'}});
  const j = await r.json();
  return JSON.stringify(j);
}})();
""".strip()
            raw = run_mcp('chrome_javascript', {'code': js})
            j = extract_json(raw)
            jobs = (((j.get('zpData') or {}).get('jobList')) or []) if isinstance(j, dict) else []
            for job in jobs:
                url = 'https://www.zhipin.com/job_detail/' + str(job.get('encryptJobId','')) + '.html'
                if not job.get('encryptJobId'):
                    continue
                if url in by_url:
                    continue
                rec = {
                    'title': job.get('jobName',''),
                    'company': ((job.get('brandName') or '').strip()),
                    'salary': job.get('salaryDesc',''),
                    'location': job.get('cityName',''),
                    'experience': job.get('jobExperience',''),
                    'education': job.get('jobDegree',''),
                    'company_size': job.get('brandScaleName',''),
                    'funding_stage': job.get('brandIndustry',''),
                    'tags': [],
                    'jd_summary': '',
                    'jd_full': '',
                    'url': url,
                    'source': 'boss直聘',
                    'status': 'pending',
                    'jd_quality': '',
                    'collected_at': '',
                    'notion_page_id': ''
                }
                items.append(rec)
                by_url[url] = rec
                added += 1

    data['internships'] = items
    ypath.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'added={added} total={len(items)}')


if __name__ == '__main__':
    main()
