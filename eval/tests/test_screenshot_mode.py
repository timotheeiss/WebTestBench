import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

EVAL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = EVAL_DIR.parent / "scripts"
for path in (str(EVAL_DIR), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from agent import APIConfig
from agent.claude_code_gold import (
    SCREENSHOT_MAX_BUFFER_SIZE,
    ClaudeCodeWebTester_Gold,
    image_safe_content,
)
from agent.claude_code_gold_hints import (
    SemanticHintsTools,
    ClaudeCodeWebTester_GoldHints,
)
from dump_agent_view import text_of
from prompt import USER_PROMPT
from run_agent import parse_args
from tools import (
    EXPERIMENT_BUILTIN_TOOLS,
    FORBIDDEN_EXPERIMENT_TOOLS,
    PLAYWRIGHT_CLIPBOARD_PERMISSIONS,
    PLAYWRIGHT_MCP_PACKAGE,
    PLAYWRIGHT_SCREENSHOT_TOOL,
    PLAYWRIGHT_UNSAFE_CODE_TOOL,
    playwright_tools,
)
from result_store import STRUCTURED_RESULTS_SERVER, STRUCTURED_RESULT_TOOLS


class ScreenshotModeTests(unittest.TestCase):
    SAFE_PLAYWRIGHT_0_0_76_TOOLS = {
        "mcp__playwright__browser_click",
        "mcp__playwright__browser_close",
        "mcp__playwright__browser_console_messages",
        "mcp__playwright__browser_drag",
        "mcp__playwright__browser_drop",
        "mcp__playwright__browser_evaluate",
        "mcp__playwright__browser_file_upload",
        "mcp__playwright__browser_fill_form",
        "mcp__playwright__browser_handle_dialog",
        "mcp__playwright__browser_hover",
        "mcp__playwright__browser_navigate",
        "mcp__playwright__browser_navigate_back",
        "mcp__playwright__browser_network_request",
        "mcp__playwright__browser_network_requests",
        "mcp__playwright__browser_press_key",
        "mcp__playwright__browser_resize",
        "mcp__playwright__browser_select_option",
        "mcp__playwright__browser_snapshot",
        "mcp__playwright__browser_tabs",
        "mcp__playwright__browser_type",
        "mcp__playwright__browser_wait_for",
    }

    def make_agent(self, cls, output_dir: Path, enabled: bool):
        return cls(
            instruction="Test the page",
            api_config=APIConfig(model="test-model", auth_mode="subscription"),
            server_url="http://localhost:6001/",
            local_project_dir=output_dir,
            output_dir=output_dir,
            event_log_stream=None,
            record={"checklist": [{"class": "content", "content": "Example"}]},
            allow_screenshots=enabled,
        )

    def test_prompt_variants_match_screenshot_mode(self):
        cases = (
            (ClaudeCodeWebTester_Gold, False, "defect_detection_based_gold"),
            (ClaudeCodeWebTester_Gold, True, "defect_detection_based_gold_with_screenshots"),
            (ClaudeCodeWebTester_GoldHints, False, "defect_detection_based_gold_with_hints"),
            (
                ClaudeCodeWebTester_GoldHints,
                True,
                "defect_detection_based_gold_with_hints_and_screenshots",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            for index, (cls, enabled, expected_key) in enumerate(cases):
                with self.subTest(agent=cls.__name__, enabled=enabled):
                    agent = self.make_agent(cls, Path(tmp) / str(index), enabled)
                    self.assertEqual(agent.defect_prompt_key, expected_key)
                    prompt = USER_PROMPT[expected_key].template
                    if enabled:
                        self.assertIn(
                            "available as a general observation channel",
                            prompt,
                        )
                        self.assertIn(
                            "Decide autonomously when and how often screenshots are useful",
                            prompt,
                        )
                        self.assertNotIn("Do NOT use screenshots", prompt)
                        self.assertNotIn("Do not take screenshots by default", prompt)
                    else:
                        self.assertIn("Do NOT use screenshots", prompt)

    def test_prompt_allows_checklist_required_reload(self):
        active_prompt_keys = (
            "defect_detection_based_gold",
            "defect_detection_based_gold_with_screenshots",
            "defect_detection_based_gold_with_hints",
            "defect_detection_based_gold_with_hints_and_screenshots",
        )
        for key in active_prompt_keys:
            with self.subTest(prompt=key):
                prompt = USER_PROMPT[key].template
                self.assertIn(
                    "when a checklist item explicitly tests behavior",
                    prompt,
                )
                self.assertIn("after a reload, refresh, revisit", prompt)
                self.assertIn("navigate to the current page URL", prompt)
                self.assertNotIn("never re-enter a URL directly or reload", prompt)

    def test_prompt_requires_compaction_safe_result_recording(self):
        active_prompt_keys = (
            "defect_detection_based_gold",
            "defect_detection_based_gold_with_screenshots",
            "defect_detection_based_gold_with_hints",
            "defect_detection_based_gold_with_hints_and_screenshots",
        )
        for key in active_prompt_keys:
            with self.subTest(prompt=key):
                prompt = USER_PROMPT[key].template
                self.assertIn("Immediately call `record_result`", prompt)
                self.assertIn("survives context compaction", prompt)
                self.assertIn("call `get_result_progress`", prompt)
                self.assertIn("Do not reconstruct or emit", prompt)

    def test_tool_permissions_mcp_args_and_buffer_are_conditional(self):
        cases = (ClaudeCodeWebTester_Gold, ClaudeCodeWebTester_GoldHints)
        with tempfile.TemporaryDirectory() as tmp:
            for cls in cases:
                for enabled in (False, True):
                    with self.subTest(agent=cls.__name__, enabled=enabled):
                        output_dir = Path(tmp) / cls.__name__ / str(enabled)
                        agent = self.make_agent(cls, output_dir, enabled)
                        options = agent._get_browser_agent_options()
                        args = options.mcp_servers["playwright"]["args"]

                        self.assertEqual(args[:2], ["-y", PLAYWRIGHT_MCP_PACKAGE])
                        grant_index = args.index("--grant-permissions")
                        self.assertEqual(
                            args[grant_index + 1:grant_index + 3],
                            PLAYWRIGHT_CLIPBOARD_PERMISSIONS,
                        )

                        expected_mcp_tools = playwright_tools(enabled)
                        if cls is ClaudeCodeWebTester_GoldHints:
                            expected_mcp_tools += SemanticHintsTools
                        expected_mcp_tools += STRUCTURED_RESULT_TOOLS

                        self.assertEqual(options.tools, EXPERIMENT_BUILTIN_TOOLS)
                        self.assertIn(
                            STRUCTURED_RESULTS_SERVER,
                            options.mcp_servers,
                        )
                        self.assertEqual(
                            options.allowed_tools,
                            EXPERIMENT_BUILTIN_TOOLS + expected_mcp_tools,
                        )
                        self.assertTrue(
                            set(FORBIDDEN_EXPERIMENT_TOOLS).issubset(
                                options.disallowed_tools
                            )
                        )
                        self.assertFalse(
                            set(FORBIDDEN_EXPERIMENT_TOOLS)
                            & set(options.allowed_tools)
                        )
                        self.assertIn(
                            PLAYWRIGHT_UNSAFE_CODE_TOOL,
                            options.disallowed_tools,
                        )
                        self.assertEqual(options.permission_mode, "dontAsk")
                        self.assertEqual(options.setting_sources, [])
                        self.assertEqual(
                            options.extra_args,
                            {"disable-slash-commands": None},
                        )

                        self.assertEqual(
                            PLAYWRIGHT_SCREENSHOT_TOOL in options.allowed_tools,
                            enabled,
                        )
                        self.assertEqual(
                            PLAYWRIGHT_SCREENSHOT_TOOL in options.disallowed_tools,
                            not enabled,
                        )
                        self.assertEqual("--image-responses" in args, enabled)
                        self.assertEqual("--output-dir" in args, enabled)
                        self.assertEqual(
                            options.max_buffer_size,
                            SCREENSHOT_MAX_BUFFER_SIZE if enabled else 1024 * 1024,
                        )
                        if enabled:
                            self.assertTrue((output_dir / "playwright").is_dir())

    def test_hints_shared_browser_grants_clipboard_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = self.make_agent(
                ClaudeCodeWebTester_GoldHints,
                Path(tmp),
                enabled=False,
            )
            launcher = agent.cdp_launcher.read_text(encoding="utf-8")
            self.assertIn(
                'context.grantPermissions(["clipboard-read", "clipboard-write"])',
                launcher,
            )

    def test_playwright_allowlist_matches_pinned_safe_schema(self):
        self.assertEqual(PLAYWRIGHT_MCP_PACKAGE, "@playwright/mcp@0.0.76")
        self.assertSetEqual(
            set(playwright_tools(False)),
            self.SAFE_PLAYWRIGHT_0_0_76_TOOLS,
        )
        self.assertNotIn(PLAYWRIGHT_UNSAFE_CODE_TOOL, playwright_tools(True))
        self.assertEqual(
            set(playwright_tools(True)) - self.SAFE_PLAYWRIGHT_0_0_76_TOOLS,
            {PLAYWRIGHT_SCREENSHOT_TOOL},
        )

    def test_cli_defaults_disabled_and_accepts_enabled(self):
        required = [
            "run_agent.py",
            "--agent", "claude_code_gold",
            "--data_jsonl_path", "data.jsonl",
            "--output_root", "out",
            "--log_root", "logs",
            "--version", "baseline",
            "--model", "test-model",
        ]
        with patch.object(sys, "argv", required):
            self.assertEqual(parse_args().screenshots, "disabled")
        with patch.object(sys, "argv", required + ["--screenshots", "enabled"]):
            self.assertEqual(parse_args().screenshots, "enabled")

    def test_inline_images_are_summarized_without_base64(self):
        data = "abc123" * 100
        content = [{
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
        }]

        safe = image_safe_content(content)
        self.assertEqual(safe[0]["media_type"], "image/jpeg")
        self.assertEqual(safe[0]["base64_chars"], len(data))
        self.assertNotIn(data, str(safe))

        report_text = text_of(content)
        self.assertIn("media_type=image/jpeg", report_text)
        self.assertIn(f"base64_chars={len(data)}", report_text)
        self.assertNotIn(data, report_text)


if __name__ == "__main__":
    unittest.main()
