name: internship-scout-sync
description: |
  BOSS直聘实习岗位抓取 pipeline。
  DOM 抓取由 shell 脚本完成；字段提取由 agentTurn 完成（上下文严格隔离，每次只处理一条 JD）。

args:
  workspace:
    type: string
    default: /Users/okonfu/.openclaw/workspace
  yaml:
    type: string
    default: /Users/okonfu/.openclaw/workspace/internships.yaml
  prefs:
    type: string
    default: /Users/okonfu/.openclaw/workspace/internship-prefs.md
  batch_size:
    type: number
    default: 5
  sync_mode:
    type: string
    default: new

env:
  WORKSPACE: ${args.workspace}
  YAML_PATH: ${args.yaml}
  PREFS_PATH: ${args.prefs}
  BATCH_SIZE: ${args.batch_size}
  SYNC_MODE: ${args.sync_mode}

steps:
  # ── 1. 环境检查 ──────────────────────────────────────────────────────────
  - id: preflight
    run: |
      test -f "$PREFS_PATH" || { echo "ERROR: internship-prefs.md not found"; exit 1; }
      test -f "$YAML_PATH"  || { echo "ERROR: internships.yaml not found"; exit 1; }
      python3 --version

  # ── 2. 抓取职位列表（API，不含 JD 正文）────────────────────────────────
  - id: fetch-links
    run: |
      python3 "$WORKSPACE/skills/internship-scout/scripts/fetch_job_links.py" \
        --prefs "$PREFS_PATH" \
        --yaml  "$YAML_PATH"

  # ── 3. 抓取 JD 原文（DOM，硬阻断：空 JD → exit 1）──────────────────────
  - id: fetch-jd-dom
    run: |
      python3 "$WORKSPACE/skills/internship-scout/scripts/fetch_jd_dom.py" \
        --yaml    "$YAML_PATH" \
        --limit   "$BATCH_SIZE" \
        --retries 1
    # 任何条目抓取失败都停止，防止 AI 节点拿到空输入后产生幻觉
    failFast: true

  # ── 4. 字段提取（agentTurn，上下文严格隔离）────────────────────────────
  #
  #  输入：单条 jd_full 原始文本（由 extract-prep 写入临时文件）
  #  输出：严格 JSON，只含 jd_summary + tags
  #  禁止：推断、补全、输出输入中不存在的信息
  #
  - id: extract-prep
    run: |
      # 把每条待处理的 jd_full 写成独立 txt，供 agentTurn 逐条读取
      python3 - <<'EOF'
      import yaml, json
      from pathlib import Path

      yaml_path  = Path("$YAML_PATH")
      cache_dir  = Path("$WORKSPACE/jd_raw_cache")
      cache_dir.mkdir(exist_ok=True)

      items = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or []
      pending = []
      for i, it in enumerate(items):
          if not isinstance(it, dict): continue
          jd = (it.get('jd_full') or '').strip()
          if not jd: continue
          if it.get('jd_summary') and it.get('tags'): continue  # 已提取，跳过
          cache_file = cache_dir / f"{i:04d}.txt"
          cache_file.write_text(jd, encoding='utf-8')
          pending.append({'index': i, 'file': str(cache_file)})

      Path("$WORKSPACE/jd_raw_cache/pending.json").write_text(
          json.dumps(pending, ensure_ascii=False, indent=2), encoding='utf-8'
      )
      print(f"pending={len(pending)}")
      EOF

  - id: extract-fields
    # agentTurn：AI 只能看到单条 JD 文本，输出严格 JSON
    agentTurn:
      model: sonnet
      # 系统提示明确限定输入/输出范围，禁止幻觉
      systemPrompt: |
        你是一个结构化信息提取器，只做提取，不做推断。

        ## 输入
        一段从 BOSS直聘页面抓取的原始 JD 文本（.job-sec-text DOM 节点的 innerText）。

        ## 输出
        严格 JSON，只包含以下两个字段：
        {
          "jd_summary": "<30-50字中文摘要，信息密度高，禁止模板词开头>",
          "tags": ["<只从候选列表中选，不得自造标签>"]
        }

        ## tags 候选列表（只能从这里选）
        Python LangChain LangGraph RAG LLM Agent MCP FastAPI React TypeScript
        Rust Go Docker K8s 向量数据库 微调 LoRA Dify Coze OpenAI Claude Qwen
        多模态 强化学习 RLHF 自动驾驶

        ## 硬性规则
        - jd_summary 中的每个关键词必须在输入文本中出现，禁止推断
        - tags 只选输入中明确提到的技术，不得根据岗位名称猜测
        - 如果输入为空、乱码、或明显不是 JD，输出 {"error": "invalid_input"}
        - 不得输出除上述 JSON 以外的任何内容（无解释、无前缀、无 markdown）

      # 逐条处理：读 pending.json，对每个 txt 文件单独跑一次 agentTurn
      forEach:
        source: "$WORKSPACE/jd_raw_cache/pending.json"
        inputKey: file
        outputDir: "$WORKSPACE/jd_extracted"

  # ── 5. 把提取结果 merge 回 YAML（纯 shell，不经过 AI）──────────────────
  - id: merge-extracted
    run: |
      python3 - <<'EOF'
      import yaml, json, re
      from pathlib import Path

      yaml_path     = Path("$YAML_PATH")
      cache_dir     = Path("$WORKSPACE/jd_raw_cache")
      extracted_dir = Path("$WORKSPACE/jd_extracted")
      pending_file  = cache_dir / "pending.json"

      if not pending_file.exists():
          print("no pending entries, skip merge")
          exit(0)

      pending = json.loads(pending_file.read_text(encoding='utf-8'))
      items   = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or []

      VALID_TAGS = set([
          'Python','LangChain','LangGraph','RAG','LLM','Agent','MCP','FastAPI',
          'React','TypeScript','Rust','Go','Docker','K8s','向量数据库','微调',
          'LoRA','Dify','Coze','OpenAI','Claude','Qwen','多模态','强化学习',
          'RLHF','自动驾驶'
      ])

      merged = 0
      for entry in pending:
          idx        = entry['index']
          out_file   = extracted_dir / f"{idx:04d}.json"
          raw_file   = Path(entry['file'])

          if not out_file.exists():
              print(f"WARN: no extracted output for index {idx}")
              continue

          try:
              extracted = json.loads(out_file.read_text(encoding='utf-8'))
          except Exception as e:
              print(f"WARN: invalid JSON for index {idx}: {e}")
              continue

          if 'error' in extracted:
              print(f"WARN: extraction error for index {idx}: {extracted['error']}")
              continue

          # 幻觉检测：summary 中的中文词必须在原始 JD 中出现
          raw_jd  = raw_file.read_text(encoding='utf-8') if raw_file.exists() else ''
          summary = extracted.get('jd_summary', '')
          key_terms = re.findall(r'[\u4e00-\u9fff]{2,4}', summary)
          hallucinated = [t for t in key_terms[:8] if t not in raw_jd]
          if len(hallucinated) > 2:
              print(f"WARN: possible hallucination in index {idx}, skip. terms={hallucinated}")
              continue

          # 只写白名单内的 tags
          tags = [t for t in (extracted.get('tags') or []) if t in VALID_TAGS]

          it = items[idx]
          it['jd_summary'] = summary
          it['tags']       = tags
          merged += 1

      yaml_path.write_text(yaml.dump(items, allow_unicode=True, sort_keys=False), encoding='utf-8')
      print(f"merged={merged}")
      EOF

  # ── 6. JD 评分 ──────────────────────────────────────────────────────────
  - id: rate-jds
    run: |
      python3 "$WORKSPACE/skills/jd-rater/scripts/rate_jds.py" --missing-only

  # ── 7. Notion 同步 ───────────────────────────────────────────────────────
  - id: notion-sync
    run: |
      python3 "$WORKSPACE/skills/internship-scout/scripts/notion_sync.py" \
        --yaml "$YAML_PATH" \
        --mode "$SYNC_MODE"
