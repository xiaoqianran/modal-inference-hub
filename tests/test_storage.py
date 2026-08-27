from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import storage


class StorageTests(unittest.TestCase):
    def test_explicit_data_dir_does_not_require_home(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with (
                patch.dict(os.environ, {"MODAL_3D_AGENT_DATA_DIR": root}, clear=True),
                patch.object(Path, "home", side_effect=RuntimeError("home unavailable")),
            ):
                self.assertEqual(storage.data_dir(), Path(root))

    def test_missing_platform_home_falls_back_to_temp(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(Path, "home", side_effect=RuntimeError("home unavailable")),
                patch.object(tempfile, "gettempdir", return_value=root),
            ):
                expected = Path(root) / "modal-inference-hub"
                self.assertEqual(storage.data_dir(), expected)
                self.assertTrue(expected.is_dir())

    def test_existing_legacy_data_dir_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / "modal-3D-client"
            legacy.mkdir()
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(storage, "_platform_data_root", return_value=Path(root)),
            ):
                self.assertEqual(storage.data_dir(), legacy)

    def test_new_data_dir_env_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "custom"
            with patch.dict(
                os.environ,
                {
                    "MODAL_INFERENCE_HUB_DATA_DIR": str(target),
                    "MODAL_3D_AGENT_DATA_DIR": str(Path(root) / "legacy-override"),
                },
                clear=True,
            ):
                self.assertEqual(storage.data_dir(), target)


if __name__ == "__main__":
    unittest.main()
