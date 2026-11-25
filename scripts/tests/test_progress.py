import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from progress import TASKS, validate


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tasks = json.loads(TASKS.read_text())

    def test_baseline(self):
        self.assertEqual(len(validate(self.tasks)), len(self.tasks))

    def test_unknown_dependency(self):
        self.tasks[0]["dependencies"] = ["UNKNOWN"]
        with self.assertRaises(ValueError):
            validate(self.tasks)

    def test_cycle(self):
        self.tasks[0]["dependencies"] = [self.tasks[0]["id"]]
        with self.assertRaises(ValueError):
            validate(self.tasks)

    def test_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            task_id = self.tasks[0]["id"]
            task_doc = tmp_root / "docs" / "tasks" / f"{task_id}.md"
            task_doc.parent.mkdir(parents=True, exist_ok=True)
            task_doc.write_text("# Task\n")
            candidate = [{**self.tasks[0], "status": "VERIFIED", "dependencies": []}]
            with patch("progress.ROOT", tmp_root):
                with self.assertRaisesRegex(
                    ValueError, f"Missing verification report: {task_id}"
                ):
                    validate(candidate)

    def test_duplicate(self):
        self.tasks.append(self.tasks[0])
        with self.assertRaises(ValueError):
            validate(self.tasks)
