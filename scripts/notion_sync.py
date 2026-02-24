#!/usr/bin/env python3
"""
notion_sync.py — Sync internships.yaml to a Notion database.

Usage:
  python3 notion_sync.py [--yaml PATH] [--db-id ID_OR_URL] [--mode new|update|all]
                         [--filter COMPANY] [--dry-run]

Inputs:
  --yaml     Path to internships.yaml. Default: ~/.openclaw/workspace/internships.yaml
  --db-id    Notion DB ID (UUID or full Notion share URL). If omitted, reads
             notion_db_id from internship-prefs.md; if still missing, prompts user.
  --mode     new    — only POST entries where notion_page_id is empty (default)
             update — only PATCH entries that already have a notion_page_id
             all    — both new and update
  --filter   Only sync entries whose company name contains this string.
  --dry-run  Print what would happen without making any API calls.

Outputs:
  stdout:    ✅ CompanyName | created  or  ✅ CompanyName | updated  or  ❌ CompanyName | reason
  Side-effect: writes notion_page_id back to YAML for newly created pages.
  Exit code: 0 = all succeeded, 1 = any failure.

Environment:
  NOTION_API_KEY  — required
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

WORKSPACE = Path("~/.openclaw/workspace").expanduser()
DEFAULT_YAML = WORKSPACE / "internships.yaml"
PREFS_FILE   = WORKSPACE / "internship-prefs.md"
API_VERSION  = "2022-06-28"
NOTION_KEY   = os.environ.get("NOTION_API_KEY", "")

# ── Field mapping: YAML key → Notion property name ──────────────────────────
FIELD_MAP = {
    "company":       ("Name",    "title"),
    "salary":        ("薪资",    "rich_text"),
    "location":      ("城市",    "rich_text"),
    "company_size":  ("规模",    "select"),
    "funding_stage": ("融资阶段","select"),
    "jd_quality":    ("JD质量",  "select"),
    "status":        ("状态",    "select"),
    "tags":          ("技术标签","multi_select"),
    "url":           ("链接",    "url"),
    "collected_at":  ("收录日期","date"),
    "jd_summary":    ("JD摘要",  "rich_text"),
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def notion_request(method: str, endpoint: str, payload: dict | None = None) -> dict:
    url = f"https://api.notion.com/v1/{endpoint}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {NOTION_KEY}")
    req.add_header("Notion-Version", API_VERSION)
    req.add_header("Content-Type", "application/json")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, ConnectionResetError) as e:
            if attempt == 2:
                raise
            time.sleep(1.5)
    return {}


def extract_db_id(raw: str) -> str:
    """Accept UUID or Notion share URL, return formatted UUID."""
    # Strip URL noise
    m = re.search(r"([0-9a-f]{32})", raw.replace("-", ""))
    if not m:
        return ""
    h = m.group(1)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def read_prefs_db_id() -> str:
    if not PREFS_FILE.exists():
        return ""
    text = PREFS_FILE.read_text()
    m = re.search(r"notion_db_id:\s*([^\s\n]+)", text)
    return m.group(1).strip() if m else ""


def write_prefs_db_id(db_id: str):
    text = PREFS_FILE.read_text() if PREFS_FILE.exists() else ""
    if "notion_db_id:" in text:
        text = re.sub(r"notion_db_id:\s*[^\n]*", f"notion_db_id: {db_id}", text)
    else:
        text = text.rstrip() + f"\n\n## Notion 数据库\n\n- notion_db_id: {db_id}\n"
    PREFS_FILE.write_text(text)


def resolve_db_id(arg_db_id: str) -> str:
    """Resolve DB ID from arg → prefs → prompt user."""
    if arg_db_id:
        db_id = extract_db_id(arg_db_id)
        if db_id:
            return db_id

    db_id = read_prefs_db_id()
    if db_id:
        return db_id

    print("⚠️  未找到 Notion 数据库 ID。")
    print("请提供以下任意一种格式：")
    print("  1. 标准 UUID：75ba29af-95bf-43e3-bf02-37960aa08b5d")
    print("  2. Notion 分享链接：https://www.notion.so/75ba29af...?v=...")
    print("  3. 输入 'new' 在 ヤチヨ 元Agent 页面下新建数据库")
    raw = input("→ ").strip()

    if raw.lower() == "new":
        db_id = create_database()
    else:
        db_id = extract_db_id(raw)

    if not db_id:
        print("❌ 无法解析数据库 ID，退出。")
        sys.exit(1)

    write_prefs_db_id(db_id)
    print(f"✅ 已保存 notion_db_id: {db_id}")
    return db_id


def create_database() -> str:
    """Create a new 实习岗位追踪 database under ヤチヨ 元Agent."""
    parent_id = "3102496b-9cb5-8003-8188-d6bf72b71afa"
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "icon": {"type": "emoji", "emoji": "📋"},
        "title": [{"type": "text", "text": {"content": "实习岗位追踪"}}],
    }
    res = notion_request("POST", "databases", payload)
    db_id = res.get("id", "")
    if not db_id:
        print("❌ 创建数据库失败:", res.get("message", ""))
        sys.exit(1)

    # Add properties via PATCH (2022-06-28 quirk)
    props = {
        "薪资": {"rich_text": {}},
        "城市": {"rich_text": {}},
        "规模": {"select": {"options": [
            {"name": "20-99人", "color": "green"},
            {"name": "100-499人", "color": "blue"},
        ]}},
        "融资阶段": {"select": {"options": [
            {"name": "天使轮", "color": "pink"}, {"name": "A轮", "color": "orange"},
            {"name": "B轮", "color": "yellow"}, {"name": "C轮", "color": "green"},
            {"name": "未融资", "color": "gray"}, {"name": "不需要融资", "color": "gray"},
        ]}},
        "JD质量": {"select": {"options": [
            {"name": "good", "color": "green"}, {"name": "unclear", "color": "yellow"},
            {"name": "skip", "color": "red"},
        ]}},
        "状态": {"select": {"options": [
            {"name": "pending", "color": "gray"}, {"name": "applied", "color": "blue"},
            {"name": "interviewing", "color": "orange"}, {"name": "offered", "color": "green"},
            {"name": "rejected", "color": "red"}, {"name": "ghosted", "color": "brown"},
        ]}},
        "技术标签": {"multi_select": {"options": []}},
        "来源": {"rich_text": {}},
        "链接": {"url": {}},
        "收录日期": {"date": {}},
        "JD摘要": {"rich_text": {}},
    }
    notion_request("PATCH", f"databases/{db_id}", {"properties": props})
    print(f"✅ 数据库已创建: {db_id}")
    return db_id


# ── YAML parsing ─────────────────────────────────────────────────────────────

def parse_yaml(path: Path) -> list[dict]:
    text = path.read_text()
    entries = []
    for block in re.split(r"\n  - title:", text)[1:]:
        e = {}
        for f in ["title", "company", "salary", "location", "company_size",
                  "funding_stage", "jd_summary", "url", "source", "status",
                  "jd_quality", "collected_at", "notion_page_id"]:
            m = re.search(rf'{f}:\s*"([^"]*)"', block)
            e[f] = m.group(1) if m else ""
        tm = re.search(r"tags:\s*\[([^\]]*)\]", block)
        e["tags"] = [t.strip().strip('"') for t in tm.group(1).split(",")
                     if t.strip().strip('"')] if tm else []
        entries.append(e)
    return entries


def write_notion_id(yaml_path: Path, url: str, notion_id: str):
    text = yaml_path.read_text()
    url_esc = re.escape(url)
    if "notion_page_id:" in text:
        text = re.sub(
            rf'(url:\s*"{url_esc}".*?notion_page_id:\s*)"[^"]*"',
            rf'\g<1>"{notion_id}"', text, count=1, flags=re.DOTALL)
    else:
        text = re.sub(
            rf'(url:\s*"{url_esc}")',
            rf'\1\n    notion_page_id: "{notion_id}"', text, count=1)
    yaml_path.write_text(text)


# ── Notion property builder ───────────────────────────────────────────────────

def build_properties(entry: dict) -> dict:
    props = {}
    for yaml_key, (notion_key, notion_type) in FIELD_MAP.items():
        val = entry.get(yaml_key, "")
        if not val and yaml_key != "company":
            continue
        if notion_type == "title":
            props[notion_key] = {"title": [{"text": {"content": str(val)[:2000]}}]}
        elif notion_type == "rich_text":
            props[notion_key] = {"rich_text": [{"text": {"content": str(val)[:2000]}}]}
        elif notion_type == "select":
            props[notion_key] = {"select": {"name": str(val)}}
        elif notion_type == "multi_select":
            props[notion_key] = {"multi_select": [{"name": t} for t in val[:10]]}
        elif notion_type == "url":
            props[notion_key] = {"url": val}
        elif notion_type == "date":
            props[notion_key] = {"date": {"start": val}}
    return props


# ── Main sync logic ───────────────────────────────────────────────────────────

def sync(entries: list[dict], db_id: str, mode: str,
         filter_str: str, dry_run: bool, yaml_path: Path) -> int:
    errors = 0
    for entry in entries:
        company = entry.get("company", "?")
        nid = entry.get("notion_page_id", "")
        url = entry.get("url", "")

        if filter_str and filter_str.lower() not in company.lower():
            continue
        if mode == "new" and nid:
            continue
        if mode == "update" and not nid:
            continue

        props = build_properties(entry)
        action = "updated" if nid else "created"

        if dry_run:
            print(f"[dry-run] {action} → {company}")
            continue

        try:
            if nid:
                res = notion_request("PATCH", f"pages/{nid}", {"properties": props})
            else:
                res = notion_request("POST", "pages",
                                     {"parent": {"database_id": db_id}, "properties": props})

            if res.get("id"):
                if not nid:
                    write_notion_id(yaml_path, url, res["id"])
                print(f"✅ {company} | {action}")
            else:
                print(f"❌ {company} | {res.get('message', 'unknown error')[:80]}")
                errors += 1
        except Exception as e:
            print(f"❌ {company} | {e}")
            errors += 1

        time.sleep(0.2)

    return errors


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync internships.yaml → Notion")
    parser.add_argument("--yaml",    default=str(DEFAULT_YAML))
    parser.add_argument("--db-id",   default="")
    parser.add_argument("--mode",    choices=["new", "update", "all"], default="new")
    parser.add_argument("--filter",  default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not NOTION_KEY:
        print("❌ NOTION_API_KEY not set")
        sys.exit(1)

    yaml_path = Path(args.yaml).expanduser()
    if not yaml_path.exists():
        print(f"❌ YAML not found: {yaml_path}")
        sys.exit(1)

    db_id = resolve_db_id(args.db_id)
    entries = parse_yaml(yaml_path)

    target = [e for e in entries
              if not args.filter or args.filter.lower() in e.get("company","").lower()]
    if args.mode == "new":
        target = [e for e in target if not e.get("notion_page_id")]
    elif args.mode == "update":
        target = [e for e in target if e.get("notion_page_id")]

    print(f"DB: {db_id} | mode: {args.mode} | entries: {len(target)}"
          + (" [dry-run]" if args.dry_run else ""))

    errors = sync(entries, db_id, args.mode, args.filter, args.dry_run, yaml_path)
    sys.exit(0 if errors == 0 else 1)


if __name__ == "__main__":
    main()
