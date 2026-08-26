from __future__ import annotations

import unittest

import numpy as np
from PIL import Image

from agent.preprocess.image_ops import refine_mask


def _mask_from_array(array: np.ndarray) -> Image.Image:
    """Build an L mask from a float32 probability array (0.0..1.0)."""
    return Image.fromarray((array * 255).astype(np.uint8), mode="L")


def _alpha_array(mask: Image.Image) -> np.ndarray:
    return np.asarray(mask.convert("L"), dtype=np.float32) / 255.0


class RefineMaskTests(unittest.TestCase):
    def test_clean_hard_edged_mask_is_idempotent(self) -> None:
        mask = Image.new("L", (40, 40), 0)
        mask.paste(255, (10, 10, 30, 30))
        refined = refine_mask(mask)
        np.testing.assert_array_equal(_alpha_array(refined), _alpha_array(mask))
        self.assertEqual(refined.size, mask.size)
        self.assertEqual(refined.mode, "L")

    def test_interior_hole_is_filled(self) -> None:
        mask = Image.new("L", (40, 40), 0)
        mask.paste(255, (10, 10, 30, 30))
        # Carve a background hole fully enclosed by the subject.
        mask.paste(0, (18, 18, 22, 22))
        refined = refine_mask(mask)
        alpha = _alpha_array(refined)
        self.assertGreater(alpha[20, 20], 0.9)  # hole now reads as subject
        self.assertEqual(alpha[5, 5], 0.0)  # outside stays background
        self.assertEqual(alpha[10, 10], 1.0)  # original solid subject intact

    def test_low_confidence_subject_is_kept(self) -> None:
        # A subject the model was only mildly confident about (0.55) must not be
        # dropped the way a min-max stretch + hard cut would drop it.
        array = np.zeros((40, 40), dtype=np.float32)
        array[12:28, 12:28] = 0.55
        refined = refine_mask(_mask_from_array(array))
        alpha = _alpha_array(refined)
        self.assertGreater(alpha[20, 20], 0.5)
        self.assertEqual(alpha[5, 5], 0.0)

    def test_thin_gap_is_bridged_without_growing_subject(self) -> None:
        # Two blobs separated by a 1px gap: closing bridges it, but the subject
        # never grows past its original outer bounds.
        mask = Image.new("L", (40, 40), 0)
        mask.paste(255, (10, 10, 20, 30))
        mask.paste(255, (22, 10, 32, 30))  # 2px gap between blobs
        refined = refine_mask(mask)
        alpha = _alpha_array(refined)
        self.assertGreater(alpha[21, 20], 0.9)  # gap bridged
        self.assertEqual(alpha[8, 20], 0.0)  # left edge not grown outward
        self.assertEqual(alpha[34, 20], 0.0)  # right edge not grown outward

    def test_added_pixels_get_full_opacity(self) -> None:
        # Pixels the repair added (a hole) had no soft alpha; they must read as
        # full subject, not as faint background.
        mask = Image.new("L", (40, 40), 0)
        mask.paste(255, (10, 10, 30, 30))
        mask.paste(0, (18, 18, 22, 22))
        refined = refine_mask(mask)
        alpha = _alpha_array(refined)
        self.assertEqual(alpha[20, 20], 1.0)

    def test_background_outside_subject_is_zeroed(self) -> None:
        # Low-confidence noise far from the subject is not part of any repaired
        # region and must be dropped.
        array = np.zeros((40, 40), dtype=np.float32)
        array[12:28, 12:28] = 0.9  # confident subject
        array[2:6, 2:6] = 0.4  # faint isolated noise below threshold
        refined = refine_mask(_mask_from_array(array))
        alpha = _alpha_array(refined)
        self.assertEqual(alpha[4, 4], 0.0)
        # 0.9 quantises to 229/255 ≈ 0.898 through the uint8 mask round-trip.
        self.assertGreater(alpha[20, 20], 0.89)

    def test_no_confident_subject_falls_back_to_raw_mask(self) -> None:
        # Nothing reaches the 0.5 threshold: return the raw soft mask untouched
        # so the caller's own foreground check can decide usability.
        array = np.full((40, 40), 0.3, dtype=np.float32)
        refined = refine_mask(_mask_from_array(array))
        np.testing.assert_allclose(_alpha_array(refined), array, atol=0.01)


if __name__ == "__main__":
    unittest.main()
