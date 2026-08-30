from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from photo_exporter import GalleryReportServer
from photo_pipeline import PipelineOptions
from photo_review import (
    ReviewError, archive_context, atomic_json, is_archived, load_reviews, load_roster,
    mark_review, output_write_lock, parse_roster_text, read_roster_file, review_queue,
    save_roster, set_review_enabled, student_detail, undo_review, validate_roster,
)
from review_web import enhance_gallery_html, review_page, search_markup
from xlsx_photo_core import JobResult, ProcessingOptions, _write_gallery, run_processing


def fixture(root: Path, enabled: bool = True) -> Path:
    records = {}
    for index, sid in enumerate(["2600000001", "2600000002", "2600000003"]):
        for folder in ["原始图片", "处理后图片"]:
            path = root / folder / f"{sid}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (295, 413), [(70, 130, 210), (190, 120, 75), (100, 170, 130)][index]).save(path)
        records[sid] = {
            "student_id": sid, "row": index + 2, "source_fingerprint": f"test-{index}",
            "source_display": "模拟数据", "original_file": f"原始图片/{sid}.jpg",
            "original_sha256": hashlib.sha256((root / "原始图片" / f"{sid}.jpg").read_bytes()).hexdigest(),
            "last_batch_id": "test-old",
            "processing": {"status": "success", "processed_file": f"处理后图片/{sid}.jpg",
                           "message": "模拟处理完成", "last_processed_at": "2026-08-31T10:00:00",
                           "config_fingerprint": "test-config", "step_files": [
                               {"label": "模拟处理步骤", "file": f"原始图片/{sid}.jpg", "status": "passed"}]},
        }
    # The latest report intentionally contains only one student; search/review must span all.
    batch = root / "批次记录" / "test-latest"
    batch.mkdir(parents=True, exist_ok=True)
    item = JobResult(student_id="2600000001", row_number=2, change="reprocessed", status="success")
    _write_gallery(batch, "test-latest", [item], {}, {})
    atomic_json(batch / "batch.json", {"results": [{"student_id": "2600000001"}]})
    atomic_json(root / "export_state.json", {"schema_version": 3, "records": records, "batches": []})
    save_roster(root, list(records) + ["2600000004"], confirmed_complete=True)
    if enabled:
        set_review_enabled(root, True)
    return batch / "index.html"


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.report = fixture(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def mark(self, sid="2600000001", decision="approved") -> dict:
        detail = student_detail(self.root, sid)
        return mark_review(self.root, sid, decision, detail["version"], detail["revision"])

    def test_roster_validation_and_complete_confirmation(self):
        self.assertEqual(parse_roster_text("学号\n001,002；003"), ["001", "002", "003"])
        for invalid in [[], ["001", "001"], ["001", ""], ["../photo"], [None], "001"]:
            with self.subTest(invalid=invalid), self.assertRaises(ReviewError):
                validate_roster(invalid)
        with self.assertRaises(ReviewError):
            save_roster(self.root, ["001"], confirmed_complete=False)
        other = self.root / "new-output"
        with self.assertRaises(ReviewError):
            set_review_enabled(other, True)
        save_roster(other, ["001"], confirmed_complete=True)
        self.assertFalse(load_roster(other)["enabled"])

    def test_csv_and_xlsx_roster_import_preserves_leading_zeroes(self):
        path = self.root / "roster.csv"
        path.write_text("姓名,学号\n测试甲,0001\n测试乙,0002\n", encoding="utf-8-sig")
        self.assertEqual(read_roster_file(path), ["0001", "0002"])
        path.write_text("姓名,学号\n测试甲,\n", encoding="utf-8-sig")
        with self.assertRaises(ReviewError):
            read_roster_file(path)
        # Exercise the XLSX adapter using actual workbook cell parsing, without extra dependencies.
        import zipfile
        path = self.root / "roster.xlsx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            zf.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="名单" sheetId="1" r:id="rId1"/></sheets></workbook>')
            zf.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            zf.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B1" t="inlineStr"><is><t>学号</t></is></c></row><row r="2"><c r="B2" t="inlineStr"><is><t>0001</t></is></c></row></sheetData></worksheet>')
        self.assertEqual(read_roster_file(path), ["0001"])

    def test_save_replacement_disables_gate_but_keeps_approval(self):
        self.mark()
        save_roster(self.root, ["2600000001"], confirmed_complete=True)
        self.assertFalse(load_roster(self.root)["enabled"])
        self.assertEqual(load_reviews(self.root)["reviews"]["2600000001"]["status"], "approved")
        with self.assertRaises(ReviewError):
            self.mark()

    def test_approval_is_durable_copy_and_does_not_modify_processing(self):
        before = (self.root / "export_state.json").read_bytes()
        self.mark()
        detail = student_detail(self.root, "2600000001")
        self.assertEqual(detail["status"], "approved")
        self.assertEqual((self.root / detail["archive_file"]).read_bytes(), (self.root / detail["result_file"]).read_bytes())
        self.assertEqual(before, (self.root / "export_state.json").read_bytes())
        record = json.loads(before)["records"]["2600000001"]
        self.assertTrue(is_archived(self.root, "2600000001", record, archive_context(self.root)))

    def test_force_and_selected_processing_never_run_archived_versions(self):
        for sid in ["2600000001", "2600000002", "2600000003"]:
            self.mark(sid)
        before = (self.root / "处理后图片/2600000001.jpg").read_bytes()
        with patch("xlsx_photo_core.run_pipeline") as pipeline:
            result = run_processing(ProcessingOptions(output_dir=self.root, force_process=True,
                pipeline=PipelineOptions(crop_enabled=True, crop_width=123, crop_height=234),
                selected_student_ids=["2600000001"], workers=1))
            self.assertEqual(result.summary["archived_skipped"], 1)
            self.assertEqual(result.summary["reprocessed"], 0)
            result = run_processing(ProcessingOptions(output_dir=self.root, pipeline=PipelineOptions(crop_enabled=True), force_process=True, workers=1))
            self.assertEqual(result.summary["archived_skipped"], 3)
            pipeline.assert_not_called()
        self.assertEqual(before, (self.root / "处理后图片/2600000001.jpg").read_bytes())
        self.assertEqual(student_detail(self.root, "2600000001")["status"], "approved")

    def test_changed_original_or_result_invalidates_old_approval_even_with_same_stat(self):
        self.mark()
        record = json.loads((self.root / "export_state.json").read_text(encoding="utf-8"))["records"]["2600000001"]
        original = self.root / record["original_file"]
        stat = original.stat()
        data = original.read_bytes()
        original.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertFalse(is_archived(self.root, "2600000001", record, archive_context(self.root)))
        self.assertEqual(student_detail(self.root, "2600000001")["status"], "stale")
        original.write_bytes(data)
        os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        (self.root / record["processing"]["processed_file"]).write_bytes(b"changed")
        self.assertFalse(is_archived(self.root, "2600000001", record, archive_context(self.root)))

    def test_reject_skip_and_undo_restore_state_in_order(self):
        approval = self.mark()
        rejected = self.mark(decision="rejected")
        skipped = self.mark("2600000002", "skipped")
        self.assertFalse((self.root / "审核归档/2600000002").exists())
        with self.assertRaises(ReviewError):
            undo_review(self.root, rejected["action_id"])
        undo_review(self.root, skipped["action_id"])
        undo_review(self.root, rejected["action_id"])
        self.assertEqual(student_detail(self.root, "2600000001")["status"], "approved")
        archive = student_detail(self.root, "2600000001")["archive_file"]
        undo_review(self.root, approval["action_id"])
        self.assertEqual(student_detail(self.root, "2600000001")["status"], "pending")
        self.assertTrue((self.root / archive).is_file())

    def test_changed_original_is_reprocessed_without_force_even_with_same_parameters(self):
        options = ProcessingOptions(output_dir=self.root, pipeline=PipelineOptions(crop_enabled=True),
                                    selected_student_ids=["2600000001"], workers=1)
        run_processing(options)
        self.mark()
        Image.new("RGB", (300, 420), "white").save(self.root / "原始图片/2600000001.jpg")
        result = run_processing(options)
        self.assertEqual(result.summary["reprocessed"], 1)
        self.assertEqual(result.summary["archived_skipped"], 0)
        self.assertEqual(student_detail(self.root, "2600000001")["status"], "stale")
        result = run_processing(options)
        self.assertEqual(result.summary["reprocessed"], 0)
        self.assertEqual(result.summary["unchanged"], 1)

    def test_stale_tabs_and_busy_writer_cannot_overwrite_review(self):
        detail = student_detail(self.root, "2600000001")
        self.mark()
        with self.assertRaises(ReviewError):
            mark_review(self.root, "2600000001", "rejected", detail["version"], detail["revision"])
        with output_write_lock(self.root):
            with self.assertRaises(ReviewError):
                self.mark("2600000002")
            with self.assertRaises(ReviewError):
                run_processing(ProcessingOptions(output_dir=self.root, pipeline=PipelineOptions()))
        self.mark("2600000002")  # lock was released

    def test_missing_failed_results_and_outside_roster_cannot_be_approved(self):
        with self.assertRaises(ReviewError):
            self.mark("2600000004")
        with self.assertRaises(ReviewError):
            self.mark("outside")
        state_path = self.root / "export_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["records"]["2600000001"]["processing"] = {"status": "warning", "message": "API 超时"}
        atomic_json(state_path, state)
        self.assertFalse(student_detail(self.root, "2600000001")["can_approve"])
        with self.assertRaises(ReviewError):
            self.mark()
        self.mark(decision="rejected")

    def test_queue_spans_batches_and_persists_marks(self):
        self.mark()
        queue = review_queue(self.root)
        self.assertEqual(queue["student_ids"], ["2600000002", "2600000003"])
        self.assertEqual(queue["counts"]["missing"], 1)
        self.assertEqual(queue["counts"]["approved"], 1)
        self.assertEqual(queue["statuses"]["2600000001"], "approved")
        self.assertFalse(student_detail(self.root, "2600000004")["exists"])

    def test_corrupt_persisted_state_fails_closed(self):
        (self.root / "review_state.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(ReviewError):
            run_processing(ProcessingOptions(output_dir=self.root, pipeline=PipelineOptions(crop_enabled=True)))

    def test_shared_component_and_review_buttons(self):
        original = '<html><head></head><body><header></header></body></html>'
        enhanced = enhance_gallery_html(original)
        self.assertEqual(enhance_gallery_html(enhanced), enhanced)
        self.assertIn(search_markup("gallery"), enhanced)
        page = review_page()
        self.assertIn(search_markup("review"), page)
        for button in ["review-yes", "review-previous", "review-no", "review-skip", "review-undo"]:
            self.assertIn(f'id="{button}"', page)

    def test_local_review_api_token_cross_batch_search_mutation_and_paths(self):
        server = GalleryReportServer(lambda *_: (True, "queued"))
        self.addCleanup(server.close)
        url = server.register(self.report)
        token = url.split("/reports/")[1].split("/")[0]
        base = url.split("/reports/")[0]

        def get(action, **args):
            query = urllib.parse.urlencode(dict(report_token=token, action=action, **args))
            return json.loads(urllib.request.urlopen(base + "/api/review?" + query).read())

        def post(payload, **headers):
            request = urllib.request.Request(base + "/api/review", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **headers})
            return json.loads(urllib.request.urlopen(request).read())

        detail = get("student", student_id="2600000002")
        self.assertTrue(detail["can_approve"])
        payload = dict(report_token=token, action="mark", student_id="2600000002", decision="approved",
                       expected_version=detail["version"], expected_revision=detail["revision"])
        result = post(payload)
        self.assertEqual(result["status"], "approved")
        for invalid in [[], {**payload, "report_token": "invalid"}, payload]:
            with self.subTest(invalid=invalid), self.assertRaises(urllib.error.HTTPError):
                post(invalid)
        with self.assertRaises(urllib.error.HTTPError):
            post(payload, Origin="https://outside.invalid")
        for suffix in ["output/export_state.json", "output/../outside.jpg", "output/C:%5CWindows%5Cwin.ini"]:
            with self.subTest(suffix=suffix), self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(url.rsplit("/", 1)[0] + "/" + suffix)
        html = urllib.request.urlopen(url).read().decode()
        self.assertIn('id="spf-shared-search"', html)
        self.assertIn('id="review-yes"', urllib.request.urlopen(url.replace("index.html", "review.html")).read().decode())
        self.assertEqual(post(dict(report_token=token, action="undo", action_id=result["action_id"]))["status"], "undone")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        # Isolated disposable data for manual browser checks; never touches user output.
        with tempfile.TemporaryDirectory(prefix="studentphotoflow-review-test-") as directory:
            server = GalleryReportServer(lambda *_: (True, "测试队列：不执行图片处理"))
            print(server.register(fixture(Path(directory))), flush=True)
            try:
                threading.Event().wait()
            finally:
                server.close()
    else:
        unittest.main()
