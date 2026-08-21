#!/usr/bin/env python3
"""Recover canonical result.md files from completed reports in evaluation logs.

This is an offline repair tool: it never starts an app or calls a model.  It
replays the same semantic result parser used by the runners against each rep's
terminal result and captured assistant text.  By default it is a dry run.

Examples:
    python scripts/recover_results.py ../experiments/runs/2026-08-19_sonnet-5
    python scripts/recover_results.py ../experiments/runs/2026-08-19_sonnet-5 --apply
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator


EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from agent.base_agent import APIConfig, BaseAgent


RESULT_RE = re.compile(
    r'"result"\s*:\s*(null|"(?:\\.|[^"\\])*")\s*,\s*'
    r'\n\s*"structured_output"',
    re.DOTALL,
)
EVENT_PREFIX_RE = re.compile(r"__EVENT__\s+")


def event_records(text: str) -> Iterator[dict[str, Any]]:
    """Yield complete JSON records written after ``__EVENT__`` in an eval log."""
    decoder = json.JSONDecoder()
    for match in EVENT_PREFIX_RE.finditer(text):
        try:
            value, _ = decoder.raw_decode(text, match.end())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def log_candidates(log_path: Path) -> tuple[str, list[str]]:
    """Return the terminal result plus assistant text candidates from one log."""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    terminal_result = ""
    assistant_texts: list[str] = []

    for event in event_records(text):
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("role") != "assistant":
            continue
        content = payload.get("content")
        if payload.get("type") == "assistant_message" and isinstance(content, str):
            assistant_texts.append(content)
        elif payload.get("type") == "result_message" and isinstance(content, dict):
            result = content.get("result")
            if isinstance(result, str):
                terminal_result = result

    # Older pretty-printed logs may not have a parseable __EVENT__ wrapper for
    # the ResultMessage.  Its result field has a stable JSON shape, so use it
    # as a fallback.
    if not terminal_result:
        matches = RESULT_RE.findall(text)
        for raw in reversed(matches):
            if raw == "null":
                continue
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, str):
                terminal_result = decoded
                break

    return terminal_result, assistant_texts


def parser_for_rep(rep_dir: Path) -> BaseAgent:
    """Create the lightweight parser host for this rep's checklist."""
    return BaseAgent(
        instruction="offline result recovery",
        api_config=APIConfig(model="offline-recovery", auth_mode="subscription"),
        server_url="http://localhost/offline-recovery",
        local_project_dir=rep_dir,
        output_dir=rep_dir,
    )


def recover_rep(rep_dir: Path) -> dict[str, Any]:
    """Find the newest complete, semantically valid report for a missing rep."""
    logs = sorted(rep_dir.glob("*-eval.log"), reverse=True)
    if not logs:
        return {"status": "skipped", "reason": "no_eval_log"}
    if not (rep_dir / "checklist.md").exists():
        return {"status": "skipped", "reason": "no_checklist"}

    parser = parser_for_rep(rep_dir)
    best_failure = None
    for log_path in logs:
        terminal_result, assistant_texts = log_candidates(log_path)
        parser.recent_assistant_text_blocks = {"defect_detection": assistant_texts}
        parsed, from_result_message = parser._normalise_defect_result(terminal_result)
        if parsed.is_valid:
            return {
                "status": "recoverable",
                "source_log": str(log_path),
                "source": "result_message" if from_result_message else "assistant_text",
                "result": parsed.canonical_result,
                "emitted_count": parsed.emitted_count,
            }
        if best_failure is None or parsed.emitted_count > best_failure["emitted_count"]:
            best_failure = {
                "status": "skipped",
                "reason": parsed.failure_kind,
                "emitted_count": parsed.emitted_count,
                "missing_ids": parsed.missing_ids or [],
                "duplicate_ids": parsed.duplicate_ids or [],
                "unknown_ids": parsed.unknown_ids or [],
            }
    return best_failure or {"status": "skipped", "reason": "no_result_candidate"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="experiments/runs/<run-id> directory")
    parser.add_argument("--apply", action="store_true", help="write recovered result.md files")
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        parser.error(f"run_dir does not exist: {run_dir}")

    reps = sorted(path for path in run_dir.glob("*/*/rep*") if path.is_dir())
    manifest: dict[str, Any] = {
        "run_dir": str(run_dir),
        "mode": "apply" if args.apply else "dry_run",
        "reps": {},
    }
    recovered = existing = skipped = 0

    for rep_dir in reps:
        key = str(rep_dir.relative_to(run_dir))
        result_path = rep_dir / "result.md"
        if result_path.is_file() and result_path.stat().st_size > 0:
            existing += 1
            manifest["reps"][key] = {"status": "existing_result"}
            continue

        outcome = recover_rep(rep_dir)
        if outcome["status"] == "recoverable":
            recovered += 1
            if args.apply:
                result_path.write_text(outcome.pop("result"), encoding="utf-8")
                outcome["status"] = "recovered"
            else:
                outcome.pop("result")
            print(f"{outcome['status']:>11}  {key}")
        else:
            skipped += 1
            print(f"{outcome['status']:>11}  {key}  ({outcome.get('reason')})")
        manifest["reps"][key] = outcome

    manifest_path = run_dir / (
        "result_recovery_manifest.json" if args.apply else "result_recovery_dry_run.json"
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n{'Recovered' if args.apply else 'Recoverable'}: {recovered}; "
        f"existing: {existing}; skipped: {skipped}.\n"
        f"Manifest: {manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
