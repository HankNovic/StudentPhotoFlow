from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from photo_pipeline import (  # noqa: E402
    PipelineOptions,
    _load_haar_cascade,
    run_pipeline,
    save_pipeline_stages,
)


def encoded_image(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (240, 320), color)
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=95)
    return stream.getvalue()


class PhotoPipelineTests(unittest.TestCase):
    def test_haar_fallback_loads_from_memory_when_path_loader_fails(self) -> None:
        import cv2

        class EmptyCascade:
            @staticmethod
            def empty() -> bool:
                return True

        class PathFailingCv2:
            data = cv2.data
            FileStorage = cv2.FileStorage
            FILE_STORAGE_READ = cv2.FILE_STORAGE_READ
            FILE_STORAGE_MEMORY = cv2.FILE_STORAGE_MEMORY
            error = cv2.error

            @staticmethod
            def CascadeClassifier(path: str | None = None):
                return EmptyCascade() if path is not None else cv2.CascadeClassifier()

        cascade = _load_haar_cascade(PathFailingCv2)
        self.assertFalse(cascade.empty())

    def test_grayscale_photo_is_rejected_and_stages_are_saved(self) -> None:
        options = PipelineOptions(
            quality_enabled=True,
            auto_orient=False,
            check_grayscale=True,
            check_face=False,
            check_glare=False,
            check_recapture=False,
            background_mode="none",
        )
        result = run_pipeline(encoded_image((145, 145, 145)), options)
        self.assertEqual(result.status, "rejected")
        self.assertIn("MONOCHROME_OR_GRAYSCALE", {reason["code"] for reason in result.reasons})
        with tempfile.TemporaryDirectory() as temp_dir:
            saved = save_pipeline_stages(result, Path(temp_dir))
            self.assertEqual(len(saved), len(result.stages))
            self.assertTrue(all(Path(item["file"]).is_file() for item in saved))

    def test_color_photo_passes_when_model_based_checks_are_disabled(self) -> None:
        options = PipelineOptions(
            quality_enabled=True,
            auto_orient=False,
            check_grayscale=True,
            check_face=False,
            check_glare=False,
            check_recapture=False,
            background_mode="none",
        )
        result = run_pipeline(encoded_image((70, 125, 210)), options)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.detector, "yunet")
        self.assertIsNotNone(result.output_bytes)


if __name__ == "__main__":
    unittest.main()
