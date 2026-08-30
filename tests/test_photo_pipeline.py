from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from photo_pipeline import (  # noqa: E402
    PipelineOptions,
    _crop_and_resize,
    _hivision_replace_background,
    _load_haar_cascade,
    _orient_image,
    run_pipeline,
    save_pipeline_stages,
    test_hivision_api,
)
from photo_exporter import (  # noqa: E402
    GalleryReportServer,
    load_portable_settings,
    pipeline_options_from_settings,
    save_portable_settings,
)
from xlsx_photo_core import (  # noqa: E402
    ExportOptions,
    JobResult,
    ProcessingOptions,
    SelectedRow,
    SourceRef,
    _execute_job,
    _execute_job_after_resume,
    _write_gallery,
    run_processing,
)


def encoded_image(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (240, 320), color)
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=95)
    return stream.getvalue()


class PhotoPipelineTests(unittest.TestCase):
    def test_portable_settings_round_trip(self) -> None:
        settings = {
            "background_mode": "不处理",
            "quality_enabled": True,
            "crop_width": 295,
            "hivision_url": "https://photo-api.example.com",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "StudentPhotoFlow.settings.json"
            save_portable_settings(path, settings)
            self.assertEqual(load_portable_settings(path), settings)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)

    def test_default_pipeline_does_not_replace_background(self) -> None:
        self.assertEqual(PipelineOptions().background_mode, "none")

    def test_saved_settings_build_the_same_processing_pipeline(self) -> None:
        pipeline = pipeline_options_from_settings({
            "quality_enabled": True,
            "background_mode": "Hivision API（可选）",
            "color_preset": "白色 #FFFFFF",
            "hivision_timeout": 45,
            "crop_enabled": True,
            "crop_width": 295,
            "crop_height": 413,
        })
        self.assertTrue(pipeline.quality_enabled)
        self.assertEqual(pipeline.background_mode, "hivision")
        self.assertEqual(pipeline.background_color, "#FFFFFF")
        self.assertEqual(pipeline.hivision_timeout, 45)
        self.assertTrue(pipeline.crop_enabled)

    def test_portable_launcher_does_not_wait_for_gui_exit(self) -> None:
        launcher = (PROJECT_ROOT / "启动工具.bat").read_text(encoding="utf-8")
        self.assertIn('start "" "%APP_EXE%" %*', launcher)
        self.assertNotIn("/wait", launcher.lower())

    def test_hivision_api_check_reads_openapi_without_uploading_photo(self) -> None:
        fields = {
            name: {"type": "string"}
            for name in [
                "input_image", "height", "width", "human_matting_model", "face_detect_model",
                "hd", "dpi", "face_align", "head_measure_ratio", "head_height_ratio",
                "top_distance_max", "top_distance_min", "brightness_strength",
                "contrast_strength", "sharpen_strength", "saturation_strength",
            ]
        }
        response_body = json.dumps({
            "info": {"title": "HivisionIDPhotos API", "version": "1.2.3"},
            "paths": {
                "/idphoto": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {"$ref": "#/components/schemas/IdPhotoForm"}
                                }
                            }
                        }
                    }
                }
            },
            "components": {"schemas": {"IdPhotoForm": {"properties": fields}}},
        }).encode("utf-8")
        captured: dict[str, object] = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self, _limit: int) -> bytes:
                return response_body

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("photo_pipeline.urllib.request.urlopen", side_effect=fake_urlopen):
            result = test_hivision_api("https://photo.example/idphoto", 12)

        request = captured["request"]
        self.assertEqual(request.full_url, "https://photo.example/openapi.json")
        self.assertIsNone(request.data)
        self.assertEqual(captured["timeout"], 12)
        self.assertTrue(result["ok"])
        self.assertEqual(result["endpoint"], "https://photo.example/idphoto")
        self.assertEqual(result["missing_optional_fields"], [])

    def test_export_job_only_writes_original_even_when_processing_options_are_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            source.write_bytes(encoded_image((70, 125, 210)))
            row = SelectedRow(
                row_number=2,
                student_id="2600000001",
                source=SourceRef(kind="file", value=str(source), hint=".jpg", fingerprint="source-1"),
            )
            options = ExportOptions(
                xlsx_path=root / "unused.xlsx",
                output_dir=root / "output",
                sheet_name="Sheet1",
                header_row=1,
                id_col=0,
                image_col=1,
                quality_enabled=True,
                background_mode="ai",
                crop_enabled=True,
            )
            with patch("xlsx_photo_core.run_pipeline") as pipeline:
                result = _execute_job(row, "new", None, options, "batch")
            pipeline.assert_not_called()
            self.assertEqual(result.status, "success")
            self.assertEqual(result.processing_status, "pending")
            self.assertIsNone(result.processed_file)
            self.assertTrue((options.output_dir / result.original_file).is_file())

    def test_processing_stage_reads_exported_original_without_fetching_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_dir = root / "原始图片"
            original_dir.mkdir(parents=True)
            original_path = original_dir / "2600000002.jpg"
            original_path.write_bytes(encoded_image((70, 125, 210)))
            state = {
                "schema_version": 3,
                "app_version": "1.5.0",
                "records": {
                    "2600000002": {
                        "student_id": "2600000002",
                        "row": 2,
                        "source_fingerprint": "source-2",
                        "source_display": "https://example.invalid/photo.jpg",
                        "original_file": "原始图片/2600000002.jpg",
                        "original_sha256": "old",
                        "processing": {"status": "pending"},
                    }
                },
                "batches": [],
            }
            (root / "export_state.json").write_text(json.dumps(state), encoding="utf-8")
            options = ProcessingOptions(
                output_dir=root,
                pipeline=PipelineOptions(
                    quality_enabled=False,
                    background_mode="none",
                    crop_enabled=True,
                    crop_width=295,
                    crop_height=413,
                ),
                workers=1,
            )
            with patch("xlsx_photo_core._fetch_source") as fetch:
                result = run_processing(options)
            fetch.assert_not_called()
            self.assertEqual(result.operation, "process")
            self.assertEqual(result.summary["reprocessed"], 1)
            with Image.open(root / "处理后图片" / "2600000002.jpg") as output:
                self.assertEqual(output.size, (295, 413))
            batch = json.loads((result.batch_dir / "batch.json").read_text(encoding="utf-8"))
            self.assertEqual(batch["operation"], "process")

    def test_processing_resumes_only_unfinished_students_after_forced_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_dir = root / "原始图片"
            original_dir.mkdir(parents=True)
            records: dict[str, object] = {}
            for index, student_id in enumerate(("2600000101", "2600000102"), start=2):
                original = original_dir / f"{student_id}.jpg"
                original.write_bytes(encoded_image((70, 125, 210)))
                records[student_id] = {
                    "student_id": student_id,
                    "row": index,
                    "source_fingerprint": f"source-{index}",
                    "source_display": "test",
                    "original_file": f"原始图片/{student_id}.jpg",
                    "original_sha256": "old",
                    "processing": {"status": "pending"},
                }
            state = {
                "schema_version": 3,
                "app_version": "1.6.0",
                "records": records,
                "batches": [],
            }
            state_path = root / "export_state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            options = ProcessingOptions(
                output_dir=root,
                pipeline=PipelineOptions(
                    quality_enabled=True,
                    auto_orient=False,
                    check_grayscale=False,
                    check_face=False,
                    check_glare=False,
                    check_recapture=False,
                    background_mode="none",
                ),
                workers=1,
            )

            first_calls: list[str] = []

            def interrupted(_event, row, existing, _options, _batch_id, _cancel_event):
                first_calls.append(row.student_id)
                if row.student_id == "2600000102":
                    raise KeyboardInterrupt("simulated forced close")
                return JobResult(
                    student_id=row.student_id,
                    row_number=row.row_number,
                    change="reprocessed",
                    status="success",
                    original_file=existing["original_file"],
                    processing_status="success",
                    quality_status="success",
                )

            with patch("xlsx_photo_core._execute_processing_job_after_resume", side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    run_processing(options)

            interrupted_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(interrupted_state["active_run"]["status"], "running")
            self.assertEqual(interrupted_state["active_run"]["completed_ids"], ["2600000101"])
            self.assertEqual(
                interrupted_state["records"]["2600000101"]["processing"]["status"],
                "success",
            )

            second_calls: list[str] = []

            def resumed(_event, row, existing, _options, _batch_id, _cancel_event):
                second_calls.append(row.student_id)
                return JobResult(
                    student_id=row.student_id,
                    row_number=row.row_number,
                    change="reprocessed",
                    status="success",
                    original_file=existing["original_file"],
                    processing_status="success",
                    quality_status="success",
                )

            with patch("xlsx_photo_core._execute_processing_job_after_resume", side_effect=resumed):
                result = run_processing(options)

            self.assertEqual(first_calls, ["2600000101", "2600000102"])
            self.assertEqual(second_calls, ["2600000102"])
            self.assertEqual(result.summary["reprocessed"], 1)
            self.assertEqual(result.summary["unchanged"], 1)
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("active_run", final_state)
            self.assertEqual(final_state["interrupted_runs"][-1]["pending_count"], 1)

    def test_manual_cancellation_keeps_pending_students_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_dir = root / "原始图片"
            original_dir.mkdir(parents=True)
            records: dict[str, object] = {}
            for row_number, student_id in enumerate(("2600000201", "2600000202"), start=2):
                (original_dir / f"{student_id}.jpg").write_bytes(encoded_image((70, 125, 210)))
                records[student_id] = {
                    "student_id": student_id,
                    "row": row_number,
                    "source_fingerprint": student_id,
                    "source_display": "test",
                    "original_file": f"原始图片/{student_id}.jpg",
                    "original_sha256": "old",
                    "processing": {"status": "pending"},
                }
            state_path = root / "export_state.json"
            state_path.write_text(json.dumps({
                "schema_version": 3,
                "app_version": "1.7.0",
                "records": records,
                "batches": [],
            }), encoding="utf-8")
            options = ProcessingOptions(
                output_dir=root,
                pipeline=PipelineOptions(
                    quality_enabled=True,
                    auto_orient=False,
                    check_grayscale=False,
                    check_face=False,
                    check_glare=False,
                    check_recapture=False,
                    background_mode="none",
                ),
                workers=1,
            )
            cancel_event = threading.Event()
            cancel_event.set()
            result = run_processing(options, cancel_event=cancel_event)

            self.assertTrue(result.cancelled)
            self.assertTrue(result.summary["cancelled"])
            self.assertEqual(result.summary["pending"], 2)
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("active_run", final_state)
            self.assertEqual(final_state["resume_runs"]["process"]["status"], "cancelled")
            self.assertEqual(final_state["resume_runs"]["process"]["pending_count"], 2)
            run_status = json.loads((result.batch_dir / "run_status.json").read_text(encoding="utf-8"))
            self.assertEqual(run_status["status"], "cancelled")

    def test_selected_processing_only_runs_requested_student_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_dir = root / "原始图片"
            original_dir.mkdir(parents=True)
            records: dict[str, object] = {}
            for row_number, student_id in enumerate(("2600000301", "2600000302"), start=2):
                (original_dir / f"{student_id}.jpg").write_bytes(encoded_image((70, 125, 210)))
                records[student_id] = {
                    "student_id": student_id,
                    "row": row_number,
                    "source_fingerprint": student_id,
                    "source_display": "test",
                    "original_file": f"原始图片/{student_id}.jpg",
                    "original_sha256": "old",
                    "processing": {"status": "pending"},
                }
            (root / "export_state.json").write_text(json.dumps({
                "schema_version": 3,
                "app_version": "1.7.0",
                "records": records,
                "batches": [],
            }), encoding="utf-8")
            options = ProcessingOptions(
                output_dir=root,
                pipeline=PipelineOptions(
                    quality_enabled=True,
                    auto_orient=False,
                    check_grayscale=False,
                    check_face=False,
                    check_glare=False,
                    check_recapture=False,
                    background_mode="none",
                ),
                workers=1,
                force_process=True,
                selected_student_ids=["2600000302"],
            )
            calls: list[str] = []

            def selected(_event, row, existing, _options, _batch_id, _cancel_event):
                calls.append(row.student_id)
                return JobResult(
                    student_id=row.student_id,
                    row_number=row.row_number,
                    change="reprocessed",
                    status="success",
                    original_file=existing["original_file"],
                    processing_status="success",
                    quality_status="success",
                )

            with patch("xlsx_photo_core._execute_processing_job_after_resume", side_effect=selected):
                result = run_processing(options)

            self.assertEqual(calls, ["2600000302"])
            self.assertEqual(result.summary["total_rows"], 1)
            self.assertEqual(result.summary["reprocessed"], 1)
            batch = json.loads((result.batch_dir / "batch.json").read_text(encoding="utf-8"))
            self.assertEqual(batch["options"]["selected_student_ids"], ["2600000302"])

    def test_gallery_has_filter_selection_csv_and_reprocess_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            batch_dir = Path(temp_dir)
            result = JobResult(
                student_id="2600000401",
                row_number=8,
                change="reprocessed",
                status="success",
                quality_status="rejected",
                processing_status="rejected",
                processing_message="无法识别人脸",
            )
            path = _write_gallery(batch_dir, "batch-test", [result], {}, {
                "reprocessed": 1,
                "quality_rejected": 1,
            })
            page = path.read_text(encoding="utf-8")
            self.assertIn('id="resultFilter"', page)
            self.assertIn('id="selectVisible"', page)
            self.assertIn('id="exportSelected"', page)
            self.assertIn('id="reprocessButton"', page)
            self.assertIn('data-student-id="2600000401"', page)
            self.assertIn("__STUDENT_PHOTO_FLOW_REPORT_TOKEN__", page)

    def test_local_gallery_server_validates_report_and_queues_reprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            batch_dir = output_root / "批次记录" / "batch-test"
            batch_dir.mkdir(parents=True)
            (batch_dir / "index.html").write_text(
                '<html>__STUDENT_PHOTO_FLOW_REPORT_TOKEN__</html>', encoding="utf-8",
            )
            (batch_dir / "batch.json").write_text(json.dumps({
                "results": [{"student_id": "2600000501"}],
            }), encoding="utf-8")
            queued: list[tuple[Path, list[str]]] = []
            server = GalleryReportServer(
                lambda root, ids: (queued.append((root, ids)) is None, "已提交"),
            )
            try:
                url = server.register(batch_dir / "index.html")
                with urllib.request.urlopen(url, timeout=3) as response:
                    page = response.read().decode("utf-8")
                self.assertNotIn("__STUDENT_PHOTO_FLOW_REPORT_TOKEN__", page)
                token = url.split("/reports/", 1)[1].split("/", 1)[0]
                request = urllib.request.Request(
                    url.rsplit("/reports/", 1)[0] + "/api/reprocess",
                    data=json.dumps({
                        "report_token": token,
                        "student_ids": ["2600000501"],
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["message"], "已提交")
                self.assertEqual(queued, [(output_root.resolve(), ["2600000501"])])
            finally:
                server.close()

    def test_hivision_sends_adjustable_parameters_and_uses_hd_response(self) -> None:
        def png_base64(size: tuple[int, int], color: tuple[int, int, int, int]) -> str:
            stream = io.BytesIO()
            Image.new("RGBA", size, color).save(stream, format="PNG")
            return base64.b64encode(stream.getvalue()).decode("ascii")

        response_body = json.dumps({
            "status": True,
            "image_base64_standard": png_base64((5, 7), (255, 0, 0, 255)),
            "image_base64_hd": png_base64((10, 14), (0, 255, 0, 255)),
        }).encode("utf-8")
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self, _limit: int) -> bytes:
                return response_body

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        options = PipelineOptions(
            background_mode="hivision",
            background_color="#438EDB",
            hivision_url="https://example.invalid/idphoto",
            hivision_timeout=45,
            hivision_matting_model="hivision_modnet",
            hivision_face_model="retinaface-resnet50",
            hivision_hd=True,
            hivision_face_align=True,
            hivision_head_measure_ratio=0.23,
            hivision_head_height_ratio=0.44,
            hivision_top_distance_max=0.13,
            hivision_top_distance_min=0.09,
            hivision_brightness_strength=1,
            hivision_contrast_strength=2,
            hivision_sharpen_strength=1,
            hivision_saturation_strength=3,
        )
        with patch("photo_pipeline.urllib.request.urlopen", side_effect=fake_urlopen):
            result = _hivision_replace_background(Image.new("RGB", (40, 50), "white"), options)

        request = captured["request"]
        self.assertEqual(request.full_url, "https://example.invalid/idphoto")
        self.assertEqual(captured["timeout"], 45)
        body = request.data
        for expected in [
            b'name="human_matting_model"', b"hivision_modnet",
            b'name="face_detect_model"', b"retinaface-resnet50",
            b'name="hd"', b"true",
            b'name="face_align"',
            b'name="head_measure_ratio"', b"0.23",
            b'name="brightness_strength"', b"1",
        ]:
            self.assertIn(expected, body)
        self.assertEqual(result.size, (10, 14))

    def test_export_job_waits_while_paused_and_continues_after_resume(self) -> None:
        class Gate:
            def __init__(self) -> None:
                self.waiting = threading.Event()
                self.release = threading.Event()

            def wait(self) -> None:
                self.waiting.set()
                self.release.wait()

        gate = Gate()
        outcome: list[object] = []
        with patch("xlsx_photo_core._execute_job", return_value="done") as execute:
            worker = threading.Thread(
                target=lambda: outcome.append(
                    _execute_job_after_resume(gate, object(), "new", None, object(), "batch")
                )
            )
            worker.start()
            self.assertTrue(gate.waiting.wait(1.0))
            execute.assert_not_called()
            gate.release.set()
            worker.join(1.0)
            self.assertFalse(worker.is_alive())
            execute.assert_called_once()
        self.assertEqual(outcome, ["done"])

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
