from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import artifacts


def glb(payload: bytes = b"{}") -> bytes:
    body = payload + b" " * ((4 - len(payload) % 4) % 4)
    chunk = struct.pack("<I4s", len(body), b"JSON") + body
    total = 12 + len(chunk)
    return struct.pack("<4sII", b"glTF", 2, total) + chunk


class ArtifactCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = glb()
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.data_patch = patch("agent.artifacts.data_dir", return_value=self.root)
        self.data_patch.start()

    def tearDown(self) -> None:
        self.data_patch.stop()
        self.temp.cleanup()

    def cache(self, descriptor: dict | None = None):
        with patch("agent.artifacts.read", return_value=iter([self.data[:7], self.data[7:]])):
            return artifacts.cache_remote(
                "jobs/model.glb",
                descriptor or {},
                "fastsam3d-plus-plus",
            )

    def test_remote_stream_commits_content_addressed_cache(self) -> None:
        descriptor, path = self.cache(
            {"bytes": len(self.data), "sha256": self.digest, "id": "art-known"}
        )
        self.assertEqual(path, self.root / "cache" / "sha256" / self.digest[:2] / self.digest)
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(descriptor["id"], "art-known")
        self.assertEqual(descriptor["sha256"], self.digest)

    def test_hash_mismatch_never_commits_final_file(self) -> None:
        with self.assertRaisesRegex(artifacts.ArtifactValidationError, "SHA-256"):
            self.cache({"bytes": len(self.data), "sha256": "0" * 64})
        committed = list((self.root / "cache" / "sha256").glob("[0-9a-f][0-9a-f]/*"))
        self.assertEqual(committed, [])

    def test_invalid_glb_never_commits(self) -> None:
        bad = b"not a glb"
        with patch("agent.artifacts.read", return_value=iter([bad])):
            with self.assertRaises(artifacts.ArtifactValidationError):
                artifacts.cache_remote("jobs/bad.glb", {}, "fastsam3d-plus-plus")
        committed = list((self.root / "cache" / "sha256").glob("[0-9a-f][0-9a-f]/*"))
        self.assertEqual(committed, [])

    def test_cleanup_does_not_delete_leased_artifact(self) -> None:
        descriptor, path = self.cache()
        with patch.dict("os.environ", {"MODAL_3D_CACHE_BUDGET_BYTES": "1"}):
            artifacts.lease(path)
            try:
                result = artifacts.cleanup_cache()
                self.assertTrue(path.is_file())
                self.assertGreater(result["bytes"], result["budget_bytes"])
            finally:
                artifacts.release(path)


if __name__ == "__main__":
    unittest.main()
