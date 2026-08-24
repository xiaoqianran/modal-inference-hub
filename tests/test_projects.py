from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.projects import ProjectStore


class ProjectStoreLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_active_generation_states_cannot_be_deleted(self) -> None:
        for status in ("generating", "running", "connection_required", "cancel_requested"):
            with self.subTest(status=status):
                project = self.store.create(b"image", f"{status}.png")
                self.store._update(project["id"], status=status)
                with self.assertRaisesRegex(ValueError, "远程任务活动"):
                    self.store.delete(project["id"])

    def test_terminal_project_can_be_deleted(self) -> None:
        project = self.store.create(b"image", "done.png")
        self.store._update(project["id"], status="succeeded")
        self.store.delete(project["id"])
        self.assertEqual(self.store.list(), [])


if __name__ == "__main__":
    unittest.main()
