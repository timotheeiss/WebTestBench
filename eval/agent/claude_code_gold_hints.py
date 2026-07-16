import os
import time
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from claude_agent_sdk import ClaudeAgentOptions

from agent.claude_code_gold import ClaudeCodeWebTester_Gold
from tools import PlaywrightTools
from utils import *


# Semantic-hints MCP tools, namespaced by the MCP server key below.
SEMANTIC_HINTS_SERVER = "semantic_hints"
SemanticHintsTools = [
    f"mcp__{SEMANTIC_HINTS_SERVER}__semantic_snapshot",
    f"mcp__{SEMANTIC_HINTS_SERVER}__semantic_observe",
]


class ClaudeCodeWebTester_GoldHints(ClaudeCodeWebTester_Gold):
    """
    Gold-checklist defect-detection tester that adds the semantic-hints MCP.

    Differences from the baseline `ClaudeCodeWebTester_Gold`:
      1. Uses the hints-aware defect-detection prompt.
      2. Runs both MCPs against ONE shared Chromium so semantic observations and
         Playwright actions hit the same page. We launch that browser ourselves
         with a CDP endpoint; the Playwright MCP attaches via `--cdp-endpoint`
         and the semantic-hints MCP via `SEMANTIC_HINTS_CDP_URL`.
      3. Allows the two semantic-hints tools in addition to the Playwright tools.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.defect_prompt_key = "defect_detection_based_gold_with_hints"

        # Location of the built semantic-hints MCP (override with SEMANTIC_HINTS_MCP_DIR).
        default_mcp_dir = Path(__file__).resolve().parents[3] / "semantic-hints-mcp"
        self.mcp_dir = Path(os.environ.get("SEMANTIC_HINTS_MCP_DIR", str(default_mcp_dir)))
        self.mcp_entry = self.mcp_dir / "dist" / "index.js"
        self.cdp_launcher = self.mcp_dir / "scripts" / "launch-cdp-browser.mjs"

        # Shared browser: CDP port derived from the app port, kept clear of app ports.
        server_port = urlparse(self.server_url).port or 0
        offset = int(os.environ.get("SEMANTIC_HINTS_CDP_PORT_OFFSET", "20000"))
        self._cdp_port = (server_port + offset) % 65535 or 29222
        self._cdp_endpoint = f"http://127.0.0.1:{self._cdp_port}"
        # Default to a visible (headed) browser so the run can be watched on screen.
        # Set SEMANTIC_HINTS_HEADLESS=true to force headless (e.g. on a display-less host).
        self._headless = (os.environ.get("SEMANTIC_HINTS_HEADLESS", "false").lower() == "true")
        self._cdp_proc: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------ #
    # Shared CDP browser lifecycle
    # ------------------------------------------------------------------ #

    def _wait_cdp_ready(self, timeout_s: float = 30.0) -> bool:
        deadline = time.time() + timeout_s
        url = f"{self._cdp_endpoint}/json/version"
        while time.time() < deadline:
            if self._cdp_proc and self._cdp_proc.poll() is not None:
                return False  # launcher exited early
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                time.sleep(0.2)
        return False

    def _start_shared_browser(self) -> bool:
        if not self.mcp_entry.exists():
            self._mark_stage(
                stage="defect_detection", status="error",
                message=(
                    f"semantic-hints MCP not built at {self.mcp_entry}. "
                    "Run `npm install && npm run build` in the semantic-hints-mcp folder."
                ),
            )
            return False
        if not self.cdp_launcher.exists():
            self._mark_stage(
                stage="defect_detection", status="error",
                message=f"CDP launcher not found at {self.cdp_launcher}.",
            )
            return False

        # Free a stale CDP port from a previous crashed run, if any.
        self._kill_exist_port(self._cdp_port, stage="defect_detection")

        args = ["node", str(self.cdp_launcher), "--port", str(self._cdp_port)]
        if self._headless:
            args.append("--headless")

        self._mark_stage(
            stage="defect_detection", status="running",
            message=f"🌐 Launching shared CDP browser on {self._cdp_endpoint} (headless={self._headless}) ...",
        )
        self._cdp_proc = subprocess.Popen(
            args, cwd=str(self.mcp_dir),
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )

        if not self._wait_cdp_ready():
            self._mark_stage(
                stage="defect_detection", status="error",
                message=f"Shared CDP browser did not become ready at {self._cdp_endpoint}.",
            )
            self._stop_shared_browser()
            return False

        print_green(f"✅ Shared CDP browser ready at {self._cdp_endpoint}.")
        return True

    def _stop_shared_browser(self) -> None:
        proc = self._cdp_proc
        self._cdp_proc = None
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        # Safety net in case the launcher left the port bound.
        self._kill_exist_port(self._cdp_port, stage="defect_detection")

    # ------------------------------------------------------------------ #
    # Stage override
    # ------------------------------------------------------------------ #

    async def defect_detection(self) -> bool:
        # Resumed runs already have a result — let the parent skip without a browser.
        if self.result_path.exists():
            return await super().defect_detection()

        if not self._start_shared_browser():
            return False
        try:
            return await super().defect_detection()
        finally:
            self._stop_shared_browser()

    # ------------------------------------------------------------------ #
    # Agent configuration
    # ------------------------------------------------------------------ #

    def _get_browser_agent_options(
        self,
        system_prompt: Optional[str] = None,
        max_turns: int = 5,
        max_buffer_size: int = 1024 * 1024,
    ) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={
                # Playwright MCP attaches to the shared browser instead of launching its own.
                "playwright": {
                    "type": "stdio",
                    "command": "npx",
                    "args": [
                        "-y", "@playwright/mcp@0.0.76",
                        "--cdp-endpoint", self._cdp_endpoint,
                        "--viewport-size", "1280,720",
                    ],
                },
                # Semantic-hints MCP reads data-agent-* hints from the SAME browser.
                SEMANTIC_HINTS_SERVER: {
                    "type": "stdio",
                    "command": "node",
                    "args": [str(self.mcp_entry)],
                    "env": {
                        "SEMANTIC_HINTS_CDP_URL": self._cdp_endpoint,
                        "SEMANTIC_HINTS_TARGET_URL": self.server_url,
                    },
                },
            },
            allowed_tools=PlaywrightTools + SemanticHintsTools,
            disallowed_tools=[
                "mcp__playwright__browser_take_screenshot",
            ],
            model=self.api_config.model,
            max_turns=max_turns,
            max_buffer_size=max_buffer_size,
            cwd=self.cwd_dir,
            env=self.api_config.agent_env(),
        )
