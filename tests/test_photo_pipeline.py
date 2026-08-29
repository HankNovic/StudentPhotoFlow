from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from photo_pipeline import (  # noqa: E402
    PipelineOptions,
    _crop_and_resize,
    _load_haar_cascade,
    _orient_image,
    run_pipeline,
    save_pipeline_stages,
)


def encoded_image(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (240, 320), color)
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=95)
    return stream.getvalue()


class PhotoPipelineTests(unittest.TestCase):
    def test_orientation_keeps_upright_photo_when_180_score_is_only_slightly_higher(self) -> None:
        image = Image.new("RGB", (864, 1166), (60, 130, 210))
        detections = [
            ([{"box": [206, 296, 459, 614], "score": 0.8474}], "yunet"),
            ([], "yunet"),
            ([{"box": [177, 383, 347, 475], "score": 0.8540}], "yunet"),
            ([], "yunet"),
        ]
        with patch("photo_pipeline._detect_faces", side_effect=detections):
            oriented, angle, faces, detector, metrics = _orient_image(image, PipelineOptions())
        self.assertEqual(angle, 0)
        self.assertEqual(oriented.size, image.size)
        self.assertEqual(len(faces), 1)
        self.assertEqual(detector, "yunet")
        self.assertEqual(metrics["decision"], "kept_original_due_to_rotation_safety")

    def test_orientation_rotates_sideways_photo_on_clear_confidence_advantage(self) -> None:
        image = Image.new("RGB", (476, 372), (60, 130, 210))
        detections = [
            ([{"box": [141, 49, 228, 235], "score": 0.7586}], "yunet"),
            ([{"box": [86, 110, 187, 233], "score": 0.9392}], "yunet"),
            ([{"box": [105, 83, 210, 214], "score": 0.8325}], "yunet"),
            ([{"box": [110, 134, 192, 218], "score": 0.8131}], "yunet"),
        ]
        with patch("photo_pipeline._detect_faces", side_effect=detections):
            oriented, angle, faces, _detector, metrics = _orient_image(image, PipelineOptions())
        self.assertEqual(angle, 90)
        self.assertEqual(oriented.size, (372, 476))
        self.assertEqual(len(faces), 1)
        self.assertEqual(metrics["decision"], "rotated_with_clear_advantage")

    def test_final_crop_outputs_exact_custom_dimensions(self) -> None:
        image = Image.new("RGB", (864, 1166), (60, 130, 210))
        cropped, metrics = _crop_and_resize(
            image,
            [{"box": [206, 296, 459, 614], "score": 0.95}],
            295,
            413,
        )
        self.assertEqual(cropped.size, (295, 413))
        self.assertEqual(metrics["crop_anchor"], "largest_face")
        self.assertEqual(metrics["target_width"], 295)
        self.assertEqual(metrics["target_height"], 413)

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

    def test_pipeline_crop_stage_has_exact_output_size(self) -> None:
        options = PipelineOptions(
            quality_enabled=False,
            background_mode="none",
            crop_enabled=True,
            crop_width=295,
            crop_height=413,
        )
        result = run_pipeline(encoded_image((70, 125, 210)), options)
        self.assertIsNotNone(result.output_bytes)
        output = Image.open(io.BytesIO(result.output_bytes))
        self.assertEqual(output.size, (295, 413))
        self.assertEqual(result.stages[-1].code, "crop")
        self.assertEqual(result.stages[-1].status, "adjusted")


if __name__ == "__main__":
    unittest.main()
