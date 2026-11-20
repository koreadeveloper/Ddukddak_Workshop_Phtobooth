"""Local framing and face-composite regressions, without an AI API."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from id_portrait import finalize_portrait, prepare_portrait


class PortraitTest(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((720, 1280, 3), (65, 115, 185), dtype=np.uint8)
        self.face = np.array([550, 220, 180, 180, 600, 272, 680, 272, 640, 310, 610, 353, 670, 353])

    def prepare(self):
        with patch("id_portrait._detect_faces", return_value=[self.face]):
            return prepare_portrait(self.frame)

    def test_prepare_crops_a_single_face_to_print_proportions(self):
        portrait = self.prepare()
        height, width = portrait.image_bgr.shape[:2]
        x, y, face_width, face_height = portrait.face_box
        self.assertEqual(width * 49, height * 34)
        self.assertAlmostEqual((x + face_width / 2) / width, 0.5, delta=0.01)
        self.assertAlmostEqual((y + face_height / 2) / height, 0.44, delta=0.01)

    def test_prepare_rejects_no_face_or_multiple_faces(self):
        for boxes, message in (([], "얼굴"), ([self.face, self.face], "한 명")):
            with self.subTest(boxes=boxes), patch("id_portrait._detect_faces", return_value=boxes):
                with self.assertRaisesRegex(ValueError, message):
                    prepare_portrait(self.frame)

    def test_prepare_rejects_a_face_too_near_the_edge(self):
        face = self.face.copy()
        face[::2] -= 525
        face[2] = 180
        with patch("id_portrait._detect_faces", return_value=[face]):
            with self.assertRaises(ValueError):
                prepare_portrait(self.frame)

    def test_composite_preserves_the_original_face_and_edited_clothes(self):
        portrait = self.prepare()
        edited = np.full_like(portrait.image_bgr, (180, 90, 35))
        face = np.concatenate((portrait.face_box, portrait.landmarks.ravel()))
        with patch("id_portrait._detect_faces", return_value=[face]):
            result = finalize_portrait(portrait, edited)
        x, y, width, height = portrait.face_box
        face_pixel = (y + height // 2, x + width // 2)
        np.testing.assert_array_equal(result[face_pixel], portrait.image_bgr[face_pixel])
        np.testing.assert_array_equal(result[-30, result.shape[1] // 2], edited[-30, edited.shape[1] // 2])
        self.assertEqual(result.shape, portrait.image_bgr.shape)
        self.assertTrue(np.any((result[:, :, 0] > 65) & (result[:, :, 0] < 180)))

    def test_composite_rejects_a_shifted_generated_face(self):
        portrait = self.prepare()
        face = np.concatenate((portrait.face_box, portrait.landmarks.ravel()))
        face[0] += portrait.face_box[2] // 2
        with patch("id_portrait._detect_faces", return_value=[face]):
            with self.assertRaisesRegex(ValueError, "얼굴 위치"):
                finalize_portrait(portrait, portrait.image_bgr)

    def test_synthetic_photo_with_real_opencv_detector(self):
        image = cv2.imread(str(Path(__file__).resolve().parent.parent / "assets/ai_concepts/suit_profile.jpg"))
        self.assertIsNotNone(image)
        portrait = prepare_portrait(image)
        edited = cv2.resize(portrait.image_bgr, (600, 900))
        result = finalize_portrait(portrait, edited)
        self.assertEqual(result.shape, portrait.image_bgr.shape)
        with self.assertRaisesRegex(ValueError, "한 명"):
            prepare_portrait(np.hstack((image, image)))
        with self.assertRaisesRegex(ValueError, "얼굴을 찾지 못했어요"):
            prepare_portrait(np.full_like(self.frame, 160))


if __name__ == "__main__":
    unittest.main()
