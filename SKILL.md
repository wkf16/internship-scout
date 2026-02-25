---
name: internship-scout
description: Search BOSS直聘 for non-big-tech internship positions via chrome-osascript, extract full JD (DOM raw), summarize and tag via subagent, and persist to YAML. Optionally sync to Notion. Use when the user wants to find, collect, analyze, or track internship listings.
---

# internship-scout

## File Layout

```
skills/internship-scout/
├── SKILL.md
├── references/
│   ├── schema.md                # YAML field definitions
│   └── prefs-template.md        # Template for internship-prefs.md
└── scripts/
    ├── fetch_job_links.py       # Step 1: API 抓取职位列表
    ├── fetch_jd_dom.py          # Step 2: osascript DOM 抓 JD 原文
    ├── summarize_jds.py         # Step 3: subagent 批量生成 summary/tags/quality
    ├── dedup_check.py           # 去重检查
    └── notion_sync.py           # Notion upsert
```

**Data files** (workspace root):
- `internships.yaml` — all collected positions
- `internship-prefs.md` — user job preferences (created on first run)

---

## Workflow Overview

```
fetch_job_links.py   →   fetch_jd_dom.py   →   summarize_jds.py   →   notion_sync.py
  (抓列表+结构字段)        (DOM抓JD原文)         (summary/tags/ABCD)      (Notion同步)
```

---

## Step 1 — Preferences

```bash
test -f ~/.openclaw/workspace/internship-prefs.md && echo exists || echo missing
```

**Missing** → ask the user:
1. 目标城市（上海 / 北京 / 深圳 / 杭州 / 全国 / 远程）
2. 期望日薪下限（元/天）
3. 公司规模偏好（20-99人 / 100-499人 / 都可以）
4. 融资阶段偏好（天使轮/A轮/B轮以上/不限）
5. 岗位方向关键词（如：agent, LLM, 大模型）
6. 技术栈偏好（如：Python, LangChain, RAG）
7. 额外排除关键词
8. 学历情况（本科在读 / 硕士在读）
9. 可实习时长

**Exists** → load silently. Re-trigger only if user says "更新偏好" or "重置偏好".

---

## Step 2 — Fetch Job Links

```bash
python3 skills/internship-scout/scripts/fetch_job_links.py \
  --keyword "AI Agent" --city 上海 --limit 20 \
  --yaml internships.yaml
```

只写结构字段（title/company/salary/location/url 等），不含 JD 正文。

---

## Step 3 — Fetch JD DOM

```bash
python3 skills/internship-scout/scripts/fetch_jd_dom.py \
  --yaml internships.yaml \
  --limit 5 \
  --min-delay 2.0 --max-delay 5.0
```

- 用 osascript 打开 Chrome，DOM 抓取 `.job-sec-text` 节点原文（带换行）
- 只处理 `jd_full` 为空且无 `fetch_error` 的条目
- 空 JD → 写 `fetch_error: empty_job_sec_text`，exit 1
- `--refetch` 强制重抓已有 jd_full 的条目

**前提**：Chrome 已开启 `View > Developer > Allow JavaScript from Apple Events`

---

## Step 4 — Summarize JDs (subagent batch)

```bash
# 查看待处理条目
python3 skills/internship-scout/scripts/summarize_jds.py --list-pending

# 获取某批 prompt（供主会话 spawn subagent）
python3 skills/internship-scout/scripts/summarize_jds.py --dry-run --batch 0

# 写回 subagent 结果
python3 skills/internship-scout/scripts/summarize_jds.py \
  --write-result '<json>' --batch 0
```

### 工作流（由主会话 orchestrate）

```
1. --list-pending          → 确认待处理数量和批次
2. --dry-run --batch N     → 拿到 prompt
3. sessions_spawn(cleanup=delete, mode=run)  → subagent 纯文本推理
4. --write-result '<json>' --batch N         → 写回 YAML
5. 重复 2-4 直到所有批次完成
```

### subagent 输入/输出

- 输入：system prompt + 最多 5 条 JD 原文（纯文本，不传 YAML）
- 输出：严格 JSON 数组
  ```json
  [{"id": 0, "jd_summary": "30-50字摘要", "tags": ["Python", "LLM"], "jd_quality": "A"}]
  ```
- 不使用任何 tools，不联网

### jd_quality 评级

| 级别 | 标准 |
|------|------|
| A | JD≥200字，技术关键词≥5个，职责清晰，有明确技术栈 |
| B | JD≥100字，技术关键词≥3个，职责基本清晰 |
| C | JD偏短或技术描述模糊，关键词<3个 |
| D | 非技术岗/外包/纯销售/学历门槛过高/内容严重不足 |

---

## Step 5 — Notion Sync

```bash
python3 skills/internship-scout/scripts/notion_sync.py \
  --yaml internships.yaml \
  --mode new        # new / update / all / reset
```

| 场景 | 命令 |
|------|------|
| 新增条目 | `--mode new` |
| 更新已有 | `--mode update` |
| 全量同步 | `--mode all` |
| 重置去重 | `--mode reset` |
| 单家公司 | `--filter "公司名" --mode all` |

---

## Status Values

`pending` → `applied` → `interviewing` → `offered` / `rejected` / `ghosted`
