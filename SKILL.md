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

## Step 1 — Preferences & Data File

**检查偏好文件：**
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
10. 大厂排除列表（留空使用默认：字节/阿里/腾讯/百度等；填「不限」则不过滤大厂）
11. 非技术岗排除列表（留空使用默认：销售/运营/HR等；填「不限」则不过滤）

**Exists** → load silently. Re-trigger only if user says "更新偏好" or "重置偏好".

**检查数据文件：**
```bash
test -f ~/.openclaw/workspace/internships.yaml && echo exists || echo missing
```

- **Missing** → 将从空文件开始，直接进入 Step 2。
- **Exists** → 询问用户：**在现有数据基础上追加**（默认），还是**覆盖重建**？
  - 追加：`fetch_job_links.py` 会自动跳过已收录 URL，无需额外操作。
  - 覆盖：备份后清空，`cp internships.yaml internships.yaml.bak && echo '[]' > internships.yaml`。

---

## Step 2 — Fetch Job Links

```bash
python3 skills/internship-scout/scripts/fetch_job_links.py \
  --prefs internship-prefs.md \
  --yaml internships.yaml
```

从 `internship-prefs.md` 读取搜索词、城市、规模等偏好，调用 BOSS直聘内部 API 抓取职位列表。只写结构字段（title/company/salary/location/url 等），不含 JD 正文。

过滤规则（均可在 prefs 中配置）：
- **大厂排除**：留空 → 使用内置默认列表；填公司名 → 只排除指定公司；填「无」或「不限」→ 关闭大厂过滤
- **非技术岗排除**：同上逻辑，留空使用默认（销售/运营/HR等）
- **低薪过滤**：日薪低于 `日薪下限` 的条目自动跳过
- **去重**：已收录 URL 自动跳过

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

## Step 4 — Summarize JDs (single subagent)

```bash
# 查看待处理条目
python3 skills/internship-scout/scripts/summarize_jds.py --list-pending

# 打印完整 prompt（调试用）
python3 skills/internship-scout/scripts/summarize_jds.py --dry-run

# 写回 subagent 结果
python3 skills/internship-scout/scripts/summarize_jds.py --write-result '<json>'
```

### 工作流（由主会话 orchestrate）

```
1. --list-pending      → 确认待处理数量
2. --dry-run           → 拿到完整 prompt
3. sessions_spawn(cleanup=keep, mode=run)  → subagent 纯文本推理
4. --write-result '<json>'                 → 写回 YAML
```

所有 pending 条目一次性放入单个 subagent，无需分批。

### subagent 输入/输出

- 输入：system prompt + 所有 pending JD 原文（纯文本）
- 输出：严格 JSON 数组
  ```json
  [{
    "id": 0,
    "clarity": 3,
    "tech_stack": 3,
    "role_signal": 2,
    "jd_score": 8,
    "jd_quality": "A",
    "jd_summary": "30-50字摘要",
    "tags": ["Python", "LLM"]
  }]
  ```
- 不使用任何 tools，不联网

### jd_quality 评级（三维评分）

| 维度 | 1分 | 2分 | 3分 |
|------|-----|-----|-----|
| clarity | 全是套话 | 有方向但笼统 | 职责明确可执行 |
| tech_stack | 只有泛称/无要求 | 1-2个具体技术名 | ≥3个具体技术名 |
| role_signal | 非技术/外包/销售 | 技术+产品混合 | 明确技术/算法/研究岗 |

总分（clarity+tech_stack+role_signal）映射：8-9→A，6-7→B，4-5→C，3→D，含外包/销售/无技术要求→F

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
