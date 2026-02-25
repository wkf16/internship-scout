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

WORKSPACE = Path.home() / '.openclaw/workspace'
YAML_PATH = WORKSPACE / 'internships.yaml'
BATCH_SIZE = 5

SYSTEM_PROMPT = """你是一个 JD 质量评分助手。只输出 JSON，不使用任何工具，不联网，不查询外部信息。

对每条 JD，按以下步骤处理：

Step 1 — 阅读 JD 原文全文（不要基于摘要评分）

Step 2 — 按三个维度各打 1-3 分：

  [clarity 描述清晰度] 职责是否具体、可执行，而非套话
    3 = 职责明确，能判断每天做什么
    2 = 有方向但部分笼统
    1 = 全是"参与/协助/配合"等套话

  [tech_stack 技术栈明确度] 是否点名具体技术/工具/框架
    3 = 有≥3个具体技术名称（Python/LangChain/RAG/CUDA/PyTorch等）
    2 = 有1-2个具体技术名称
    1 = 只有泛称（AI/大模型/人工智能）或无技术要求

  [role_signal 岗位匹配信号] 是否是真实技术/研究岗
    3 = 明确技术/算法/研究岗
    2 = 技术+产品混合岗
    1 = 非技术岗/外包/纯销售

Step 3 — 总分映射等级（total = clarity + tech_stack + role_signal）：
  8-9分 → A
  6-7分 → B
  4-5分 → C
  3分   → D
  含"外包/销售/无任何技术要求"任一特征 → F

Step 4 — 写 jd_summary（30-50字，基于原文，不基于评分结果）

Step 5 — 提取 tags（3-8个，只从原文提取具体技术名称，候选包括但不限于：
  Python, Java, C++, Go, Rust, JavaScript, TypeScript,
  LLM, Agent开发, RAG, LangChain, LangGraph, AutoGen, MCP,
  大模型, 多模态, 强化学习, 微调, RLHF, 推理优化,
  PyTorch, TensorFlow, CUDA, 分布式训练,
  后端开发, 全栈开发, 前端开发, 数据分析, 数据标注,
  NLP, CV, 自动驾驶, 语音识别, TTS, SQL,
  转正机会, 远程, 海外）

输出格式（严格 JSON 数组，顺序与输入一致）：
[{
  "id": 0,
  "clarity": 3,
  "tech_stack": 3,
  "role_signal": 2,
  "jd_score": 8,
  "jd_quality": "A",
  "jd_summary": "...",
  "tags": [...]
}, ...]

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
        score = item.get('jd_score')

        if summary and len(summary) >= 10:
            entry['jd_summary'] = summary
        if tags and isinstance(tags, list):
            entry['tags'] = [str(t).strip() for t in tags if t]
        if quality in ('A', 'B', 'C', 'D', 'F'):
            entry['jd_quality'] = quality
            updated += 1
        if isinstance(score, int) and 3 <= score <= 9:
            entry['jd_score'] = score

        print(f"  [{batch_indices[idx_in_batch]}] {entry.get('company')} | {quality}({score}) | {summary[:40]}")
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
    ap.add_argument('--list-pending', action='store_true')
    ap.add_argument('--dry-run', action='store_true', help='Print prompt without spawning')
    ap.add_argument('--write-result', type=str, help='JSON string to write back results')
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
        batch_indices = [i for i, _ in pending]
        results = parse_result(args.write_result)
        if not results:
            print('Failed to parse result JSON', file=sys.stderr)
            sys.exit(1)
        apply_result(entries, batch_indices, results)
        save_data(args.yaml, data, entries)
        return

    # ── dry-run: print full prompt ──
    if args.dry_run:
        batch_entries = [e for _, e in pending]
        print(build_prompt(batch_entries))
        return

    # ── main loop: single batch, one subagent ──
    if not pending:
        print('No pending entries.')
        return

    total = len(pending)
    batch_indices = [i for i, _ in pending]
    batch_entries = [e for _, e in pending]

    print(f'Total pending: {total} | Spawning single subagent for all entries.')
    prompt = build_prompt(batch_entries)

    result_text = spawn_and_wait(prompt, label='summarize-jds')
    if not result_text:
        print('Subagent failed or timed out.', file=sys.stderr)
        sys.exit(1)

    results = parse_result(result_text)
    if not results:
        print(f'Unparseable JSON:\n{result_text[:300]}', file=sys.stderr)
        sys.exit(1)

    updated = apply_result(entries, batch_indices, results)
    save_data(args.yaml, data, entries)
    print(f'\nDone. Updated {updated}/{total}')


if __name__ == '__main__':
    main()
