#!/usr/bin/env python3
"""
summarize_jds.py — 为 internships.yaml 批量生成 jd_summary、tags、jd_quality（ABCD）。

工作流：
  输入：internships.yaml（有 jd_full 但缺 jd_summary/tags/jd_quality 的条目）
  输出：写回 internships.yaml（jd_summary 30-50字、tags 技术栈、jd_quality A/B/C/D）

  每批最多 5 条，spawn cleanup=delete subagent 做纯文本推理（不使用任何 tools）。
  主会话串行处理各批次，等回传后写回再继续下一批。

用法：
  # 处理所有待处理条目
  python3 scripts/summarize_jds.py

  # 只处理前 10 条
  python3 scripts/summarize_jds.py --limit 10

  # 强制重处理已有 summary 的条目
  python3 scripts/summarize_jds.py --refetch

  # 列出待处理条目
  python3 scripts/summarize_jds.py --list-pending

  # 只打印某批 prompt（调试用）
  python3 scripts/summarize_jds.py --dry-run --batch 0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

WORKSPACE = Path('/Users/okonfu/.openclaw/workspace')
YAML_PATH = WORKSPACE / 'internships.yaml'
BATCH_SIZE = 5

SYSTEM_PROMPT = """你是一个 JD 分析助手。只输出 JSON，不使用任何工具，不联网，不查询外部信息。

对每条 JD，输出：
- jd_summary: 30-50字中文摘要，只描述核心职责和技术要求，不得出现原文中没有的词
- tags: 技术栈标签数组，3-8个，只从原文提取，候选包括但不限于：
  Python, Java, C++, Go, Rust, JavaScript, TypeScript,
  LLM, Agent开发, RAG, LangChain, LangGraph, AutoGen, MCP,
  大模型, 多模态, 强化学习, 微调, RLHF, 推理优化,
  PyTorch, TensorFlow, CUDA, 分布式训练,
  后端开发, 全栈开发, 前端开发, 数据分析, 数据标注,
  NLP, CV, 自动驾驶, 语音识别, TTS,
  转正机会, 远程, 海外
- jd_quality: ABCD 四级
  A: JD≥200字，技术关键词≥5个，职责清晰，有明确技术栈
  B: JD≥100字，技术关键词≥3个，职责基本清晰
  C: JD偏短或技术描述模糊，关键词<3个
  D: 非技术岗/外包/纯销售/学历门槛过高/内容严重不足

输出格式（严格 JSON 数组，顺序与输入一致）：
[{"id": 0, "jd_summary": "...", "tags": [...], "jd_quality": "A"}, ...]

只输出 JSON，不要任何解释。"""


# ── YAML helpers ──────────────────────────────────────────────────────────────

def load_data(path: Path) -> tuple[dict, list]:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    entries = data.get('internships', []) if isinstance(data, dict) else data
    return data, entries


def save_data(path: Path, data: dict, entries: list) -> None:
    if isinstance(data, dict):
        data['internships'] = entries
    path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding='utf-8',
    )


def get_pending(entries: list, refetch: bool = False) -> list[tuple[int, dict]]:
    return [
        (i, e) for i, e in enumerate(entries)
        if e.get('jd_full', '').strip()
        and (refetch or not e.get('jd_summary', '').strip())
    ]


# ── Prompt builder ─────────────────────────────────────────────────────────────

def build_prompt(batch: list[dict]) -> str:
    lines = [SYSTEM_PROMPT, '']
    for i, e in enumerate(batch):
        lines.append(f'=== 条目 {i} ===')
        lines.append(f"职位: {e.get('title', '')} @ {e.get('company', '')}")
        lines.append(f"JD原文:\n{e.get('jd_full', '').strip()}")
        lines.append('')
    return '\n'.join(lines)


# ── Result parser & writer ─────────────────────────────────────────────────────

def parse_result(text: str) -> list[dict] | None:
    text = text.strip()
    start = text.find('[')
    end = text.rfind(']')
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def apply_result(entries: list, batch_indices: list[int], results: list[dict]) -> int:
    updated = 0
    for item in results:
        idx_in_batch = item.get('id')
        if idx_in_batch is None or idx_in_batch >= len(batch_indices):
            continue
        entry = entries[batch_indices[idx_in_batch]]

        summary = (item.get('jd_summary') or '').strip()
        tags = item.get('tags') or []
        quality = (item.get('jd_quality') or '').strip().upper()

        if summary and len(summary) >= 10:
            entry['jd_summary'] = summary
        if tags and isinstance(tags, list):
            entry['tags'] = [str(t).strip() for t in tags if t]
        if quality in ('A', 'B', 'C', 'D'):
            entry['jd_quality'] = quality
            updated += 1

        print(f"  [{batch_indices[idx_in_batch]}] {entry.get('company')} | {quality} | {summary[:40]}")
    return updated


# ── sessions_spawn via openclaw gateway API ────────────────────────────────────

def spawn_and_wait(task: str, label: str, timeout: int = 180) -> str | None:
    """
    Spawn a subagent via openclaw HTTP API and poll for result.
    Returns the final assistant text, or None on failure.
    """
    import urllib.request
    import urllib.error

    gateway = 'http://localhost:19000'

    # 1. spawn
    payload = json.dumps({
        'task': task,
        'label': label,
        'cleanup': 'delete',
        'mode': 'run',
        'runTimeoutSeconds': timeout,
    }).encode()

    try:
        req = urllib.request.Request(
            f'{gateway}/api/sessions/spawn',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            spawn_data = json.loads(resp.read())
    except Exception as e:
        print(f'  spawn error: {e}', file=sys.stderr)
        return None

    session_key = spawn_data.get('childSessionKey')
    if not session_key:
        print(f'  no childSessionKey: {spawn_data}', file=sys.stderr)
        return None

    print(f'  spawned: {session_key}')

    # 2. poll history until assistant reply appears
    deadline = time.time() + timeout
    last_seen = 0
    while time.time() < deadline:
        time.sleep(4)
        try:
            req = urllib.request.Request(
                f'{gateway}/api/sessions/{urllib.parse.quote(session_key, safe="")}/history?limit=20',
                method='GET',
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                hist = json.loads(resp.read())
        except Exception:
            continue

        messages = hist.get('messages', [])
        for msg in reversed(messages):
            if msg.get('role') == 'assistant':
                for block in (msg.get('content') or []):
                    if block.get('type') == 'text' and block.get('text', '').strip():
                        return block['text']
    return None


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    import urllib.parse  # noqa: F401 — needed by spawn_and_wait

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--yaml', type=Path, default=YAML_PATH)
    ap.add_argument('--limit', type=int, default=0, help='Max entries to process (0=all)')
    ap.add_argument('--refetch', action='store_true', help='Re-process entries that already have summary')
    ap.add_argument('--batch-size', type=int, default=BATCH_SIZE)
    ap.add_argument('--list-pending', action='store_true')
    ap.add_argument('--dry-run', action='store_true', help='Print prompts without spawning')
    ap.add_argument('--batch', type=int, default=None, help='Only process this batch index (0-based, for --dry-run)')
    # internal: write-result mode (used by main session after receiving subagent output)
    ap.add_argument('--write-result', type=str, help='JSON string to write back for --batch N')
    args = ap.parse_args()

    data, entries = load_data(args.yaml)
    pending = get_pending(entries, args.refetch)
    if args.limit > 0:
        pending = pending[:args.limit]

    # ── list-pending ──
    if args.list_pending:
        print(f'Pending: {len(pending)} entries')
        batches = (len(pending) + args.batch_size - 1) // args.batch_size
        print(f'Batches needed: {batches}')
        for i, (idx, e) in enumerate(pending):
            print(f'  [{idx}] {e.get("company")} — {e.get("title")}')
        return

    # ── write-result mode ──
    if args.write_result is not None:
        if args.batch is None:
            print('--write-result requires --batch N', file=sys.stderr)
            sys.exit(1)
        batch_start = args.batch * args.batch_size
        batch_pairs = pending[batch_start: batch_start + args.batch_size]
        batch_indices = [i for i, _ in batch_pairs]
        results = parse_result(args.write_result)
        if not results:
            print('Failed to parse result JSON', file=sys.stderr)
            sys.exit(1)
        apply_result(entries, batch_indices, results)
        save_data(args.yaml, data, entries)
        return

    # ── dry-run: print prompt for one batch ──
    if args.dry_run:
        b = args.batch or 0
        batch_start = b * args.batch_size
        batch_entries = [e for _, e in pending[batch_start: batch_start + args.batch_size]]
        print(build_prompt(batch_entries))
        return

    # ── main loop: spawn batches serially ──
    # NOTE: sessions_spawn is blocked on HTTP /tools/invoke by default.
    # This script handles prompt building and result writing only.
    # The main session (agent) is responsible for spawning subagents and
    # calling --write-result after each batch completes.
    #
    # To run the full pipeline, ask the main agent to:
    #   1. python3 scripts/summarize_jds.py --list-pending
    #   2. For each batch: --dry-run --batch N  →  spawn subagent  →  --write-result '<json>' --batch N
    print('Use --list-pending to see pending entries.')
    print('Use --dry-run --batch N to get the prompt for batch N.')
    print('Use --write-result \'<json>\' --batch N to write back results.')
    print('The main agent session handles spawning subagents between these steps.')


if __name__ == '__main__':
    main()
