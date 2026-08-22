import json
import sys
import tempfile
import unittest
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from result_store import (
    STRUCTURED_RESULTS_SERVER,
    StructuredResultStore,
)


CHECKLIST = """# Test Checklist

## Functionality
- [ ] FT-01: Create an event.
- [ ] FT-02: Edit an event.

## Constraint
- [ ] CS-01: Do not allow past dates.
"""


class StructuredResultStoreTests(unittest.TestCase):
    def make_store(self, root: Path) -> StructuredResultStore:
        checklist = root / "checklist.md"
        checklist.write_text(CHECKLIST, encoding="utf-8")
        return StructuredResultStore(checklist, root / "result_events.jsonl")

    def test_records_and_renders_complete_canonical_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            store.record_result(item_id="FT-01", verdict="PASS", evidence="toast")
            store.record_result(
                item_id="FT-02",
                verdict="FAIL",
                issue="Edit ignored",
                actual="The saved title remained unchanged.",
            )
            progress = store.record_result(item_id="CS-01", verdict="PASS")

            self.assertTrue(progress["complete"])
            report = store.render_markdown()
            self.assertIn("- [X] FT-01: Create an event.", report)
            self.assertIn("- [ ] FT-02: Edit an event.", report)
            self.assertIn("    - Issue: Edit ignored", report)
            self.assertIn(
                "    - Actual: The saved title remained unchanged.", report
            )
            self.assertIn("- [X] CS-01: Do not allow past dates.", report)

            events = [
                json.loads(line)
                for line in store.events_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(events), 3)
            self.assertEqual(events[0]["evidence"], "toast")

    def test_latest_event_wins_without_destroying_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            store.record_result(
                item_id="FT-01",
                verdict="FAIL",
                issue="Initial observation",
                actual="It appeared not to save.",
            )
            store.record_result(item_id="FT-01", verdict="PASS")

            self.assertIn("- [X] FT-01", store.render_markdown())
            self.assertEqual(
                len(store.events_path.read_text(encoding="utf-8").splitlines()),
                2,
            )

    def test_rejects_unknown_ids_and_incomplete_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            with self.assertRaisesRegex(ValueError, "unknown checklist item"):
                store.record_result(item_id="FT-99", verdict="PASS")
            with self.assertRaisesRegex(ValueError, "FAIL requires"):
                store.record_result(item_id="FT-01", verdict="FAIL")
            with self.assertRaisesRegex(ValueError, "PASS must not"):
                store.record_result(
                    item_id="FT-01",
                    verdict="PASS",
                    issue="Should not be present",
                )

    def test_ignores_truncated_tail_and_old_checklist_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.make_store(root)
            store.record_result(item_id="FT-01", verdict="PASS")
            with store.events_path.open("a", encoding="utf-8") as stream:
                stream.write('{"truncated":')
            self.assertEqual(store.progress()["recorded_count"], 1)

            store.checklist_path.write_text(
                CHECKLIST.replace("Create an event.", "Create a calendar event."),
                encoding="utf-8",
            )
            self.assertEqual(store.progress()["recorded_count"], 0)

    def test_builds_in_process_mcp_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self.make_store(Path(tmp)).create_mcp_server()
            self.assertEqual(server["type"], "sdk")
            self.assertEqual(server["name"], STRUCTURED_RESULTS_SERVER)
            self.assertIsNotNone(server["instance"])


if __name__ == "__main__":
    unittest.main()
