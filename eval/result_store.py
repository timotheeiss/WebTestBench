"""Compaction-safe structured checklist result recording.

The browser agent records each verdict through an in-process MCP tool. Every
call is appended and fsynced before the tool returns, so completed findings do
not depend on conversation memory or final Markdown formatting.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool


STRUCTURED_RESULTS_SERVER = "structured_results"
RECORD_RESULT_TOOL = f"mcp__{STRUCTURED_RESULTS_SERVER}__record_result"
GET_RESULT_PROGRESS_TOOL = f"mcp__{STRUCTURED_RESULTS_SERVER}__get_result_progress"
STRUCTURED_RESULT_TOOLS = [RECORD_RESULT_TOOL, GET_RESULT_PROGRESS_TOOL]


@dataclass(frozen=True)
class ChecklistItem:
    item_id: str
    description: str
    section: str


class StructuredResultStore:
    """Append-only verdict store backed by ``result_events.jsonl``."""

    _ITEM_RE = re.compile(
        r"^\s*-\s*\[\s*\]\s+((?:FT|CS|IX|CT)-\d+)\s*:\s*(.+?)\s*$",
        re.IGNORECASE,
    )
    _SECTION_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")

    def __init__(self, checklist_path: str | Path, events_path: str | Path):
        self.checklist_path = Path(checklist_path)
        self.events_path = Path(events_path)
        self._lock = threading.Lock()

    def delete_events(self) -> bool:
        """Delete persisted verdicts so the next attempt starts from scratch.

        Returns ``True`` when an event file existed and was removed. Missing
        files are treated as an already-clean store.
        """
        with self._lock:
            try:
                self.events_path.unlink()
            except FileNotFoundError:
                return False
        return True

    def checklist_items(self) -> list[ChecklistItem]:
        if not self.checklist_path.exists():
            return []

        section = "Checklist"
        items: list[ChecklistItem] = []
        for line in self.checklist_path.read_text(encoding="utf-8").splitlines():
            heading = self._SECTION_RE.match(line)
            if heading and not line.lstrip().startswith("# Test Checklist"):
                section = heading.group(1).strip()
                continue
            match = self._ITEM_RE.match(line)
            if match:
                items.append(
                    ChecklistItem(
                        item_id=match.group(1).upper(),
                        description=match.group(2).strip(),
                        section=section,
                    )
                )
        return items

    @staticmethod
    def _checklist_fingerprint(items: list[ChecklistItem]) -> str:
        canonical = [
            {"id": item.item_id, "description": item.description, "section": item.section}
            for item in items
        ]
        encoded = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _latest_results(
        self, items: list[ChecklistItem]
    ) -> dict[str, dict[str, Any]]:
        if not self.events_path.exists():
            return {}

        fingerprint = self._checklist_fingerprint(items)
        valid_ids = {item.item_id for item in items}
        latest: dict[str, dict[str, Any]] = {}
        with self.events_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # A killed process can leave one truncated final line;
                    # earlier fsynced events remain valid.
                    continue
                item_id = str(event.get("item_id", "")).upper()
                if (
                    event.get("checklist_fingerprint") == fingerprint
                    and item_id in valid_ids
                    and event.get("verdict") in ("PASS", "FAIL")
                ):
                    latest[item_id] = event
        return latest

    def record_result(
        self,
        *,
        item_id: str,
        verdict: str,
        issue: str = "",
        actual: str = "",
        evidence: str = "",
    ) -> dict[str, Any]:
        items = self.checklist_items()
        if not items:
            raise ValueError("checklist.md is missing or contains no checklist items")

        item_id = str(item_id).strip().upper()
        verdict = str(verdict).strip().upper()
        issue = str(issue or "").strip()
        actual = str(actual or "").strip()
        evidence = str(evidence or "").strip()
        valid_ids = {item.item_id for item in items}

        if item_id not in valid_ids:
            raise ValueError(
                f"unknown checklist item {item_id!r}; expected one of: "
                + ", ".join(item.item_id for item in items)
            )
        if verdict not in ("PASS", "FAIL"):
            raise ValueError("verdict must be PASS or FAIL")
        if verdict == "FAIL" and (not issue or not actual):
            raise ValueError("FAIL requires non-empty issue and actual fields")
        if verdict == "PASS" and (issue or actual):
            raise ValueError("PASS must not include issue or actual fields")

        event = {
            "version": 1,
            "checklist_fingerprint": self._checklist_fingerprint(items),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "item_id": item_id,
            "verdict": verdict,
            "issue": issue or None,
            "actual": actual or None,
            "evidence": evidence or None,
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            progress = self.progress()

        return {"recorded": item_id, "verdict": verdict, **progress}

    def progress(self) -> dict[str, Any]:
        items = self.checklist_items()
        latest = self._latest_results(items)
        missing = [item.item_id for item in items if item.item_id not in latest]
        return {
            "recorded_count": len(latest),
            "total_count": len(items),
            "missing_ids": missing,
            "complete": bool(items) and not missing,
        }

    def render_markdown(self) -> str:
        """Render currently recorded verdicts in canonical checklist order."""
        items = self.checklist_items()
        latest = self._latest_results(items)
        lines = ["# Test Result", ""]
        current_section: str | None = None

        for item in items:
            result = latest.get(item.item_id)
            if result is None:
                continue
            if item.section != current_section:
                if current_section is not None:
                    lines.append("")
                lines.append(f"## {item.section}")
                current_section = item.section

            checked = "X" if result["verdict"] == "PASS" else " "
            lines.append(f"- [{checked}] {item.item_id}: {item.description}")
            if result["verdict"] == "FAIL":
                lines.extend(
                    [
                        "  - Bug Report:",
                        f"    - Issue: {result['issue']}",
                        f"    - Actual: {result['actual']}",
                    ]
                )
            lines.append("")

        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"

    def create_mcp_server(self):
        store = self

        @tool(
            "record_result",
            "Persist one completed checklist verdict immediately. Call this once "
            "as soon as each checklist item has been verified. Re-recording an "
            "item replaces its effective verdict while preserving audit history.",
            {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Checklist ID such as FT-01 or CS-03.",
                    },
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                    "issue": {
                        "type": "string",
                        "description": "Required for FAIL; concise problem type.",
                    },
                    "actual": {
                        "type": "string",
                        "description": "Required for FAIL; observed deviation.",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "Optional compact observation supporting the verdict.",
                    },
                },
                "required": ["item_id", "verdict"],
                "additionalProperties": False,
            },
        )
        async def record_result(args):
            try:
                result = store.record_result(
                    item_id=args.get("item_id", ""),
                    verdict=args.get("verdict", ""),
                    issue=args.get("issue", ""),
                    actual=args.get("actual", ""),
                    evidence=args.get("evidence", ""),
                )
            except ValueError as exc:
                return {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "is_error": True,
                }
            return {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                ]
            }

        @tool(
            "get_result_progress",
            "Read persisted checklist progress after context compaction or before "
            "finishing. Returns the latest verdict for every recorded item.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        async def get_result_progress(_args):
            items = store.checklist_items()
            latest = store._latest_results(items)
            payload = store.progress()
            payload["results"] = [
                {
                    "item_id": item.item_id,
                    "verdict": latest[item.item_id]["verdict"],
                    "issue": latest[item.item_id].get("issue"),
                    "actual": latest[item.item_id].get("actual"),
                    "evidence": latest[item.item_id].get("evidence"),
                }
                for item in items
                if item.item_id in latest
            ]
            return {
                "content": [
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                ]
            }

        return create_sdk_mcp_server(
            name=STRUCTURED_RESULTS_SERVER,
            version="1.0.0",
            tools=[record_result, get_result_progress],
        )
