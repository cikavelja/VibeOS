import sys, unittest, json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from progress import validate, TASKS
class RegistryTests(unittest.TestCase):
    def setUp(self): self.tasks=json.loads(TASKS.read_text())
    def test_baseline(self): self.assertEqual(len(validate(self.tasks)),len(self.tasks))
    def test_unknown_dependency(self):
        self.tasks[0]['dependencies']=['UNKNOWN']
        with self.assertRaises(ValueError): validate(self.tasks)
    def test_cycle(self):
        self.tasks[0]['dependencies']=[self.tasks[0]['id']]
        with self.assertRaises(ValueError): validate(self.tasks)
    def test_missing_evidence(self):
        self.tasks[0]['status']='VERIFIED'
        with self.assertRaises(ValueError): validate(self.tasks)
    def test_duplicate(self):
        self.tasks.append(self.tasks[0])
        with self.assertRaises(ValueError): validate(self.tasks)
