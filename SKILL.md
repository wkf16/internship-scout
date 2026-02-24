---
name: internship-scout
description: Search BOSS直聘 for non-big-tech internship positions via chrome-mcp, filter by company size/salary/JD quality, extract full JD, auto-tag, and persist to YAML. Optionally sync to a Notion database. Use when the user wants to find, collect, or track internship listings.
---

# internship-scout

## File Layout

```
skills/internship-scout/
├── SKILL.md
├── references/
│   ├── schema.md            # YAML field definitions
│   └── prefs-template.md    # Template for internship-prefs.md
└── scripts/
    ├── mcp_call.py          # chrome-mcp helper (session init + single call)
    └── notion_sync.py       # Notion upsert script
```

**Data files** (workspace root):
- `internships.yaml` — all collected positions
- `internship-prefs.md` — user job preferences (created on first run)

---

## Session Start (run every time)

### Step 1 — Load preferences

```bash
test -f ~/.openclaw/workspace/internship-prefs.md && echo exists || echo missing
```

**Missing** → ask the user these questions, then write `internship-prefs.md` from `references/prefs-template.md`:

1. 目标城市（上海 / 北京 / 深圳 / 杭州 / 全国 / 远程）
2. 期望日薪下限（元/天，如 200）
3. 公司规模偏好（20-99人优先 / 100-499人 / 都可以）
4. 融资阶段偏好（天使轮/A轮/B轮以上/不限）
5. 岗位方向关键词（如：agent, LLM, 大模型）
6. 技术栈偏好（如：Python, LangChain, RAG）
7. 额外排除关键词（追加到过滤规则）
8. 学历情况（本科在读 / 硕士在读）
9. 可实习时长

**Exists** → load silently. Re-trigger only if user says "更新偏好" or "重置偏好".

### Step 2 — Notion sync

Ask: **是否开启 Notion 同步？**

If yes → run `notion_sync.py` setup (see §Notion Sync below).
If no → skip all Notion steps.

---

## Search Workflow

### 1. Navigate to BOSS直聘

Open `https://www.zhipin.com/web/geek/job?query=agent&city=100010000` in Chrome to ensure cookies are active.

### 2. Fetch via internal API

Use `chrome_javascript` on the BOSS tab with `fetch(..., {credentials:'include'})`:

```
GET /wapi/zpgeek/search/joblist.json
  ?query=<keyword>        # from prefs: job_keywords
  &city=<city_code>       # from prefs: target_cities
  &page=1&pageSize=30
  &jobType=4              # 实习
  &scale=302              # 20-99人 first; repeat with scale=303 for 100-499人
```

City codes: 全国=100010000 北京=101010100 上海=101020100 杭州=101210100 深圳=101280600

### 3. Filter

Exclude:
- **大厂**: 字节/阿里/腾讯/百度/美团/京东/华为/小米/网易/bilibili/滴滴/快手/拼多多/蚂蚁/微软/谷歌
- **非技术岗**: 销售/运营/市场/客服/行政/财务/人事/HR
- **薪资过低**: below `min_daily_salary` from prefs (default 150元/天)
- **猎头/外包/派遣**
- Any extra keywords from prefs `exclude_keywords`

### 4. Fetch full JD

Navigate to `https://www.zhipin.com/job_detail/<id>.html`, extract:

```javascript
document.querySelector(".job-sec-text")?.innerText
```

Delete entries where JD is empty after fetching.

### 5. Tag & score

**tags** — match against:
`Python LangChain LangGraph RAG LLM Agent MCP FastAPI React TypeScript Rust Go Docker K8s 向量数据库 微调 LoRA Dify Coze OpenAI Claude Qwen 多模态 强化学习 RLHF 自动驾驶`

**jd_quality**:
- `good` — JD ≥200字 and ≥3 tech keywords
- `unclear` — short or vague
- `skip` — non-tech / outsourcing / education barrier (硕士/博士 required; 985/211 only if user's education is below that)

### 6. Write YAML

**Before appending**, run dedup check to avoid overwriting with stale data:

```bash
python3 ~/.openclaw/workspace/skills/internship-scout/scripts/dedup_check.py \
  --company "<company>" \
  --title   "<title>" \
  --salary  "<salary>" \
  --location "<location>" \
  --jd      "<jd_full 前300字>" \
  --threshold 0.25
```

- Exit 0 → 找到疑似重复，输出 `index=N`，**覆盖更新**该条目而非新增
- Exit 1 → 无重复，正常 append
- `--threshold` 默认 0.25（距离率），调小更严格，调大更宽松

字段权重：公司名 30%、岗位名 30%、JD 15%、薪资 15%、地点 10%。

**After every YAML write** → if Notion sync is enabled, immediately run:

```bash
python3 ~/.openclaw/workspace/skills/internship-scout/scripts/notion_sync.py --mode new
```

---

## Notion Sync

### Script: `scripts/notion_sync.py`

Requires: `pip install aiohttp`
Concurrency: 3 parallel requests (matches Notion's ~3 req/s limit). Handles 429 automatically via `Retry-After` header.

```
Inputs
  --yaml     Path to internships.yaml (default: workspace/internships.yaml)
  --db-id    Notion DB ID or share URL. Falls back to notion_db_id in internship-prefs.md.
             If missing: prompts user (UUID / share URL / 'new' to create)
  --mode     new    — POST entries where notion_page_id is empty  [default]
             update — PATCH entries that already have notion_page_id (full overwrite)
             all    — new + update
             reset  — archive all DB pages → clear YAML notion_page_ids → POST all fresh
  --filter   Only sync entries whose company name contains this string
  --dry-run  Preview without API calls

Outputs
  stdout:    ✅ CompanyName | created/updated   or   ❌ CompanyName | reason
  Side-effect: writes notion_page_id back to YAML for new pages
  Exit code: 0 = all ok, 1 = any failure
```

### DB ID resolution order

1. `--db-id` argument (UUID or Notion share URL — ID extracted automatically)
2. `notion_db_id` field in `internship-prefs.md`
3. Prompt user → accept UUID / share URL / `new`
   - `new` → creates database under ヤチヨ 元Agent, saves ID to prefs

### reset mode — step by step

```
Step 1: query all page IDs from DB
Step 2: async archive all pages (concurrency=3, handles 429)
Step 3: clear all notion_page_id fields in YAML
Step 4: async POST all entries fresh (concurrency=3)
Step 5: write returned page IDs back to YAML
```

Use `reset` when: duplicate entries exist, DB is out of sync, or after manual DB edits.

### Field mapping (full overwrite on update/reset)

| YAML field | Notion property | Type |
|---|---|---|
| `company` | `Name` | title |
| `salary` | `薪资` | rich_text |
| `location` | `城市` | rich_text |
| `company_size` | `规模` | select |
| `funding_stage` | `融资阶段` | select |
| `jd_quality` | `JD质量` | select |
| `status` | `状态` | select |
| `tags` | `技术标签` | multi_select |
| `url` | `链接` | url |
| `collected_at` | `收录日期` | date |
| `jd_summary` | `JD摘要` | rich_text |

### Trigger rules

| Event | Command |
|---|---|
| New entries added | `--mode new` |
| Status / jd_quality changed | `--mode update` |
| Full re-sync | `--mode all` |
| DB has duplicates / out of sync | `--mode reset` |
| Single company | `--filter "深度赋智" --mode all` |

---

## Status Values

`pending` → `applied` → `interviewing` → `offered` / `rejected` / `ghosted`
