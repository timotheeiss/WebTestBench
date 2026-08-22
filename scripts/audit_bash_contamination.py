#!/usr/bin/env python3
"""Label evaluation replications that requested the Claude ``Bash`` tool.

This is an offline audit: it reads only evaluation logs and never starts an
application or calls a model.  It treats a rep as contaminated when *any* of
its primary ``*-eval.log`` attempts contains an ``assistant_tool`` event named
``Bash``.  This intentionally includes requests that the permission layer
later denied, so the label records a protocol deviation rather than claiming
that a shell command succeeded.

By default the script writes two files to the run directory:

* ``bash_contamination_reps.csv``: one labelled row per app/rep.
* ``bash_contamination_manifest.json``: row data plus the source logs.

``retry_logs`` copies are excluded to avoid double-counting attempts.

Examples:
    python scripts/audit_bash_contamination.py ../experiments/runs/2026-08-17_sonnet-5
    python scripts/audit_bash_contamination.py ../experiments/runs/2026-08-19_sonnet-5 \\
        --output-dir /tmp/aug19-bash-audit
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator


EVENT_PREFIX = "__EVENT__ "
BASH_TOOL_NAME = "Bash"
APPROVAL_MARKERS = ("requires approval", "requested permissions", "permission")


def event_records(text: str) -> Iterator[dict[str, Any]]:
    """Yield parseable event records from an evaluation log."""
    decoder = json.JSONDecoder()
    position = 0
    while True:
        start = text.find(EVENT_PREFIX, position)
        if start == -1:
            return
        try:
            event, position = decoder.raw_decode(text, start + len(EVENT_PREFIX))
        except json.JSONDecodeError:
            position = start + len(EVENT_PREFIX)
            continue
        if isinstance(event, dict):
            yield event


def is_permission_denial(content: object) -> bool:
    if not isinstance(content, str):
        return False
    lowered = content.lower()
    return any(marker in lowered for marker in APPROVAL_MARKERS)


def audit_log(log_path: Path) -> dict[str, int]:
    """Count Bash requests and their immediately following tool result."""
    events = list(event_records(log_path.read_text(encoding="utf-8", errors="replace")))
    counts = {
        "bash_requests": 0,
        "permission_denied": 0,
        "tool_result_received": 0,
        "unclassified": 0,
    }

    for index, event in enumerate(events):
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "assistant_tool" or payload.get("content") != BASH_TOOL_NAME:
            continue

        counts["bash_requests"] += 1
        following = events[index + 1].get("payload") if index + 1 < len(events) else None
        if not isinstance(following, dict) or following.get("type") != "user_tool_result":
            counts["unclassified"] += 1
        elif is_permission_denial(following.get("content")):
            counts["permission_denied"] += 1
        else:
            # This means the SDK returned a response.  It may still represent
            # an unsuccessful shell command (for example, "Exit code 1").
            counts["tool_result_received"] += 1
    return counts


def rep_directories(run_dir: Path) -> list[Path]:
    """Return arm/app/rep directories, without assuming a specific arm name."""
    return sorted(path for path in run_dir.glob("*/*/rep*") if path.is_dir())


def label_run(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    totals: defaultdict[str, int] = defaultdict(int)

    for rep_dir in rep_directories(run_dir):
        relative = rep_dir.relative_to(run_dir)
        arm, app, rep = relative.parts
        primary_logs = sorted(
            path for path in rep_dir.glob("*-eval.log") if "retry_logs" not in path.parts
        )
        counts: defaultdict[str, int] = defaultdict(int)
        bash_logs: list[str] = []
        for log_path in primary_logs:
            log_counts = audit_log(log_path)
            for key, value in log_counts.items():
                counts[key] += value
            if log_counts["bash_requests"]:
                bash_logs.append(log_path.name)

        contaminated = bool(counts["bash_requests"])
        row: dict[str, Any] = {
            "arm": arm,
            "app": app,
            "rep": rep,
            "label": "contaminated" if contaminated else "clean",
            "primary_log_count": len(primary_logs),
            "bash_log_count": len(bash_logs),
            "bash_request_count": counts["bash_requests"],
            "permission_denied_count": counts["permission_denied"],
            "tool_result_received_count": counts["tool_result_received"],
            "unclassified_bash_count": counts["unclassified"],
            "bash_logs": bash_logs,
        }
        rows.append(row)
        totals["reps"] += 1
        totals["contaminated_reps"] += int(contaminated)
        totals["primary_logs"] += len(primary_logs)
        totals["bash_logs"] += len(bash_logs)
        for key, value in counts.items():
            totals[key] += value

    summary = dict(totals)
    summary["contamination_rate_percent"] = (
        round(100 * summary["contaminated_reps"] / summary["reps"], 1)
        if summary["reps"]
        else 0.0
    )
    return rows, summary


def write_outputs(output_dir: Path, run_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "bash_contamination_reps.csv"
    json_path = output_dir / "bash_contamination_manifest.json"
    fields = [key for key in rows[0] if key != "bash_logs"] if rows else []
    fields.append("bash_logs")
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            csv_row = dict(row)
            csv_row["bash_logs"] = ";".join(row["bash_logs"])
            writer.writerow(csv_row)
    json_path.write_text(
        json.dumps({"run_dir": str(run_dir), "summary": summary, "reps": rows}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"CSV: {csv_path}")
    print(f"Manifest: {json_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="experiments/runs/<run-id> directory")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="directory for the CSV and JSON outputs (default: run_dir)",
    )
    args = parser.parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        parser.error(f"run_dir does not exist: {run_dir}")

    rows, summary = label_run(run_dir)
    output_dir = (args.output_dir or run_dir).expanduser().resolve()
    write_outputs(output_dir, run_dir, rows, summary)
    print(
        f"Contaminated reps: {summary['contaminated_reps']}/{summary['reps']} "
        f"({summary['contamination_rate_percent']:.1f}%); "
        f"Bash requests: {summary['bash_requests']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
