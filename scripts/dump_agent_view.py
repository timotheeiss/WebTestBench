#!/usr/bin/env python3
"""
Dump exactly what a Claude Code testing agent "saw" and "did" during a run,
by replaying its SDK session transcript.

Usage:
    python scripts/dump_agent_view.py <session_id | path/to/session.jsonl> [--full] [--out FILE] [--stdout]

- Pairs each tool_use (browser_*) with its tool_result.
- For browser_snapshot, the tool_result IS the accessibility tree the model received.
- Without --full, snapshots are truncated for readability; with --full, printed whole.
- By default the report is written into the matching run's output folder
  (e.g. outputs/<version>/<record_id>/agent_view.txt), resolved from the session id
  recorded in that folder's session_meta.json. Override with --out, or force the
  terminal with --stdout.
"""
import json, sys, argparse, glob, os

PROJECTS = os.path.expanduser("~/.claude/projects")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUTS = os.path.join(REPO_ROOT, "outputs")


def resolve_transcript(arg: str) -> str:
    if os.path.isfile(arg):
        return arg
    hits = glob.glob(f"{PROJECTS}/**/{arg}.jsonl", recursive=True)
    if not hits:
        sys.exit(f"No transcript found for session id {arg!r} under {PROJECTS}")
    return hits[0]


def resolve_output_dir(session_id: str):
    """Find the outputs/<version>/<record_id> folder whose session_meta.json
    references this session id. Returns the dir path, or None."""
    for meta in glob.glob(os.path.join(OUTPUTS, "*", "*", "session_meta.json")):
        try:
            if session_id in open(meta, encoding="utf-8").read():
                return os.path.dirname(meta)
        except OSError:
            continue
    return None


def text_of(content) -> str:
    """Flatten a tool_result/content payload to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or json.dumps(b)[:200])
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return str(content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--full", action="store_true", help="print full snapshots, no truncation")
    ap.add_argument("--out", help="explicit output file path (overrides auto-resolution)")
    ap.add_argument("--stdout", action="store_true", help="print to terminal instead of the run folder")
    ap.add_argument("--limit", type=int, default=2000, help="truncation length when not --full")
    args = ap.parse_args()

    path = resolve_transcript(args.session)
    session_id = os.path.splitext(os.path.basename(path))[0]
    lines = [json.loads(l) for l in open(path) if l.strip()]

    # Decide where to write: explicit --out > run's output folder > stdout.
    out_path = None
    if args.out:
        out_path = args.out
    elif not args.stdout:
        run_dir = resolve_output_dir(session_id)
        if run_dir:
            out_path = os.path.join(run_dir, "agent_view.txt")
        else:
            print(f"[warn] no outputs/*/*/session_meta.json references {session_id}; "
                  f"printing to stdout (use --out to force a path).", file=sys.stderr)

    # Collect tool_use blocks and tool_results (keyed by tool_use_id).
    tool_uses = []          # (id, name, input)
    results = {}            # id -> text
    for o in lines:
        msg = o.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                tool_uses.append((b.get("id"), b.get("name", ""), b.get("input", {})))
            elif b.get("type") == "tool_result":
                results[b.get("tool_use_id")] = text_of(b.get("content"))

    out = open(out_path, "w", encoding="utf-8") if out_path else sys.stdout
    def w(s=""):
        print(s, file=out)

    snap_n = 0
    w(f"# Transcript: {path}")
    w(f"# total tool calls: {len(tool_uses)}\n")
    for i, (tid, name, inp) in enumerate(tool_uses, 1):
        short = name.replace("mcp__playwright__", "")
        res = results.get(tid, "<no result captured>")
        is_snap = name.endswith("browser_snapshot")
        if is_snap:
            snap_n += 1
        head = f"[{i:02d}] {short}"
        if inp:
            head += f"  input={json.dumps(inp, ensure_ascii=False)[:160]}"
        w("=" * 80)
        w(head)
        w("-" * 80)
        body = res if (args.full or not is_snap) else (res[: args.limit] + ("\n…[truncated]" if len(res) > args.limit else ""))
        w(body)
        w()
    w(f"\n# accessibility snapshots seen by the agent: {snap_n}")
    if out_path:
        out.close()
        print(f"Wrote report to {out_path}  (snapshots: {snap_n}, tool calls: {len(tool_uses)})")


if __name__ == "__main__":
    main()
