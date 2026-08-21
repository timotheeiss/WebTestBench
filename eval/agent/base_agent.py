import os
import copy
import json
import re
import sys
import time
import subprocess
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Literal

from utils import *


# Vars that route the spawned Claude Code CLI at a third-party gateway. In
# subscription mode every one of these must be absent from the CLI's
# environment, or it authenticates against the gateway instead of falling back
# to the stored OAuth credentials.
ANTHROPIC_ROUTING_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)

AuthMode = Literal["api", "subscription"]


# Headless is ONE knob for BOTH A/B arms. The baseline arm launches Chromium via
# the Playwright MCP and the hints arm via the shared CDP launcher, so the two
# have to read the same variable or the conditions can silently diverge on
# browser mode — which is exactly what happened while this was named
# SEMANTIC_HINTS_HEADLESS and only reached the hints arm.
#
# Default true: it is what upstream WebTestBench ran, and the only mode a
# display-less host supports. Set BROWSER_HEADLESS=false to watch a run locally.
def browser_headless() -> bool:
    """Whether both conditions should run their browser headless."""
    return os.environ.get("BROWSER_HEADLESS", "true").strip().lower() not in ("false", "0", "no")


@dataclass
class APIConfig:
    model: str
    auth_mode: AuthMode = "api"
    base_url: Optional[str] = None
    api_key: Optional[str] = None

    def __post_init__(self) -> None:
        if self.auth_mode == "api" and not (self.base_url and self.api_key):
            raise ValueError("auth_mode='api' requires both base_url and api_key.")

    def agent_env(self) -> Dict[str, str]:
        """Env overrides for the Claude Code CLI the Agent SDK spawns.

        Subscription mode returns {} and relies on scrub_routing_env() having
        cleared the routing vars from this process: the SDK merges these
        overrides *over* the inherited environment, so it cannot unset a var.
        """
        if self.auth_mode == "subscription":
            return {}
        return {
            "ANTHROPIC_BASE_URL": self.base_url or "",
            "ANTHROPIC_AUTH_TOKEN": self.api_key or "",
            "ANTHROPIC_API_KEY": "",
        }


def scrub_routing_env() -> None:
    """Drop gateway routing vars so the CLI falls back to subscription OAuth."""
    for var in ANTHROPIC_ROUTING_VARS:
        os.environ.pop(var, None)


StageStatus = Literal["running", "complete", "skip", "error"]


@dataclass
class ChecklistResultItem:
    """One required checklist item, as defined by checklist.md."""

    item_id: str
    description: str
    section: str


@dataclass
class ResultParseResult:
    """The outcome of validating an agent's final checklist report."""

    canonical_result: str = ""
    raw_content: str = ""
    missing_ids: list[str] | None = None
    duplicate_ids: list[str] | None = None
    unknown_ids: list[str] | None = None
    emitted_count: int = 0

    @property
    def is_valid(self) -> bool:
        return bool(self.canonical_result)

    @property
    def failure_kind(self) -> str:
        if self.duplicate_ids:
            return "duplicate_ids"
        if self.unknown_ids:
            return "unknown_ids"
        if self.missing_ids:
            return "missing_ids"
        return "no_checklist_outcomes"


class BaseAgent:

    def __init__(
        self,
        instruction: str,
        api_config: APIConfig,
        output_dir: str | Path,
        server_url: str,
        local_project_dir: str | Path,
        event_log_stream: Optional[Any] = None,
    ) -> None:
        self.instruction = instruction
        self.api_config = api_config
        self.server_url = server_url
        self.local_project_dir = local_project_dir

        self.event_log_stream = event_log_stream

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.current_stage: Optional[str] = None

        self.checklist_path = self.output_dir / "checklist.md"
        # Single result artifact: result.md holds the extracted "# Test Result"
        # checklist (the detection stage trims the agent's raw output to that
        # section before writing). The raw transcript, if ever needed, lives in
        # the co-located <ts>-eval.log.
        self.result_path = self.output_dir / "result.md"
        self.raw_result_path = self.output_dir / "raw_result.md"
        self.result_failure_path = self.output_dir / "result_failure.json"
        self.session_meta_path = self.output_dir / "session_meta.json"

    @staticmethod
    def _is_test_result_heading(line: str) -> bool:
        """Return whether *line* is a Markdown heading beginning "Test Result"."""
        stripped = line.strip()
        return (
            stripped.startswith("#")
            and stripped.lstrip("#").strip().startswith("Test Result")
        )

    def _extract_test_result_section(self, content: str) -> str:
        """Compatibility extractor for callers that still rely on a title.

        The actual defect-result path uses ``_normalise_defect_result`` below,
        which validates checklist IDs rather than relying on a report heading.
        Keep this helper permissive and consistent with ``_has_required_result``
        for older callers.
        """
        lines = content.splitlines()
        start_idx = None
        for i, line in enumerate(lines):
            if self._is_test_result_heading(line):
                start_idx = i
                break
        if start_idx is None:
            return ""

        extracted_lines = lines[start_idx:]
        last_item_idx = None
        for i, line in enumerate(extracted_lines):
            if line.lstrip().startswith("- [") and "]" in line:
                last_item_idx = i

        if last_item_idx is None:
            trimmed = "\n".join(extracted_lines).strip()
            return f"{trimmed}\n" if trimmed else ""

        end_idx = last_item_idx + 1
        while end_idx < len(extracted_lines):
            line = extracted_lines[end_idx]
            if line.strip() == "" or line[0].isspace():
                end_idx += 1
                continue
            break

        trimmed = "\n".join(extracted_lines[:end_idx]).strip()
        return f"{trimmed}\n" if trimmed else ""

    _CHECKLIST_ITEM_RE = re.compile(
        r"^\s*-\s*\[\s*\]\s+((?:FT|CS|IX|CT)-\d+)\s*:\s*(.+?)\s*$",
        re.IGNORECASE,
    )
    _RESULT_ITEM_RE = re.compile(
        r"^\s*-\s*\[\s*([Xx ])\s*\]\s+"
        r"(?:\*\*)?((?:FT|CS|IX|CT)-\d+)(?:\*\*)?"
        r"(?:\s*(?::|—|–|-)\s*|\s+)(.*\S)?\s*$",
        re.IGNORECASE,
    )
    _SECTION_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")

    def _required_checklist_items(self) -> list[ChecklistResultItem]:
        """Read the expected IDs, descriptions, and sections from checklist.md."""
        if not self.checklist_path.exists():
            return []

        section = "Checklist"
        items: list[ChecklistResultItem] = []
        for line in self.checklist_path.read_text(encoding="utf-8").splitlines():
            heading = self._SECTION_RE.match(line)
            if heading and not line.lstrip().startswith("# Test Checklist"):
                section = heading.group(1).strip()
                continue
            item = self._CHECKLIST_ITEM_RE.match(line)
            if item:
                items.append(
                    ChecklistResultItem(
                        item_id=item.group(1).upper(),
                        description=item.group(2).strip(),
                        section=section,
                    )
                )
        return items

    def _parse_result_report(self, content: str | None) -> ResultParseResult:
        """Validate report outcomes and return a canonical scorer-ready result.

        A report title is presentation, not data.  The data contract is one
        checked or unchecked outcome for every ID in the generated checklist.
        This deliberately accepts title variations and reports that start
        directly at a section heading, while rejecting missing, duplicate, and
        unknown IDs.
        """
        raw_content = content or ""
        expected_items = self._required_checklist_items()
        if not raw_content.strip() or not expected_items:
            return ResultParseResult(
                raw_content=raw_content,
                missing_ids=[item.item_id for item in expected_items],
            )

        expected_by_id = {item.item_id: item for item in expected_items}
        occurrences: dict[str, list[tuple[str, list[str]]]] = {}
        unknown_ids: list[str] = []
        lines = raw_content.splitlines()

        matches: list[tuple[int, re.Match[str]]] = []
        for index, line in enumerate(lines):
            match = self._RESULT_ITEM_RE.match(line)
            if match:
                matches.append((index, match))

        for match_index, (line_index, match) in enumerate(matches):
            item_id = match.group(2).upper()
            if item_id not in expected_by_id:
                unknown_ids.append(item_id)
                continue

            next_line_index = (
                matches[match_index + 1][0]
                if match_index + 1 < len(matches)
                else len(lines)
            )
            details: list[str] = []
            for detail_line in lines[line_index + 1:next_line_index]:
                # A report heading separates sections; it is not part of the
                # preceding item's bug report.
                if self._SECTION_RE.match(detail_line):
                    break
                if (
                    detail_line.strip()
                    or detail_line.startswith((" ", "\t"))
                ):
                    details.append(detail_line.rstrip())
            occurrences.setdefault(item_id, []).append(
                ("X" if match.group(1).strip().upper() == "X" else " ", details)
            )

        duplicate_ids = sorted(item_id for item_id, values in occurrences.items() if len(values) > 1)
        missing_ids = [item.item_id for item in expected_items if item.item_id not in occurrences]
        result = ResultParseResult(
            raw_content=raw_content,
            missing_ids=missing_ids,
            duplicate_ids=duplicate_ids,
            unknown_ids=sorted(set(unknown_ids)),
            emitted_count=len(occurrences),
        )
        if missing_ids or duplicate_ids or unknown_ids:
            return result

        result.canonical_result = self._render_canonical_result(expected_items, occurrences)
        return result

    @staticmethod
    def _render_canonical_result(
        expected_items: list[ChecklistResultItem],
        occurrences: dict[str, list[tuple[str, list[str]]]],
    ) -> str:
        """Render valid outcome data in the one format scoring expects."""
        lines = ["# Test Result", ""]
        current_section: str | None = None
        for item in expected_items:
            if item.section != current_section:
                if current_section is not None:
                    lines.append("")
                lines.extend([f"## {item.section}"])
                current_section = item.section

            status, details = occurrences[item.item_id][0]
            lines.append(f"- [{status}] {item.item_id}: {item.description}")
            if details:
                lines.extend(details)
            lines.append("")

        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"

    def _normalise_defect_result(self, result_text: str | None) -> tuple[ResultParseResult, bool]:
        """Find and normalise a complete checklist from terminal or assistant text.

        Claude Code sometimes returns an empty terminal ``ResultMessage`` after
        it has already emitted the report as an assistant text block.  Search
        both sources and prefer the terminal result when both are valid.
        """
        candidates: list[tuple[str, bool]] = [(result_text or "", True)]
        recent_blocks = getattr(self, "recent_assistant_text_blocks", {}).get(
            "defect_detection", []
        )
        candidates.extend((candidate, False) for candidate in reversed(recent_blocks))

        best_result: ResultParseResult | None = None
        best_source = True
        seen: set[str] = set()
        for candidate, from_result_message in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            parsed = self._parse_result_report(candidate)
            if parsed.is_valid:
                return parsed, from_result_message
            if best_result is None or parsed.emitted_count > best_result.emitted_count:
                best_result = parsed
                best_source = from_result_message

        return best_result or ResultParseResult(), best_source

    def _record_invalid_result(self, parsed: ResultParseResult) -> None:
        """Persist recoverable evidence and a machine-readable rejection reason."""
        if parsed.raw_content.strip():
            self.raw_result_path.write_text(parsed.raw_content.rstrip() + "\n", encoding="utf-8")
        self.result_failure_path.write_text(
            json.dumps(
                {
                    "failure_kind": "incomplete_result",
                    "reason": parsed.failure_kind,
                    "missing_ids": parsed.missing_ids or [],
                    "duplicate_ids": parsed.duplicate_ids or [],
                    "unknown_ids": parsed.unknown_ids or [],
                    "emitted_count": parsed.emitted_count,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    # Server Deployment
    # ------------------------------------------------------------------ #

    async def server_deploy(self):
        """
        Ensure project dev server is up.

        Rules:
        - Kill previous dev server on port 5173.
        - If `dev_server.log` already exists and curl succeeds, we skip.
        - Otherwise install deps and run dev server in background.
        """
        stage = "server_deploy"
        if not self.server_url.startswith('http://localhost'):
            self._mark_stage(stage=stage, status="skip", message=f"⏭️ Skipping {stage}: server_url is an online webpage, skipping server deployment: {self.server_url}")
            return True

        if not self.local_project_dir:
            self._mark_stage(stage=stage, status="error", message=f"{stage}: local_project_dir is not set.")
            return False
        
        self._mark_stage(stage=stage, status="running", message="🚀 Starting Server Deployment ...")
        
        # Extract port from server_url
        parsed_url = urlparse(self.server_url)
        port = parsed_url.port

        self._kill_exist_port(port)
        self._deploy_local_server(port)

        return True

    def _kill_exist_port(self, port: int, stage: str = "server_deploy") -> None:
        """Kill old dev server process on the given port if it exist."""
        self._mark_stage(stage=stage, message=f"🧹 Checking old dev server on port {port} ...")
        try:
            # Kill the process running on the extracted port
            result = subprocess.run(
                f"lsof -ti:{port} | xargs kill -9",
                shell=True, capture_output=True, text=True,
            )
            if result.returncode == 0:
                self._mark_stage(stage=stage, message=f"🧹 Killed process on port {port}.")
            else:
                self._mark_stage(stage=stage, message=f"✅ No process found on port {port}.")
                pass  # It's okay if nothing was running on the port
        except Exception as e:
            self._mark_stage(stage=stage, status="error", message=f"Failed to kill old server process: {e}")
    
    def _deploy_local_server(self, port: int, stage: str = "server_deploy"):
        """Start dev server in background on the given port and wait until it responds."""
        project_dir = Path(self.local_project_dir)
        if not project_dir.exists():
            raise FileNotFoundError(f"local_project_dir not found: {project_dir}")
        
        self._mark_stage(stage=stage, message=f"📦 Installing dependencies (npm install) in {project_dir} ...")
        subprocess.run(["npm", "install"], cwd=str(project_dir), check=True)

        log_path = self.output_dir / "dev_server.log"
        self._mark_stage(stage=stage, message=f"🚀 Starting dev server on port {port} (log: {log_path}) ...")

        self._dev_server_log_handle = open(log_path, "w", encoding="utf-8")
        self.dev_server_process = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(port)],
            cwd=str(project_dir), stdout=self._dev_server_log_handle, stderr=subprocess.STDOUT, preexec_fn=os.setsid,
        )
        
        # step 3: wait for server to respond
        print("⏳ Waiting for server to start...")
        time.sleep(20)
        for _ in range(60):  # 60 sec
            time.sleep(1)
            try:
                response = subprocess.run(
                    ["curl", "-s", self.server_url],
                    capture_output=True, timeout=2
                )
                if response.returncode == 0:
                    self._mark_stage(stage=stage, status="complete", message=f"✅ Server is ready at {self.server_url}")
                    self._mark_stage(stage=stage, message=f"✅ Dev server started (PID: {self.dev_server_process.pid})")
                    return True
            except:
                continue
        
        raise RuntimeError(f"Dev server failed to start within 60s. See log: {log_path}")

    def kill_local_server(self) -> None:
        """Cleanup for local dev server."""
        if not self.server_url.startswith("http://localhost"):
            return

        self._kill_exist_port(urlparse(self.server_url).port, stage="server_cleanup")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _log_instruction(self) -> None:
        """Persist and display the input instruction for traceability."""
        print("=" * 20, "Instruction", "=" * 20)
        print(self.instruction)
        print("=" * 50)
    
    def _should_skip_stage(self, file_path: Path, stage: str) -> bool:
        """Skip a stage if its output file already exists."""
        if file_path.exists():
            self._mark_stage(stage=stage, status="skip", message=f"⏭️ Skipping {stage}: output already exists at {file_path}.")
            self._emit_file_event(stage, file_path)
            return True
        return False

    def _handle_message(self, message, stage: str):
        """Emit structured events while streaming assistant messages and tool usage."""
        pass

    def _mark_stage(self, stage: str, status: Optional[StageStatus] = None, message: Optional[str] = None) -> None:
        """Emit structured stage updates."""
        self.current_stage = stage
        if status is None:
            status = "running"

        if message:
            print_red(message) if status == "error" else print(message)

        self._emit_event(type_name="stage_status", stage=stage, status=status, message=message)
    
    def _emit_event(self, type_name: str, stage: str, status: Optional[StageStatus] = None,
                    message: Optional[str] = None, payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a single structured event.
        {
            "type": <type name>,
            "stage": <stage name>,
            "status": <optional status string>,
            "message": <optional human readable string>,
            "payload": <optional structured data dict>
        }
        - Console: truncated content for readability (written directly to the real stdout).
        - Log file: full content (appended to `event_log_path`).
        """
        event_stage = stage or self.current_stage
        base_event: Dict[str, Any] = {
            "type": type_name, "stage": event_stage, "status": status, "message": message, "payload": payload
        }

        if status == "error":
            self._write_stage_success(stage, False)
        else:
            self._write_stage_success(stage, True)

        display_event = self._to_display_event(base_event)

        # Console-friendly event (bypass Tee to avoid duplicating truncated content in logs)
        try:
            sys.__stdout__.write(f"__EVENT__ {json.dumps(display_event, ensure_ascii=False)}\n")
            sys.__stdout__.flush()
        except Exception as exc:
            print_red(f"Failed to encode event {display_event}: {exc}")

        # Full event log for later debugging
        try:
            self.event_log_stream.write("__EVENT__ " + json.dumps(base_event, ensure_ascii=False, indent=2) + "\n")
            self.event_log_stream.flush()
        except Exception as exc:
            print_red(f"Failed to write full event: {exc}")
    
    def _to_display_event(self, event: Dict[str, Any], limit: int = 200) -> Dict[str, Any]:
        """Return a console-friendly copy of the event with long strings truncated."""

        def truncate(value: Any) -> Any:
            if isinstance(value, str) and len(value) > limit:
                truncated = value[:limit]
                truncated = truncated.rsplit(" ", 1)[0] or truncated
                return f"{truncated} ... (truncated)"
            if isinstance(value, list):
                return [truncate(item) for item in value]
            if isinstance(value, dict):
                return {k: truncate(v) for k, v in value.items()}
            return value

        return truncate(copy.deepcopy(event))

    def _emit_file_event(self, stage: str, path: Path) -> None:
        """Emit a file event with a predictable payload."""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            self._emit_event(
                type_name="file_generate", stage=stage, status="error",
                message=f"Unable to read generated file {path}: {exc}",
            )
            return

        self._emit_event(
            type_name="file_generate", stage=stage, status="complete",
            payload={"file": {"name": path.name, "path": str(path), "content": content}},
        )

    def _write_stage_success(self, stage: str, success: bool) -> None:
        session_meta: Dict[str, Any] = {}
        if self.session_meta_path.exists():
            try:
                session_meta = json.loads(self.session_meta_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print_red(f"Failed to read session meta from {self.session_meta_path}: {exc}")
        if not isinstance(session_meta, dict):
            session_meta = {}

        stage_success = session_meta.get("stage_success")
        if not isinstance(stage_success, dict):
            stage_success = {}
        current_value = stage_success.get(stage)
        if isinstance(current_value, bool):
            stage_success[stage] = current_value and bool(success)
        else:
            stage_success[stage] = bool(success)
        session_meta["stage_success"] = stage_success
        try:
            self.session_meta_path.write_text(
                json.dumps(session_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            print_red(f"Failed to write session meta to {self.session_meta_path}: {exc}")

    def _verify_output_file(self, file_path: Path) -> bool:
        """Verify that an output file exists and is non-empty."""
        if not file_path.exists():
            return False
        
        try:
            size = file_path.stat().st_size
            return size > 0
        except Exception as e:
            print_red(f"Error verifying file {file_path}: {e}")
            return False
    
    def _load_file_content(self, file_path: Path) -> str:
        """Load the complete content of a file."""
        return file_path.read_text(encoding="utf-8")
        
    def _load_file_until_marker(self, filepath: Path, marker: str) -> str:
        """Load file content up to a marker line (exclusive). Returns full file if the marker is missing."""
        if not filepath.exists():
            print_red(f"⚠️  File not found: {filepath}")
            return ""
        
        content_lines = []
        with filepath.open('r', encoding='utf-8') as f:
            for line in f:
                if line.strip().startswith(marker):
                    break
                content_lines.append(line)
        
        return ''.join(content_lines)
    
    def write_markdown(self, path: Path, text: str) -> None:
        """Persist markdown content, unwrapping ```markdown fences when present."""
        m = re.search(r"```(?:markdown|md)\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        content = m.group(1).strip() if m else text.strip()
        path.write_text(content, encoding="utf-8")

    def _has_required_checklist(self, content: str | None) -> bool:
        if content:
            for line in content.splitlines():
                if line.strip().startswith("# Test Checklist"):
                    return True
        return False
    
    def _has_required_result(self, content: str | None) -> bool:
        # Keep a lightweight, title-compatible predicate for legacy callers.
        # The current Gold defect-detection path uses _normalise_defect_result,
        # which validates the actual checklist IDs instead.
        return bool(content) and any(
            self._is_test_result_heading(line) for line in content.splitlines()
        )
    
    
