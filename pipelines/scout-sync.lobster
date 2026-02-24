name: internship-scout-sync
args:
  workspace: string
  yaml: string
  prefs: string
  batch_size: number
  sync_mode: string
  filter_company: string?

env:
  WORKSPACE: ${args.workspace}
  YAML_PATH: ${args.yaml}
  PREFS_PATH: ${args.prefs}
  BATCH_SIZE: ${args.batch_size}
  SYNC_MODE: ${args.sync_mode}
  FILTER_COMPANY: ${args.filter_company}

steps:
  - id: preflight
    run: |
      test -f "$PREFS_PATH"
      test -f "$YAML_PATH"
      python3 --version

  - id: fetch-links
    run: |
      python3 "$WORKSPACE/skills/internship-scout/scripts/fetch_job_links.py" \
        --prefs "$PREFS_PATH" \
        --yaml "$YAML_PATH"

  - id: fetch-jd-dom
    run: |
      python3 "$WORKSPACE/skills/internship-scout/scripts/fetch_jd_dom.py" \
        --yaml "$YAML_PATH" \
        --limit "$BATCH_SIZE" \
        --retry-empty 1

  - id: summarize
    run: |
      python3 "$WORKSPACE/skills/internship-scout/scripts/summarize_jd_deterministic.py" \
        --yaml "$YAML_PATH" --min-len 30 --max-len 50

  - id: rate-sync
    run: |
      bash "$WORKSPACE/skills/internship-scout/scripts/rate_and_sync.sh" \
        "$WORKSPACE" "$YAML_PATH" "$SYNC_MODE" "$FILTER_COMPANY"
