---
name: internship-scout
description: Search BOSS直聘 for non-big-tech internship positions via chrome-mcp, filter by company size/salary/JD quality, extract full JD, auto-tag, and persist to YAML. Optionally sync to a Notion database. Use when the user wants to find, collect, or track internship listings.
---

# internship-scout

## File Layout

```
skills/internship-scout/
├── SKILL.md
├── pipelines/
│   └── scout-sync.lobster       # 主 pipeline（7步，含 agentTurn 提取节点）
├── references/
│   ├── schema.md                # YAML field definitions
│   └── prefs-template.md        # Template for internship-prefs.md
└── scripts/
    ├── mcp_call.py              # chrome-mcp helper
    ├── fetch_job_links.py       # Step 2: API 抓取职位列表
    ├── fetch_jd_dom.py          # Step 3: DOM 抓取 JD 原文（硬阻断）
    ├── dedup_check.py           # 去重检查
    └── notion_sync.py           # Notion upsert
```

**临时缓存目录**（pipeline 运行时自动创建，可随时删除）：
- `jd_raw_cache/` - 每条 JD 的原始 DOM 文本（`0001.txt` 等）
- `jd_extracted/` - agentTurn 提取结果（`0001.json` 等）

**Data files** (workspace root):
- `internships.yaml` - all collected positions
- `internship-prefs.md` - user job preferences (created on first run)

---

## Session Start (run every time)

### Step 1 - Load preferences

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

### Step 2 - Notion sync

Ask: **是否开启 Notion 同步？**

If yes → run `notion_sync.py` setup (see §Notion Sync below).
If no → skip all Notion steps.

---

## 初始化（每次跑批前）

1. 确认 Chrome MCP 已连接（`tabs > 0`），否则停止。
2. 打开并登录 BOSS 直聘账号，确保 cookie 生效。
3. 读取 `internship-prefs.md`（关键词、城市、过滤规则、notion_db_id）。
4. 若 Notion 数据库缺字段，先确保存在：`JD摘要`、`JD原文`。
5. 本轮设置小批次（建议每批 3-5 条），每批落盘后再继续下一批。

## Search Workflow

**推荐方式：直接运行 lobster pipeline**

```bash
openclaw lobster run skills/internship-scout/pipelines/scout-sync.lobster \
  --batch_size 5 --sync_mode new
```

Pipeline 共 7 步，关键设计：

| 步骤 | 执行方式 | 说明 |
|---|---|---|
| fetch-links | shell | API 抓列表，只写结构字段，不含 JD 正文 |
| fetch-jd-dom | shell + **failFast** | DOM 抓 JD 原文；空 JD → exit 1 硬停 |
| extract-prep | shell | 把每条 jd_full 写成独立 txt |
| **extract-fields** | **agentTurn** | 每次只看一条 JD，输出 jd_summary + tags |
| merge-extracted | shell + 幻觉检测 | 关键词回查原文，校验后写回 YAML |
| rate-jds | shell | jd-rater 批量评分 |
| notion-sync | shell | Notion 同步 |

### agentTurn 上下文隔离规则

`extract-fields` 节点的 systemPrompt 强制约束：
- 输入：单条 `.txt` 原始文本（不传 YAML，不传历史）
- 输出：只允许 `{"jd_summary": "...", "tags": [...]}`
- tags 只能从预定义候选列表中选，不得自造
- summary 中的词必须在原文中出现，否则 merge 步骤拒绝写入

### 手动单条更新

```bash
# 只更新某一条（已有 jd_full，重新提取 summary/tags）
# 1. 删除该条的 jd_summary 和 tags
# 2. 从 fetch-jd-dom 步骤开始重跑
openclaw lobster run ... --start-from fetch-jd-dom
```

---

## JD Rating

**Never rate inline during collection.** Always use jd-rater after JDs are collected.

```bash
# Re-rate all entries
python3 ~/.openclaw/workspace/skills/jd-rater/scripts/rate_jds.py

# Dry-run first to preview changes
python3 ~/.openclaw/workspace/skills/jd-rater/scripts/rate_jds.py --dry-run
```

See `skills/jd-rater/SKILL.md` for full rubric and scoring details.

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
  --mode     new    - POST entries where notion_page_id is empty  [default]
             update - PATCH entries that already have notion_page_id (full overwrite)
             all    - new + update
             reset  - archive all DB pages → clear YAML notion_page_ids → POST all fresh
  --filter   Only sync entries whose company name contains this string
  --dry-run  Preview without API calls

Outputs
  stdout:    ✅ CompanyName | created/updated   or   ❌ CompanyName | reason
  Side-effect: writes notion_page_id back to YAML for new pages
  Exit code: 0 = all ok, 1 = any failure
```

### DB ID resolution order

1. `--db-id` argument (UUID or Notion share URL - ID extracted automatically)
2. `notion_db_id` field in `internship-prefs.md`
3. Prompt user → accept UUID / share URL / `new`
   - `new` → creates database under ヤチヨ 元Agent, saves ID to prefs

### reset mode - step by step

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
| `jd_summary` | `JD摘要`（30-50字） | rich_text |
| `jd_full` | `JD原文`（完整文本） | rich_text |

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
