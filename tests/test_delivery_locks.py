import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from delivery_state import DELIVERY_LOCK_FILE, DeliveryStateError, validate_delivery_locks
from export_reviewed_photos import DeliveryError, export_delivery, roster_overview, main as delivery_main
from photo_exporter import GalleryReportServer, PhotoExporterApp, build_parser, _cli_main
from photo_pipeline import PipelineOptions
from photo_review import (ReviewError, atomic_json, import_delivery_locks, load_delivery_locks,
                          mark_review, output_write_lock, parse_roster_text, read_delivered_file,
                          review_queue, save_roster, set_review_enabled, student_detail,
                          undo_review, unlock_delivery_ids)
from xlsx_photo_core import (ExportOptions, InspectionReport, ProcessingOptions, SelectedRow,
                             SourceRef, run_export, run_processing)
from test_photo_review import fixture


class DeliveryLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.report = fixture(self.root)
        self.sid = "2600000001"
        self.other = "2600000002"
        self.destination = self.root.parent / "delivery"

    def lock(self, ids=None):
        return import_delivery_locks(self.root, ids or [self.sid], confirmed_sent=True, source="test.json")

    def mark(self, sid=None, decision="approved"):
        sid = sid or self.sid
        detail = student_detail(self.root, sid)
        return mark_review(self.root, sid, decision, detail["version"], detail["revision"])

    def process(self, ids=None):
        return run_processing(ProcessingOptions(self.root, PipelineOptions(crop_enabled=True, crop_width=100, crop_height=140),
                                                force_process=True, selected_student_ids=ids or [self.sid]))

    def test_import_requires_actual_sent_confirmation_roster_and_valid_subset(self):
        for ids, confirmed in [([self.sid], False), (["outside"], True), ([self.sid, self.sid], True), ([""], True)]:
            with self.assertRaises(ReviewError):
                import_delivery_locks(self.root, ids, confirmed_sent=confirmed)
        self.assertFalse((self.root / DELIVERY_LOCK_FILE).exists())
        (self.root / "grade_roster.json").unlink()
        with self.assertRaises(ReviewError):
            self.lock()

    def test_json_import_and_exported_state_reimport_preserve_ids(self):
        path = self.root.parent / "ids.json"
        for payload in [[self.sid], {"student_ids": [self.sid]}]:
            atomic_json(path, payload)
            self.assertEqual(read_delivered_file(path), [self.sid])
        self.lock()
        self.assertEqual(read_delivered_file(self.root / DELIVERY_LOCK_FILE), [self.sid])
        atomic_json(path, {"student_ids": [123]})
        with self.assertRaises(ReviewError):
            read_delivered_file(path)

    def test_import_idempotent_merges_and_never_rewrites_review_or_processing(self):
        self.mark()
        before = {name: (self.root / name).read_bytes() for name in ["export_state.json", "review_state.json", "grade_roster.json"]}
        self.assertEqual(self.lock()["added"], 1)
        snapshot = (self.root / DELIVERY_LOCK_FILE).read_bytes()
        self.assertEqual(self.lock()["already_locked"], 1)
        self.assertEqual((self.root / DELIVERY_LOCK_FILE).read_bytes(), snapshot)
        result = self.lock([self.sid, self.other])
        self.assertEqual((result["added"], result["total"]), (1, 2))
        self.assertEqual(len(load_delivery_locks(self.root)["actions"]), 2)
        backup = list((self.root / "交付锁定历史").glob("*.json"))
        self.assertEqual(len(backup), 1)
        self.assertEqual(json.loads(backup[0].read_text(encoding="utf-8"))["student_ids"], [self.sid])
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in before})

    def test_manual_unlock_requires_reason_current_revision_and_locked_ids(self):
        self.lock()
        state = load_delivery_locks(self.root)
        for reason, revision, ids in [("", state["revision"], [self.sid]), ("reason", "old", [self.sid]),
                                      ("reason", state["revision"], [self.other])]:
            with self.assertRaises(ReviewError):
                unlock_delivery_ids(self.root, ids, reason=reason, expected_revision=revision)
        result = unlock_delivery_ids(self.root, [self.sid], reason="更正误导入", expected_revision=state["revision"])
        self.assertEqual(result["total"], 0)
        self.assertEqual(load_delivery_locks(self.root)["actions"][-1]["reason"], "更正误导入")
        self.assertFalse(student_detail(self.root, self.sid)["delivery_locked"])
        self.assertEqual(self.lock()["added"], 1)  # Explicitly confirming sent can lock again.

    def test_import_and_unlock_respect_running_job_lock(self):
        with output_write_lock(self.root), self.assertRaises(ReviewError):
            self.lock()
        self.lock()
        with output_write_lock(self.root), self.assertRaises(ReviewError):
            unlock_delivery_ids(self.root, [self.sid], reason="test", expected_revision=load_delivery_locks(self.root)["revision"])

    def test_locks_survive_roster_disable_resave_and_directory_move(self):
        self.lock()
        roster = json.loads((self.root / "grade_roster.json").read_text(encoding="utf-8"))
        set_review_enabled(self.root, False)
        save_roster(self.root, roster["student_ids"], confirmed_complete=True)
        moved = self.root.parent / "moved"
        shutil.copytree(self.root, moved)
        os.utime(moved / f"原始图片/{self.sid}.jpg", (1000, 1000))
        self.assertTrue(student_detail(moved, self.sid)["delivery_locked"])
        self.assertEqual(roster_overview(moved)["groups"]["delivered"]["count"], 1)

    def test_force_process_skips_missing_original_without_touching_records(self):
        self.lock()
        (self.root / f"原始图片/{self.sid}.jpg").unlink()
        before = json.loads((self.root / "export_state.json").read_text(encoding="utf-8"))["records"]
        with patch("xlsx_photo_core.run_pipeline", side_effect=AssertionError("must not invoke image processing")):
            result = self.process()
        self.assertEqual(result.summary["delivered_skipped"], 1)
        self.assertEqual(result.summary["reprocessed"], 0)
        self.assertEqual(result.summary["failed"], 0)
        self.assertFalse(result.results[0].executed)
        self.assertEqual(json.loads((self.root / "export_state.json").read_text(encoding="utf-8"))["records"], before)

    def test_actual_new_force_download_does_not_unlock_or_process_delivered_student(self):
        self.mark()
        self.lock()
        lock_bytes = (self.root / DELIVERY_LOCK_FILE).read_bytes()
        data = (self.root / f"原始图片/{self.sid}.jpg").read_bytes() + b"new image version"
        workbook = self.root / "test.xlsx"
        workbook.write_bytes(b"mocked workbook")
        selected = SelectedRow(2, self.sid, SourceRef(kind="file", fingerprint="new", display="test"))
        selection = InspectionReport(str(workbook), "sheet", 1, ["学号", "照片"], [selected], {})
        with patch("xlsx_photo_core.inspect_selection", return_value=selection), patch("xlsx_photo_core._fetch_source", return_value=data):
            run_export(ExportOptions(workbook, self.root, "sheet", 1, 0, 1, force_refresh=True))
        self.assertEqual((self.root / DELIVERY_LOCK_FILE).read_bytes(), lock_bytes)
        self.assertEqual(student_detail(self.root, self.sid)["status"], "delivered")
        with patch("xlsx_photo_core.run_pipeline", side_effect=AssertionError("must skip changed delivered image")):
            self.assertEqual(self.process().summary["delivered_skipped"], 1)

    def test_all_review_queues_skip_locks_and_api_search_remains_read_only(self):
        action = self.mark()
        stale = student_detail(self.root, self.sid)
        self.lock()
        for filter_name in ["all", "pending", "approved", "rejected", "stale"]:
            result = review_queue(self.root, filter_name)
            self.assertNotIn(self.sid, result["student_ids"])
            self.assertEqual(result["statuses"][self.sid], "delivered")
            self.assertEqual(result["counts"]["delivered"], 1)
            self.assertIsNone(result["last_action_id"])
        detail = student_detail(self.root, self.sid)
        self.assertFalse(detail["can_approve"])
        self.assertEqual(detail["saved_review_status"], "approved")
        for decision in ["approved", "rejected", "skipped"]:
            with self.assertRaises(ReviewError):
                mark_review(self.root, self.sid, decision, stale["version"], stale["revision"])
        with self.assertRaises(ReviewError):
            undo_review(self.root, action["action_id"])
        server = GalleryReportServer(lambda *_: (True, "test"))
        self.addCleanup(server.close)
        url = server.register(self.report)
        base, tail = url.split("/reports/")
        token = tail.split("/")[0]
        query = urllib.parse.urlencode(dict(action="student", student_id=self.sid, report_token=token))
        fetched = json.loads(urllib.request.urlopen(base + "/api/review?" + query).read())
        self.assertTrue(fetched["delivery_locked"])
        self.assertIn('!current.delivery_locked', urllib.request.urlopen(base + f"/reports/{token}/review.html").read().decode())

    def test_overview_and_export_partition_grade_without_mislabeling_approval(self):
        self.mark(self.other)
        self.lock([self.sid, "2600000004"])
        data = roster_overview(self.root)
        for key, expected in {"all": 4, "delivered": 2, "outstanding": 2, "approved": 1, "remaining": 1}.items():
            self.assertEqual(data["groups"][key]["count"], expected, key)
        locked = next(row for row in data["rows"] if row["student_id"] == self.sid)
        self.assertEqual(locked["groups"], ["all", "delivered"])
        result = export_delivery(self.root, self.destination, lists_only=True)
        self.assertEqual(result["delivered_locked"], 2)
        self.assertEqual(result["outstanding"], 2)
        folder = Path(result["output_dir"])
        self.assertEqual((folder / "已交付锁定学号.txt").read_text(encoding="utf-8-sig").splitlines(), [self.sid, "2600000004"])
        self.assertEqual((folder / "未审核通过学号.txt").read_text(encoding="utf-8-sig").splitlines(), ["2600000003"])
        self.assertEqual(json.loads((folder / "综合名单.json").read_text(encoding="utf-8"))["delivered_locked"], [self.sid, "2600000004"])

    def test_initial_and_incremental_skip_locked_even_when_current_approval_is_valid(self):
        self.mark()
        self.mark(self.other)
        self.lock()
        for mode in ["initial", "incremental"]:
            destination = self.destination / mode
            result = export_delivery(self.root, destination, mode=mode)
            self.assertEqual(result["exported"], 1)
            self.assertEqual(result["delivered_locked"], 1)
            photos = Path(result["photos_dir"])
            self.assertEqual([p.stem for p in photos.glob("*.jpg")], [self.other])
            cumulative = Path(result["output_dir"]) / result["cumulative_list"]
            self.assertEqual(cumulative.read_text(encoding="utf-8-sig").splitlines(), [self.sid, self.other])
            self.assertEqual(read_delivered_file(cumulative.parent / "累计交付学号.json"), [self.sid, self.other])
        result = export_delivery(self.root, self.destination / "incremental", mode="incremental")
        self.assertEqual(result["exported"], 0)

    def test_newly_exported_ids_are_not_automatically_confirmed_sent(self):
        self.mark(self.other)
        self.lock()
        export_delivery(self.root, self.destination, mode="incremental")
        self.assertEqual(load_delivery_locks(self.root)["student_ids"], [self.sid])

    def test_other_students_still_process_normally_after_import(self):
        self.lock()
        result = self.process([self.sid, self.other])
        self.assertEqual(result.summary["delivered_skipped"], 1)
        self.assertEqual(result.summary["reprocessed"], 1)
        self.assertEqual(result.summary["failed"], 0)
        self.assertEqual(student_detail(self.root, self.other)["processing_status"], "success")

    def test_desktop_import_export_json_and_unlock_callbacks(self):
        import tkinter as tk
        from tkinter import ttk
        tcl = Path(sys.base_prefix) / "tcl"
        if (tcl / "tcl8.6").is_dir() and (tcl / "tk8.6").is_dir():
            environment = patch.dict(os.environ, {"TCL_LIBRARY": str(tcl / "tcl8.6"), "TK_LIBRARY": str(tcl / "tk8.6")})
            environment.start()
            self.addCleanup(environment.stop)
        try:
            tk_root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display/runtime unavailable: {exc}")
        tk_root.withdraw()
        self.addCleanup(tk_root.destroy)
        app = PhotoExporterApp.__new__(PhotoExporterApp)
        app.root, app.tk, app.ttk, app.busy = tk_root, tk, ttk, False
        app.output_var = tk.StringVar(master=tk_root, value=str(self.root))
        app.messagebox, app.filedialog, app._append_log = MagicMock(), MagicMock(), MagicMock()
        path, exported = self.root.parent / "input.json", self.root.parent / "locks-copy.json"
        atomic_json(path, [self.sid])
        app.filedialog.askopenfilename.return_value = str(path)
        app.filedialog.asksaveasfilename.return_value = str(exported)
        app.messagebox.askyesno.return_value = True
        app.manage_delivery_locks()
        window = next(child for child in tk_root.winfo_children() if isinstance(child, tk.Toplevel))
        window.withdraw()
        widgets = []
        def walk(parent):
            for child in parent.winfo_children():
                widgets.append(child)
                walk(child)
        walk(window)
        buttons = {w.cget("text"): w for w in widgets if isinstance(w, ttk.Button)}
        buttons["导入名单…"].invoke()
        buttons["确认导入并锁定"].invoke()
        self.assertFalse((self.root / DELIVERY_LOCK_FILE).exists())
        self.assertTrue(app.messagebox.showerror.called)  # Confirmation is mandatory.
        next(w for w in widgets if isinstance(w, ttk.Checkbutton)).invoke()
        buttons["确认导入并锁定"].invoke()
        self.assertEqual(load_delivery_locks(self.root)["count"], 1)
        buttons["导出锁定 JSON…"].invoke()
        self.assertEqual(read_delivered_file(exported), [self.sid])
        with patch("tkinter.simpledialog.askstring", return_value="test correction"):
            buttons["解除输入区学号的锁定…"].invoke()
        self.assertEqual(load_delivery_locks(self.root)["count"], 0)

    def test_conflicting_history_is_not_ignored_just_because_locks_exist(self):
        self.lock()
        for index, sid in enumerate([self.sid, self.other]):
            directory = self.destination / f"新增({index + 1})"
            directory.mkdir(parents=True)
            (directory / "审核通过学号(9).txt").write_text(sid, encoding="utf-8")
        with self.assertRaises(DeliveryError):
            export_delivery(self.root, self.destination, mode="incremental")

    def test_corrupt_lock_state_fails_closed_across_consumers(self):
        self.lock()
        state = load_delivery_locks(self.root)
        state["student_ids"].append(self.other)  # Digest/count mismatch must never become an empty list.
        atomic_json(self.root / DELIVERY_LOCK_FILE, state)
        for function in [lambda: self.process(), lambda: review_queue(self.root), lambda: student_detail(self.root, self.sid),
                         lambda: roster_overview(self.root), lambda: export_delivery(self.root, self.destination), lambda: self.lock()]:
            with self.assertRaises((ReviewError, DeliveryError, DeliveryStateError)):
                function()

    def test_grade_676_baseline_483_is_not_approval_recovery(self):
        ids = [f"27{index:08d}" for index in range(676)]
        save_roster(self.root, ids, confirmed_complete=True)
        self.lock(ids[:483])
        groups = roster_overview(self.root)["groups"]
        self.assertEqual(groups["delivered"]["count"], 483)
        self.assertEqual(groups["outstanding"]["count"], 193)
        self.assertEqual(groups["approved"]["count"], 0)
        self.assertEqual(review_queue(self.root)["counts"]["delivered"], 483)

    def test_cli_reuses_import_validation_and_explicit_unlock_confirmation(self):
        path = self.root.parent / "ids.json"
        atomic_json(path, [self.sid])
        args = build_parser().parse_args(["--import-delivered", str(path), "--output", str(self.root)])
        with self.assertRaises(ReviewError):
            _cli_main(args)
        args.confirm_delivered = True
        self.assertEqual(_cli_main(args), 0)
        args = build_parser().parse_args(["--unlock-delivered", str(path), "--output", str(self.root), "--unlock-reason", "test"])
        with self.assertRaises(SystemExit):
            _cli_main(args)
        args.confirm_unlock = True
        self.assertEqual(_cli_main(args), 0)

    def test_stdlib_delivery_bundle_reuses_shared_state_module(self):
        self.lock()
        self.mark(self.other)
        bundled = self.root.parent / "standalone"
        bundled.mkdir()
        repo = Path(__file__).resolve().parents[1]
        for name in ["export_reviewed_photos.py", "delivery_state.py"]:
            shutil.copy2(repo / name, bundled / name)
        result = subprocess.run([sys.executable, "-I", "-S", str(bundled / "export_reviewed_photos.py"),
                                 str(self.root), "--mode", "incremental", "--output", str(self.destination), "--no-gui"],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertFalse(list(self.destination.rglob(self.sid + ".jpg")))
        self.assertEqual(len(list(self.destination.rglob(self.other + ".jpg"))), 1)

    def test_interactive_incremental_uses_locks_without_prompting_for_history(self):
        self.lock()
        with patch("builtins.input", side_effect=[str(self.root)]) as prompt, patch("sys.stdin.isatty", return_value=False), patch("sys.stdout", new_callable=io.StringIO):
            result = delivery_main(["--mode", "incremental", "--output", str(self.destination), "--no-gui"])
        self.assertEqual(result, 0)
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual((self.destination / "新增(1)/审核通过学号(1).txt").read_text(encoding="utf-8-sig").splitlines(), [self.sid])


if __name__ == "__main__":
    unittest.main()
