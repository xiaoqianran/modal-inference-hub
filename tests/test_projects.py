from __future__ import annotations

import struct
import tempfile
import threading
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.generation_store import (
    GenerationConflict,
    GenerationIntentStore,
    GenerationSubmissionUnknown,
)
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
        self.intents = GenerationIntentStore(self.store.db_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record_generation(
        self, project_id: str, model: str, profile: str, job_id: str
    ) -> dict:
        request_id = f"request-{job_id}"
        claim = self.intents.claim(project_id, request_id, model, profile)
        self.assertTrue(claim["claimed"])
        self.intents.begin_remote(request_id)
        self.intents.mark_remote(request_id, f"remote-{job_id}")
        self.intents.bind_job(request_id, job_id)
        return self.store.get(project_id)

    def test_active_generation_states_cannot_be_deleted(self) -> None:
        for status in (
            "submitting",
            "submission_unknown",
            "generating",
            "running",
            "connection_required",
            "cancel_requested",
        ):
            with self.subTest(status=status):
                project = self.store.create(_png(), f"{status}.png")
                self.store._update(project["id"], status=status)
                with self.assertRaisesRegex(ValueError, "远程任务活动"):
                    self.store.delete(project["id"])

    def test_generation_claim_is_atomic_and_idempotent(self) -> None:
        project = self.store.create(_png(), "claim.png")
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_claim", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, descriptor)

        first = self.intents.claim(project["id"], "request-claim-a", "model-a", "default")
        self.assertTrue(first["claimed"])
        self.assertEqual(self.store.get(project["id"])["status"], "submitting")

        retry = self.intents.claim(project["id"], "request-claim-a", "model-a", "default")
        self.assertFalse(retry["claimed"])
        self.assertIsNone(retry["job_id"])
        with self.assertRaisesRegex(GenerationConflict, "已有远程生成任务"):
            self.intents.claim(project["id"], "request-claim-b", "model-a", "default")

        self.intents.begin_remote("request-claim-a")
        self.intents.mark_remote("request-claim-a", "remote-claim")
        self.intents.bind_job("request-claim-a", "job-claim")
        generated = self.store.get(project["id"])
        self.assertEqual(generated["status"], "generating")
        bound_retry = self.intents.claim(
            project["id"], "request-claim-a", "model-a", "default"
        )
        self.assertFalse(bound_retry["claimed"])
        self.assertEqual(bound_retry["job_id"], "job-claim")

        self.store._update(project["id"], status="succeeded")
        terminal_retry = self.intents.claim(
            project["id"], "request-claim-a", "model-a", "default"
        )
        self.assertFalse(terminal_retry["claimed"])
        self.assertEqual(terminal_retry["job_id"], "job-claim")
        with self.assertRaisesRegex(GenerationConflict, "不同的生成参数"):
            self.intents.claim(project["id"], "request-claim-a", "model-b", "default")

    def test_concurrent_generation_claim_has_single_winner(self) -> None:
        project = self.store.create(_png(), "concurrent-claim.png")
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_race", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, descriptor)
        barrier = threading.Barrier(2)

        def claim(request_id: str) -> bool:
            barrier.wait()
            try:
                return bool(
                    self.intents.claim(project["id"], request_id, "model-a", "default")[
                        "claimed"
                    ]
                )
            except GenerationConflict:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ("request-race-a", "request-race-b")))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)

    def test_preparing_generation_is_safely_released_after_restart(self) -> None:
        project = self.store.create(_png(), "recover-claim.png")
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_recover", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, descriptor)
        self.intents.claim(project["id"], "request-crashed", "model-a", "default")

        restored = GenerationIntentStore(self.store.db_path)
        self.assertEqual(restored.recover_after_restart(), [])
        self.assertEqual(self.store.get(project["id"])["status"], "ready")
        self.assertTrue(
            restored.claim(project["id"], "request-retry", "model-a", "default")["claimed"]
        )

    def test_remote_submission_window_fails_closed_after_restart(self) -> None:
        project = self.store.create(_png(), "uncertain.png")
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_uncertain", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, descriptor)
        self.intents.claim(project["id"], "request-uncertain", "model-a", "default")
        self.intents.begin_remote("request-uncertain")

        restored = GenerationIntentStore(self.store.db_path)
        self.assertEqual(restored.recover_after_restart(), [])
        self.assertEqual(self.store.get(project["id"])["status"], "submission_unknown")
        with self.assertRaises(GenerationSubmissionUnknown):
            restored.claim(project["id"], "request-uncertain", "model-a", "default")
        with self.assertRaises(GenerationConflict):
            restored.claim(project["id"], "request-new", "model-a", "default")

    def test_uncertain_generation_can_only_be_unlocked_explicitly(self) -> None:
        project = self.store.create(_png(), "abandon-uncertain.png")
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_abandon", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, descriptor)
        self.intents.claim(project["id"], "request-abandon", "model-a", "default")
        self.intents.begin_remote("request-abandon")
        self.intents.recover_after_restart()

        self.intents.abandon_uncertain(project["id"])

        self.assertEqual(self.store.get(project["id"])["status"], "ready")
        with self.assertRaisesRegex(GenerationConflict, "已放弃"):
            self.intents.claim(project["id"], "request-abandon", "model-a", "default")
        self.assertTrue(
            self.intents.claim(project["id"], "request-after-abandon", "model-a", "default")[
                "claimed"
            ]
        )

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

    def test_editing_canonical_keeps_the_last_generated_model(self) -> None:
        project = self.store.create(_png(), "preserve-model.png")
        canonical = _png(1024, 1024)
        first = {"id": "can_first", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, first)
        generated = self.record_generation(project["id"], "model-a", "default", "job-1")
        self.assertEqual(generated["artifact_canonical_sha256"], first["sha256"])
        self.store._update(
            project["id"],
            status="succeeded",
            artifact_id="artifact-1",
            artifact_sha256="c" * 64,
            artifact_bytes=128,
        )

        second = {"id": "can_second", "sha256": "b" * 64, "bytes": len(canonical)}
        updated = self.store.save_canonical_selection(
            project["id"], _png(2, 2), canonical, second, {"selected_component_ids": ["cc-1"]}
        )

        self.assertEqual(updated["job_id"], "job-1")
        self.assertEqual(updated["artifact_id"], "artifact-1")
        self.assertEqual(updated["artifact_canonical_sha256"], first["sha256"])
        self.assertEqual(updated["status"], "ready")

    def test_editing_canonical_preserves_active_generation_state(self) -> None:
        project = self.store.create(_png(), "active-edit.png")
        canonical = _png(1024, 1024)
        first = {"id": "can_active_a", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, first)
        self.record_generation(project["id"], "model-a", "default", "job-active")

        second = {"id": "can_active_b", "sha256": "b" * 64, "bytes": len(canonical)}
        updated = self.store.record_local_canonical(project["id"], second)

        self.assertEqual(updated["status"], "generating")
        self.assertEqual(updated["job_id"], "job-active")
        self.assertEqual(updated["canonical_sha256"], second["sha256"])
        with self.assertRaisesRegex(ValueError, "远程任务活动"):
            self.store.delete(project["id"])
        with self.assertRaisesRegex(GenerationConflict, "已有远程生成任务"):
            self.intents.claim(project["id"], "request-second", "model-a", "default")

    def test_new_claim_clears_previous_job_identity_but_keeps_history(self) -> None:
        project = self.store.create(_png(), "new-claim.png")
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_old", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, descriptor)
        self.record_generation(project["id"], "model-a", "default", "job-old")
        self.store.record_job({
            "id": "job-old",
            "status": "succeeded",
            "result": {"artifact": {"id": "art-old", "sha256": "c" * 64, "bytes": 128}},
            "error": None,
        })

        claim = self.intents.claim(project["id"], "request-new", "model-a", "default")
        current = self.store.get(project["id"])

        self.assertTrue(claim["claimed"])
        self.assertEqual(current["status"], "submitting")
        self.assertIsNone(current["job_id"])
        self.assertEqual(current["artifact_id"], "art-old")
        self.assertEqual(self.store.list_generations(project["id"])[0]["job_id"], "job-old")

    def test_project_keeps_multiple_generation_records(self) -> None:
        project = self.store.create(_png(), "history.png")
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_history", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, descriptor)

        self.record_generation(project["id"], "model-a", "fast", "job-a")
        self.store.record_job({
            "id": "job-a",
            "status": "succeeded",
            "result": {"artifact": {"id": "art-a", "sha256": "b" * 64, "bytes": 100}},
            "error": None,
        })
        self.record_generation(project["id"], "model-b", "quality", "job-b")

        generations = self.store.list_generations(project["id"])
        self.assertEqual([item["job_id"] for item in generations], ["job-b", "job-a"])
        self.assertEqual(generations[1]["artifact_id"], "art-a")
        self.assertEqual(generations[1]["canonical_sha256"], descriptor["sha256"])

    def test_existing_single_result_is_migrated_to_generation_history(self) -> None:
        project = self.store.create(_png(), "migration.png")
        canonical = _png(1024, 1024)
        descriptor = {"id": "can_migration", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(project["id"], _png(2, 2), canonical, descriptor)
        self.record_generation(project["id"], "model-a", "default", "job-old")
        self.store.record_job({
            "id": "job-old",
            "status": "succeeded",
            "result": {"artifact": {"id": "art-old", "sha256": "b" * 64, "bytes": 100}},
            "error": None,
        })
        with self.store._connect() as db:
            db.execute("DELETE FROM project_generations")

        restored = ProjectStore(Path(self.temp.name))
        generations = restored.list_generations(project["id"])

        self.assertEqual(len(generations), 1)
        self.assertEqual(generations[0]["job_id"], "job-old")
        self.assertEqual(generations[0]["artifact_id"], "art-old")
        self.assertEqual(generations[0]["status"], "succeeded")

    def test_library_import_is_local_idempotent_and_creates_thumbnail(self) -> None:
        source = _png(7, 5)

        first = self.store.import_library(source, "chair.png")
        duplicate = self.store.import_library(source, "same-content.png")

        self.assertEqual(first["status"], "imported")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["project"]["id"], first["project"]["id"])
        self.assertEqual(first["project"]["status"], "draft")
        self.assertIsNone(first["project"]["canonical_id"])
        self.assertTrue(self.store.thumbnail_path(first["project"]["id"]).is_file())
        self.assertEqual(self.store.list_gallery()["total"], 1)

    def test_gallery_paginates_and_marks_old_canonical_results_as_history(self) -> None:
        first = self.store.create(_png(2, 2), "first.png")
        second = self.store.create(_png(3, 2), "second.png")
        third = self.store.create(_png(4, 2), "third.png")
        canonical = _png(1024, 1024)
        old = {"id": "can_gallery_old", "sha256": "a" * 64, "bytes": len(canonical)}
        self.store.save_preprocessed(first["id"], _png(2, 2), canonical, old)
        self.record_generation(first["id"], "model-a", "default", "job-gallery-old")
        self.store.record_job({
            "id": "job-gallery-old",
            "status": "succeeded",
            "result": {"artifact": {"id": "art-gallery-old", "sha256": "c" * 64, "bytes": 128}},
            "error": None,
        })
        current = {"id": "can_gallery_current", "sha256": "b" * 64, "bytes": len(canonical)}
        self.store.save_canonical_selection(
            first["id"], _png(2, 2), canonical, current, {"selected_component_ids": ["cc-1"]}
        )

        page_one = self.store.list_gallery(page=1, page_size=2)
        page_two = self.store.list_gallery(page=2, page_size=2)
        gallery_first = next(
            item for item in page_one["items"] + page_two["items"]
            if item["project"]["id"] == first["id"]
        )

        self.assertEqual(page_one["total"], 3)
        self.assertEqual(len(page_one["items"]), 2)
        self.assertEqual(len(page_two["items"]), 1)
        self.assertEqual({item["project"]["id"] for item in page_one["items"] + page_two["items"]}, {first["id"], second["id"], third["id"]})
        self.assertEqual(len(gallery_first["generations"]), 1)
        self.assertEqual(gallery_first["generations"][0]["job_id"], "job-gallery-old")
        self.assertFalse(gallery_first["generations"][0]["is_current"])

        self.record_generation(first["id"], "model-a", "default", "job-gallery-current")
        current_gallery = self.store.list_gallery(page=1, page_size=10)
        gallery_first = next(item for item in current_gallery["items"] if item["project"]["id"] == first["id"])
        self.assertEqual(gallery_first["generations"][0]["job_id"], "job-gallery-current")
        self.assertTrue(gallery_first["generations"][0]["is_current"])

    def test_legacy_sam_columns_are_ignored(self) -> None:
        project = self.store.create(_png(), "legacy.png")
        with self.store._connect() as db:
            db.execute("ALTER TABLE projects ADD COLUMN sam_provider TEXT")
            db.execute("ALTER TABLE projects ADD COLUMN scene_id TEXT")
            db.execute("ALTER TABLE projects ADD COLUMN selection_id TEXT")
            db.execute("ALTER TABLE projects ADD COLUMN candidate_id TEXT")
            db.execute("UPDATE projects SET sam_provider = 'cloud', scene_id = 'old-scene' WHERE id = ?", (project["id"],))

        restored = self.store.get(project["id"])
        self.assertEqual(restored["id"], project["id"])
        self.assertNotIn("sam_provider", restored)
        self.assertNotIn("scene_id", restored)
        self.assertNotIn("selection_id", restored)
        self.assertNotIn("candidate_id", restored)

    def test_terminal_project_can_be_deleted(self) -> None:
        project = self.store.create(_png(), "done.png")
        self.store._update(project["id"], status="succeeded")
        self.store.delete(project["id"])
        self.assertEqual(self.store.list(), [])


if __name__ == "__main__":
    unittest.main()
