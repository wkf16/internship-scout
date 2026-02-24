---
name: internship-scout
description: Use chrome-mcp to search for internship/job positions on BOSS直聘, extract full JD, filter by company size/quality, tag, and record results into a YAML file. Optionally sync to a Notion database. Use when: (1) user wants to search for internship or job positions, (2) user wants to collect and track job listings, (3) user wants to save JD info to YAML. Requires chrome-mcp running at 127.0.0.1:12306.
---

# internship-scout

Search job listings via chrome-mcp, extract JD details, filter, tag, and persist to YAML. Optionally sync to Notion.

## Data File

All results are stored at: `~/.openclaw/workspace/internships.yaml`

See `references/schema.md` for the full YAML schema.

---

## Session Start — Ask User

At the beginning of every internship-scout session, ask:

> 是否开启 Notion 同步？开启后每次更新 YAML 都会自动同步到 Notion 数据库。

Then list accessible Notion pages by calling:

```bash
curl -s "https://api.notion.com/v1/search" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{"filter":{"value":"page","property":"object"},"page_size":10}' \
  | python3 -c "import json,sys; pages=json.load(sys.stdin).get('results',[]); [print(p['id'], p.get('properties',{}).get('title',{}).get('title',[{}])[0].get('plain_text','(untitled)')) for p in pages]"
```

Present the list to the user and ask which page to use as the parent for the Notion database.

If the user declines, skip all Notion steps below.

---

## Workflow

### 1. Open a chrome-mcp session

Every chrome-mcp call requires a fresh session-id. Use the helper script:

```bash
python3 ~/.openclaw/workspace/skills/internship-scout/scripts/mcp_call.py <tool_name> '<json_args>'
```

### 2. Search via BOSS直聘 internal API

Navigate to a BOSS直聘 job search page first (to get cookies), then fetch:

```
GET /wapi/zpgeek/search/joblist.json
  ?query=<keyword>
  &city=<city_code>
  &page=1
  &pageSize=30
  &jobType=4          # 4 = 实习
  &scale=<scale_code> # 302=20-99人, 303=100-499人
```

Use `chrome_javascript` on the BOSS tab with `fetch(..., {credentials:'include'})`.

Common city codes: 全国=100010000, 北京=101010100, 上海=101020100, 杭州=101210100, 深圳=101280600

Scale priority: fetch `scale=302` (20-99人) first, then `scale=303` (100-499人) if more results needed.

### 3. Filter results

Exclude:
- **大厂**: 字节/阿里/腾讯/百度/美团/京东/华为/小米/网易/bilibili/滴滴/快手/拼多多/蚂蚁/微软/谷歌
- **非技术岗**: 销售/运营/市场/客服/行政/财务/人事/HR
- **薪资过低**: 日薪 <150元/天 or 月薪 <3K（跳过明显异常低薪）
- **猎头/外包/派遣**

### 4. Fetch full JD

For each filtered position, navigate to `https://www.zhipin.com/job_detail/<id>.html` and extract:

```javascript
document.querySelector(".job-sec-text")?.innerText  // JD 正文
```

Delete entries where JD is empty after fetching.

### 5. Generate tags & jd_quality

**Tags** — extract from JD text, pick from:
`Python, LangChain, LangGraph, RAG, LLM, Agent, MCP, FastAPI, React, TypeScript, Rust, Go, Docker, K8s, 向量数据库, 微调, LoRA, Dify, Coze, OpenAI, Claude, Qwen, 多模态, 强化学习, RLHF, 自动驾驶`

**jd_quality**:
- `good` — JD ≥200字 且技术关键词 ≥3 个
- `unclear` — JD 偏短或技术描述模糊
- `skip` — 非技术岗 / 外包 / 学历门槛过高（硕士/博士/985/211 required）

### 6. Write to YAML

Load existing `internships.yaml`, deduplicate by `company+title`, append new entries, write back.

Fields to populate: see `references/schema.md`.

---

## Notion Sync (Optional)

> Only run if user opted in at session start.

### Constraint: sync after every YAML write

Whenever `internships.yaml` is updated (new entries added, status changed, jd_quality updated), immediately sync the changed entries to Notion.

### Setup — find or create database

Check if a Notion database already exists under the chosen parent page:

```bash
curl -s "https://api.notion.com/v1/search" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{"query":"实习岗位","filter":{"value":"database","property":"object"}}' \
  | python3 -c "import json,sys; dbs=json.load(sys.stdin).get('results',[]); [print(d['id'], d['title'][0]['plain_text']) for d in dbs if d.get('title')]"
```

If no database exists, create one under the parent page with these properties:

| Property | Type |
|---|---|
| 岗位名称 | title |
| 公司 | rich_text |
| 薪资 | rich_text |
| 城市 | rich_text |
| 规模 | select |
| 融资阶段 | select |
| JD质量 | select (good/unclear/skip) |
| 状态 | select (pending/applied/interviewing/offered/rejected/ghosted) |
| 技术标签 | multi_select |
| 来源 | rich_text |
| 链接 | url |
| 收录日期 | date |
| JD摘要 | rich_text |

### Upsert logic

For each entry to sync:
1. If `notion_page_id` is set → PATCH the existing page properties
2. If empty → POST a new page, save returned `id` back to `notion_page_id` in YAML

```bash
# Create page
curl -s -X POST "https://api.notion.com/v1/pages" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '<properties payload>'

# Update page
curl -s -X PATCH "https://api.notion.com/v1/pages/<notion_page_id>" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '<properties payload>'
```

After creating/updating, write `notion_page_id` back to the YAML entry.

---

## Status Values

`pending` → `applied` → `interviewing` → `offered` / `rejected` / `ghosted`

Update status manually or via the internship-tracker skill (when available).
