"""Run the ID photo sheet and CUPS submission tests without a printer."""
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import composer
import printer


def filled_photo(color):
    return np.full((579, 402, 3), color[::-1], dtype=np.uint8)


class IDPrintTests(unittest.TestCase):
    def test_sheet_keeps_four_exact_photo_rectangles_in_order(self):
        colors = [(220, 70, 70), (70, 220, 70), (70, 70, 220), (190, 110, 50)]
        with TemporaryDirectory() as directory, patch.object(composer, "PHOTOS_DIR", Path(directory)):
            path = composer.compose_id_print_image([filled_photo(color) for color in colors], "a123")
            self.assertEqual(path.name, "a123.id.jpg")
            with Image.open(path) as jpeg:
                self.assertEqual(jpeg.size, (1181, 1748))
                self.assertEqual(jpeg.info["dpi"], (300, 300))
                sheet = np.asarray(jpeg, dtype=int)

        centers = [(330, 525), (850, 525), (330, 1222), (850, 1222)]
        for slot, ((x_mid, y_mid), color) in enumerate(zip(centers, colors)):
            with self.subTest(slot=slot):
                np.testing.assert_allclose(sheet[y_mid, x_mid], color, atol=3)
                row = sheet[y_mid, :, :]
                col = sheet[:, x_mid, :]
                same_row = np.flatnonzero((np.abs(row - color) < 50).all(axis=1))
                same_col = np.flatnonzero((np.abs(col - color) < 50).all(axis=1))
                self.assertEqual(len(same_row), 402)
                self.assertEqual(len(same_col), 579)

    def test_preview_skips_empty_slots_and_scales_the_whole_sheet(self):
        photo = filled_photo((220, 70, 70))
        preview = composer.make_id_print_preview([photo, photo], 874)
        self.assertEqual(preview.size, (590, 874))
        self.assertEqual(preview.getpixel((165, 262)), (220, 70, 70))
        self.assertEqual(preview.getpixel((425, 262)), (220, 70, 70))
        self.assertEqual(preview.getpixel((165, 611)), (255, 255, 255))
        self.assertEqual(preview.getpixel((425, 611)), (255, 255, 255))

    def test_id_job_requests_no_scaling_and_submits_a_300_dpi_print_page(self):
        with TemporaryDirectory() as directory, patch.object(composer, "PHOTOS_DIR", Path(directory)):
            path = composer.compose_id_print_image([filled_photo((120, 180, 220))] * 4, "demo")

            def capture(cmd, **kwargs):
                self.assertIn("print-scaling=none", cmd)
                self.assertIn("StpiShrinkOutput=Crop", cmd)
                self.assertNotIn("fit-to-page", cmd)
                submitted = Path(cmd[-1])
                self.assertEqual(submitted.suffix, ".pdf")
                pdf = submitted.read_bytes()
                match = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)\s*\]", pdf)
                self.assertIsNotNone(match)
                width, height = map(float, match.groups())
                self.assertAlmostEqual(width, 283.44)
                self.assertAlmostEqual(height, 419.52)
                return subprocess.CompletedProcess(cmd, 0, "accepted", "")

            with (patch.object(printer, "resolve_printer_name", return_value=("CP1500", "")),
                  patch.object(printer.subprocess, "run", side_effect=capture)):
                self.assertTrue(printer.print_photo(path, actual_size=True))

    def test_standard_job_retains_jpeg_and_fit_to_page(self):
        path = Path("/tmp/normal-sheet.jpg")
        with (patch.object(printer, "resolve_printer_name", return_value=("CP1500", "")),
              patch.object(printer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as lp):
            self.assertTrue(printer.print_photo(path, 2))
        cmd = lp.call_args.args[0]
        self.assertIn("fit-to-page", cmd)
        self.assertEqual(cmd[-1], str(path))


if __name__ == "__main__":
    unittest.main()
