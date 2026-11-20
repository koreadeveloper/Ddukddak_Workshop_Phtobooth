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

    def test_prepare_chooses_center_face_and_ignores_small_detection(self):
        small = np.array([100, 100, 25, 25, 105, 108, 117, 108, 111, 113, 107, 118, 115, 118, .99])
        central = np.append(self.face, .88)
        with patch("id_portrait._detect_faces", return_value=[small, central]):
            portrait = prepare_portrait(self.frame)
        self.assertAlmostEqual((portrait.face_box[0] + portrait.face_box[2] / 2) / 680, 0.5, delta=.02)

    def test_prepare_chooses_more_confident_detection_near_center(self):
        lower = np.append(self.face.copy(), .8)
        higher = np.append(self.face.copy(), .98)
        higher[1] += 20
        higher[5:14:2] += 20
        with patch("id_portrait._detect_faces", return_value=[lower, higher]):
            portrait = prepare_portrait(self.frame)
        self.assertAlmostEqual(portrait.landmarks[0, 1], (292 - (240 + 90 - (180 / .44) * .44)) * (980 / (180 / .44)), delta=2)

    def test_prepare_rejects_no_face(self):
        with patch("id_portrait._detect_faces", return_value=[]):
            with self.assertRaisesRegex(ValueError, "얼굴"):
                prepare_portrait(self.frame)

    def test_prepare_rejects_a_face_too_near_the_edge(self):
        face = self.face.copy()
        face[::2] -= 525
        face[2] = 180
        with patch("id_portrait._detect_faces", return_value=[face]):
            with self.assertRaises(ValueError):
                prepare_portrait(self.frame)

    def test_edited_image_is_used_without_pasting_the_original_face(self):
        portrait = self.prepare()
        edited = np.full_like(portrait.image_bgr, (180, 90, 35))
        face = np.concatenate((portrait.face_box, portrait.landmarks.ravel()))
        with patch("id_portrait._detect_faces", return_value=[face]):
            result = finalize_portrait(portrait, edited)
        np.testing.assert_array_equal(result, edited)

    def test_rejects_generated_image_without_a_face(self):
        portrait = self.prepare()
        with patch("id_portrait._detect_faces", return_value=[]):
            with self.assertRaisesRegex(ValueError, "얼굴 위치"):
                finalize_portrait(portrait, portrait.image_bgr)

    def test_synthetic_photo_with_real_opencv_detector(self):
        image = cv2.imread(str(Path(__file__).resolve().parent.parent / "assets/ai_concepts/suit_profile.jpg"))
        self.assertIsNotNone(image)
        portrait = prepare_portrait(image)
        edited = cv2.resize(portrait.image_bgr, (600, 900))
        result = finalize_portrait(portrait, edited)
        self.assertEqual(result.shape, portrait.image_bgr.shape)
        self.assertEqual(prepare_portrait(np.hstack((image, image))).image_bgr.shape, (980, 680, 3))
        with self.assertRaisesRegex(ValueError, "얼굴을 찾지 못했어요"):
            prepare_portrait(np.full_like(self.frame, 160))


if __name__ == "__main__":
    unittest.main()
