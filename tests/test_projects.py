from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from agent.projects import ProjectStore


def _png(width: int = 1, height: int = 1) -> bytes:
    """构造一个合法的 1x1 真彩 PNG（8-bit RGB），供 image_input.describe 解析。"""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit, truecolor
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    idat = zlib.compress(raw)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class ProjectStoreLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_active_generation_states_cannot_be_deleted(self) -> None:
        for status in ("generating", "running", "connection_required", "cancel_requested"):
            with self.subTest(status=status):
                project = self.store.create(_png(), f"{status}.png")
                self.store._update(project["id"], status=status)
                with self.assertRaisesRegex(ValueError, "远程任务活动"):
                    self.store.delete(project["id"])

    def test_local_canonical_is_saved_before_remote_upload(self) -> None:
        project = self.store.create(_png(), "source.png")
        matte = _png(2, 2)
        canonical = _png(1024, 1024)
        descriptor = {
            "id": "can_test",
            "sha256": "a" * 64,
            "bytes": len(canonical),
        }
        updated = self.store.save_preprocessed(project["id"], matte, canonical, descriptor)
        self.assertEqual(updated["status"], "ready")
        public, local = self.store.canonical_local(project["id"])
        self.assertEqual(public["id"], "can_test")
        self.assertEqual(local.read_bytes(), canonical)
        with self.assertRaisesRegex(RuntimeError, "尚未上传"):
            self.store.canonical_remote(project["id"])
        self.store.record_remote_canonical(
            project["id"], "client-inputs/test.png", descriptor["sha256"]
        )
        _, remote = self.store.canonical_remote(project["id"])
        self.assertEqual(remote, "client-inputs/test.png")

    def test_component_state_is_persisted_and_selection_invalidates_remote_canonical(self) -> None:
        project = self.store.create(_png(), "components.png")
        matte = _png(2, 2)
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_a", "sha256": "a" * 64, "bytes": len(canonical)}
        state = {
            "source_size": [2, 2],
            "components": [{"id": "cc-00001", "selected": True}],
            "selected_component_ids": ["cc-00001"],
            "component_count": 1,
        }
        self.store.save_preprocessed(project["id"], matte, canonical, descriptor, state)
        self.assertEqual(self.store.component_state(project["id"]), state)
        self.store.record_remote_canonical(
            project["id"], "client-inputs/old.png", descriptor["sha256"]
        )

        updated_descriptor = {"id": "can_b", "sha256": "b" * 64, "bytes": len(canonical)}
        updated_state = {**state, "selection_elapsed_ms": 3.5}
        self.store.save_canonical_selection(project["id"], matte, canonical, updated_descriptor, updated_state)
        self.assertEqual(self.store.component_state(project["id"]), updated_state)
        with self.assertRaisesRegex(RuntimeError, "尚未上传"):
            self.store.canonical_remote(project["id"])

    def test_remote_canonical_requires_matching_sha256(self) -> None:
        project = self.store.create(_png(), "sha.png")
        matte = _png(2, 2)
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_sha", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], matte, canonical, descriptor)

        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.store.record_remote_canonical(
                project["id"], "client-inputs/wrong.png", "b" * 64
            )

        self.store._update(
            project["id"],
            canonical_path="client-inputs/legacy.png",
            canonical_remote_sha256=None,
        )
        with self.assertRaisesRegex(RuntimeError, "尚未上传"):
            self.store.canonical_remote(project["id"])

        self.store._update(
            project["id"],
            canonical_path="client-inputs/stale.png",
            canonical_remote_sha256="b" * 64,
        )
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            self.store.canonical_remote(project["id"])

    def test_terminal_project_can_be_deleted(self) -> None:
        project = self.store.create(_png(), "done.png")
        self.store._update(project["id"], status="succeeded")
        self.store.delete(project["id"])
        self.assertEqual(self.store.list(), [])


if __name__ == "__main__":
    unittest.main()
