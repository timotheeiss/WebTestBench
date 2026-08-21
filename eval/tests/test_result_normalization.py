import sys
import tempfile
import unittest
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from agent import APIConfig, BaseAgent


CHECKLIST = """# Test Checklist

## Functionality
- [ ] FT-01: Create an event.
- [ ] FT-02: Edit an event.

## Constraint
- [ ] CS-01: Do not allow past dates.
"""


class ResultNormalisationTests(unittest.TestCase):
    def make_agent(self, output_dir: Path) -> BaseAgent:
        agent = BaseAgent(
            instruction="Test the page",
            api_config=APIConfig(model="test-model", auth_mode="subscription"),
            server_url="http://localhost:6001/",
            local_project_dir=output_dir,
            output_dir=output_dir,
        )
        agent.checklist_path.write_text(CHECKLIST, encoding="utf-8")
        return agent

    def assert_canonical(self, report: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parsed = self.make_agent(Path(tmp))._parse_result_report(report)
            self.assertTrue(parsed.is_valid)
            self.assertTrue(parsed.canonical_result.startswith("# Test Result\n\n"))
            self.assertIn("- [X] FT-01: Create an event.", parsed.canonical_result)
            self.assertIn("- [ ] CS-01: Do not allow past dates.", parsed.canonical_result)
            if "Past date accepted" in report:
                self.assertIn("Issue: Past date accepted", parsed.canonical_result)

    def test_accepts_title_variants_and_normalises_them(self):
        reports = (
            """## Test Result Report — Eventify

## Functionality
- [X] FT-01: changed description
- [X] FT-02: changed description

## Constraint
- [ ] CS-01: changed description
  - Bug Report:
    - Issue: Past date accepted
""",
            """# Eventify QA Test Result Report

## Functionality
- [X] FT-01: changed description
- [X] FT-02: changed description

## Constraint
- [ ] CS-01: changed description
""",
            """Testing complete. Final report:

## Functionality
- [X] **FT-01** — changed description
- [X] **FT-02** — changed description

## Constraint
- [ ] **CS-01** — changed description
""",
        )
        for report in reports:
            with self.subTest(report=report.splitlines()[0]):
                self.assert_canonical(report)

    def test_uses_complete_assistant_text_when_terminal_result_is_empty(self):
        report = """Testing complete.

## Functionality
- [X] FT-01: changed description
- [X] FT-02: changed description

## Constraint
- [ ] CS-01: changed description
"""
        with tempfile.TemporaryDirectory() as tmp:
            agent = self.make_agent(Path(tmp))
            agent.recent_assistant_text_blocks = {"defect_detection": [report]}
            parsed, from_result_message = agent._normalise_defect_result("")

            self.assertTrue(parsed.is_valid)
            self.assertFalse(from_result_message)

    def test_rejects_missing_duplicate_and_unknown_ids(self):
        cases = {
            "missing": (
                """- [X] FT-01: changed
- [X] FT-02: changed
""",
                "missing_ids",
            ),
            "duplicate": (
                """- [X] FT-01: changed
- [ ] FT-01: duplicate
- [X] FT-02: changed
- [ ] CS-01: changed
""",
                "duplicate_ids",
            ),
            "unknown": (
                """- [X] FT-01: changed
- [X] FT-02: changed
- [ ] CS-01: changed
- [X] IX-99: unknown
""",
                "unknown_ids",
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, (report, reason) in cases.items():
                with self.subTest(case=name):
                    agent = self.make_agent(Path(tmp) / name)
                    parsed = agent._parse_result_report(report)
                    self.assertFalse(parsed.is_valid)
                    self.assertEqual(parsed.failure_kind, reason)
                    agent._record_invalid_result(parsed)
                    self.assertTrue(agent.result_failure_path.exists())
                    self.assertTrue(agent.raw_result_path.exists())


if __name__ == "__main__":
    unittest.main()
