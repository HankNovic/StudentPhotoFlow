import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_reviewed_photos import DeliveryError, export_delivery, roster_overview
from photo_exporter import GalleryReportServer, PhotoExporterApp
from photo_review import atomic_json, mark_review, output_write_lock, student_detail, save_roster, set_review_enabled
from test_photo_review import fixture


def mark(root, sid, decision):
    detail = student_detail(root, sid)
    return mark_review(root, sid, decision, detail["version"], detail["revision"])


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.report = fixture(self.root)

    def test_empty_review_file_not_required_and_query_is_read_only(self):
        overview = roster_overview(self.root)
        self.assertEqual(overview["groups"]["all"]["count"], 4)
        self.assertEqual(overview["groups"]["remaining"]["count"], 4)
        self.assertEqual(overview["groups"]["awaiting_review"]["count"], 3)
        self.assertFalse((self.root / "review_state.json").exists())
        exported = export_delivery(self.root, self.root.parent / "delivery", lists_only=True)
        self.assertEqual(exported["processed_not_approved"], overview["groups"]["processed_remaining"]["count"])

    def test_groups_match_deliveries_and_separate_manual_and_machine_rejection(self):
        mark(self.root, "2600000001", "approved")
        mark(self.root, "2600000002", "rejected")
        state = json.loads((self.root / "export_state.json").read_text(encoding="utf-8"))
        state["records"]["2600000003"]["processing"].update(status="rejected", message="无人脸")
        atomic_json(self.root / "export_state.json", state)
        overview = roster_overview(self.root)
        groups = overview["groups"]
        for key, count in {"all": 4, "approved": 1, "remaining": 3, "rejected": 1,
                           "machine_rejected": 1, "processed_remaining": 2, "not_exported": 1}.items():
            self.assertEqual(groups[key]["count"], count, key)
        manual = next(row for row in overview["rows"] if row["student_id"] == "2600000002")
        self.assertNotIn("machine_rejected", manual["groups"])
        exported = export_delivery(self.root, self.root.parent / "delivery", lists_only=True)
        folder = Path(exported["output_dir"])
        for key, name in [("approved", "当前有效审核通过学号.txt"), ("remaining", "未审核通过学号.txt"),
                          ("processed_remaining", "已处理但未审核通过学号.txt"), ("no_evidence", "未发现处理记录学号.txt")]:
            actual = (folder / name).read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(actual, [row["student_id"] for row in overview["rows"] if key in row["groups"]])

    def test_stale_approval_is_reported_not_automatically_restored(self):
        mark(self.root, "2600000001", "approved")
        path = self.root / "原始图片/2600000001.jpg"
        path.write_bytes(path.read_bytes() + b"new")
        overview = roster_overview(self.root)
        self.assertEqual(overview["groups"]["approved"]["count"], 0)
        self.assertEqual(overview["groups"]["old_approved"]["count"], 1)
        self.assertEqual(overview["groups"]["stale"]["count"], 1)

    def test_busy_source_cannot_produce_mixed_statistics(self):
        with output_write_lock(self.root), self.assertRaises(DeliveryError):
            roster_overview(self.root)

    def test_cross_batch_api_roster_counts_token_and_no_raw_state_exposure(self):
        mark(self.root, "2600000002", "rejected")
        server = GalleryReportServer(lambda *_: (True, "test"))
        self.addCleanup(server.close)
        url = server.register(self.report)
        base, tail = url.split("/reports/")
        token = tail.split("/")[0]
        endpoint = base + "/api/review?" + urllib.parse.urlencode({"action": "overview", "report_token": token})
        data = json.loads(urllib.request.urlopen(endpoint).read())
        self.assertEqual(len(data["rows"]), 4)  # Latest batch contains just one student.
        self.assertEqual(data["groups"]["rejected"]["count"], 1)
        html = urllib.request.urlopen(url).read().decode()
        self.assertIn('id="spf-overview"', html)
        self.assertIn('id="spf-roster-csv"', html)
        self.assertEqual(html.count('id="spf-shared-search"'), 1)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(endpoint.replace(token, "bad"))
        with output_write_lock(self.root), self.assertRaises(urllib.error.HTTPError) as busy:
            urllib.request.urlopen(endpoint)
        self.assertEqual(busy.exception.code, 409)

    def test_integrated_cli_delivery_modes_do_not_need_excel_or_separate_python_script(self):
        mark(self.root, "2600000001", "approved")
        script = Path(__file__).resolve().parents[1] / "photo_exporter.py"
        target = self.root.parent / "delivery"
        for mode in ["initial", "lists", "incremental"]:
            result = subprocess.run([sys.executable, str(script), "--review-export", mode, "--output", str(self.root),
                                     "--delivery-output", str(target)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(len(list(target.glob("初次导入_*/审核通过照片/2600000001.jpg"))), 1)
        self.assertTrue((target / "新增(1)/审核通过学号(1).txt").is_file())
        self.assertEqual(list((target / "新增(1)").glob("*.jpg")), [])

    def test_desktop_delivery_button_dispatches_lists_only_and_uses_current_source(self):
        app = PhotoExporterApp.__new__(PhotoExporterApp)
        app.busy = False
        app.output_var = MagicMock()
        app.output_var.get.return_value = str(self.root)
        app.root = MagicMock()
        app.filedialog = MagicMock()
        app.filedialog.askdirectory.return_value = str(self.root.parent / "delivery")
        app.messagebox = MagicMock()
        app._set_busy = MagicMock()
        app._append_log = MagicMock()
        app.progress = MagicMock()
        app.events = MagicMock()
        with patch("photo_exporter.choose_mode", return_value="lists"), patch("photo_exporter.export_delivery", return_value={}) as export:
            with patch("photo_exporter.threading.Thread") as thread:
                thread.return_value.start.side_effect = lambda: thread.call_args.kwargs["target"]()
                app.export_review_delivery()
        self.assertTrue(export.call_args.kwargs["lists_only"])
        self.assertEqual(export.call_args.args[0], self.root)
        self.assertEqual(app.current_operation, "delivery")
        app.events.put.assert_called_with(("delivery_done", {}))


def demo(root):
    report = fixture(root)
    mark(root, "2600000001", "approved")
    mark(root, "2600000002", "rejected")
    state = json.loads((root / "export_state.json").read_text(encoding="utf-8"))
    state["records"]["2600000003"]["processing"].update(status="warning", message="模拟 API 超时：请重试，非学生照片质量问题")
    atomic_json(root / "export_state.json", state)
    # Exercise pagination and full-filter downloads with synthetic, unsubmitted IDs.
    save_roster(root, ["2600000001", "2600000002", "2600000003", "2600000004"] +
                [f"269999{i:04d}" for i in range(23)], confirmed_complete=True)
    set_review_enabled(root, True)
    return report


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        with tempfile.TemporaryDirectory(prefix="spf-overview-demo-") as directory:
            server = GalleryReportServer(lambda *_: (True, "仅测试，不处理照片"))
            print(server.register(demo(Path(directory))), flush=True)
            try:
                threading.Event().wait()
            finally:
                server.close()
    else:
        unittest.main()
