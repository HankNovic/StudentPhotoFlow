import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photo_review import mark_review, student_detail, atomic_json
from photo_pipeline import PipelineOptions
from xlsx_photo_core import (ExportOptions, ProcessingOptions, InspectionReport, SelectedRow, SourceRef,
                             run_export, run_processing)
from test_photo_review import fixture


class RefreshArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root)
        self.sid = "2600000001"
        detail = student_detail(self.root, self.sid)
        mark_review(self.root, self.sid, "approved", detail["version"], detail["revision"])
        self.original = self.root / "原始图片" / (self.sid + ".jpg")
        self.data = self.original.read_bytes()
        self.workbook = self.root / "test.xlsx"
        self.workbook.write_bytes(b"mocked selection")

    def refresh(self, data=None, force=True, fingerprint="test-0"):
        row = SelectedRow(2, self.sid, SourceRef(kind="file", fingerprint=fingerprint, display="test"))
        selection = InspectionReport(str(self.workbook), "sheet", 1, ["学号", "照片"], [row], {})
        options = ExportOptions(self.workbook, self.root, "sheet", 1, 0, 1, force_refresh=force)
        with patch("xlsx_photo_core.inspect_selection", return_value=selection), patch("xlsx_photo_core._fetch_source", return_value=self.data if data is None else data):
            return run_export(options)

    def test_identical_force_download_preserves_snapshot_files_and_force_processing_skip(self):
        before = student_detail(self.root, self.sid)
        result = self.refresh()
        after = student_detail(self.root, self.sid)
        self.assertEqual(result.summary["updated"], 0)
        self.assertEqual(result.summary["unchanged"], 1)
        self.assertTrue(result.results[0].executed)  # fetched and checkpointed, but not rewritten
        self.assertEqual(before["snapshot"], after["snapshot"])
        self.assertEqual(after["status"], "approved")
        self.assertFalse((self.root / "历史版本").exists())
        with patch("xlsx_photo_core.run_pipeline", side_effect=AssertionError("approved photo must not process")):
            processed = run_processing(ProcessingOptions(self.root, PipelineOptions(quality_enabled=True),
                force_process=True, selected_student_ids=[self.sid]))
        self.assertEqual(processed.summary["archived_skipped"], 1)
        self.assertEqual(processed.summary["reprocessed"], 0)

    def test_changed_source_link_with_same_photo_preserves_review(self):
        before = student_detail(self.root, self.sid)["snapshot"]
        self.refresh(force=False, fingerprint="different-link")
        self.assertEqual(student_detail(self.root, self.sid)["snapshot"], before)
        state = json.loads((self.root / "export_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["records"][self.sid]["source_fingerprint"], "different-link")

    def test_actual_new_photo_invalidates_review_and_retains_archive(self):
        before = student_detail(self.root, self.sid)
        archive = self.root / before["archive_file"]
        archived_bytes = archive.read_bytes()
        result = self.refresh(self.data + b"changed")
        after = student_detail(self.root, self.sid)
        self.assertEqual(result.summary["updated"], 1)
        self.assertEqual(after["status"], "stale")
        self.assertEqual(after["processing_status"], "pending")
        self.assertEqual(archive.read_bytes(), archived_bytes)
        self.assertFalse((self.root / before["result_file"]).exists())
        self.assertTrue(list((self.root / "历史版本").rglob(self.sid + ".jpg")))

    def test_disk_tampering_is_not_accepted_just_because_stored_digest_matches_download(self):
        self.original.write_bytes(self.data + b"tampered")
        result = self.refresh()
        self.assertEqual(result.summary["updated"], 1)
        self.assertEqual(student_detail(self.root, self.sid)["status"], "stale")

    def test_new_bytes_matching_manually_replaced_disk_still_invalidate_old_review(self):
        new_bytes = self.data + b"changed"
        self.original.write_bytes(new_bytes)
        result = self.refresh(new_bytes)
        self.assertEqual(result.summary["updated"], 1)
        self.assertEqual(student_detail(self.root, self.sid)["processing_status"], "pending")

    def test_unreviewed_processing_result_is_preserved_too(self):
        state = {"schema_version": 1, "reviews": {}, "actions": []}
        atomic_json(self.root / "review_state.json", state)
        before = student_detail(self.root, self.sid)["snapshot"]
        self.refresh()
        self.assertEqual(student_detail(self.root, self.sid)["snapshot"], before)
