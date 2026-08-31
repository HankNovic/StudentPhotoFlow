from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from export_reviewed_photos import DeliveryError, current_snapshot, export_delivery, find_previous_file, resolve_source
from photo_review import atomic_json, load_reviews, mark_review, output_write_lock, student_detail, undo_review
from test_photo_review import fixture


class ReviewDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "portable" / "导出结果"
        fixture(self.root)
        self.destination = self.base / "delivery"
        atomic_json(self.root / "review_state.json", {"schema_version": 1, "reviews": {}, "actions": []})

    def tearDown(self):
        self.temp.cleanup()

    def mark(self, sid="2600000001", decision="approved"):
        detail = student_detail(self.root, sid)
        return mark_review(self.root, sid, decision, detail["version"], detail["revision"])

    def deliver(self):
        return export_delivery(self.root, self.destination)

    def text_ids(self, result, name="未审核通过学号.txt"):
        return (Path(result["output_dir"]) / name).read_text(encoding="utf-8-sig").splitlines()

    def test_flat_photo_names_and_entire_grade_complement(self):
        self.mark()
        result = self.deliver()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["approved"], 1)
        self.assertEqual(result["exported"], 1)
        self.assertEqual(result["not_approved"], 3)
        self.assertEqual(self.text_ids(result), ["2600000002", "2600000003", "2600000004"])
        images = Path(result["output_dir"]) / "审核通过照片"
        self.assertEqual([p.name for p in images.iterdir()], ["2600000001.jpg"])
        archived = self.root / load_reviews(self.root)["reviews"]["2600000001"]["archive_file"]
        self.assertEqual((images / "2600000001.jpg").read_bytes(), archived.read_bytes())

    def test_undo_and_rejected_history_copies_are_not_exported(self):
        action = self.mark()
        undo_review(self.root, action["action_id"])
        self.mark("2600000002")
        self.mark("2600000002", "rejected")
        self.mark("2600000003", "skipped")
        self.assertGreater(len(list((self.root / "审核归档").rglob("*.jpg"))), 0)
        result = self.deliver()
        self.assertEqual(result["exported"], 0)
        self.assertEqual(result["not_approved"], 4)

    def test_changed_input_is_stale_like_main_app(self):
        self.mark()
        original = self.root / "原始图片/2600000001.jpg"
        original.write_bytes(original.read_bytes() + b"new")
        self.assertEqual(student_detail(self.root, "2600000001")["status"], "stale")
        result = self.deliver()
        self.assertEqual(result["approved"], 0)
        self.assertIn("2600000001", self.text_ids(result))

    def test_missing_archive_is_separate_error_not_failed_review(self):
        self.mark()
        archive = self.root / load_reviews(self.root)["reviews"]["2600000001"]["archive_file"]
        archive.unlink()
        result = self.deliver()
        self.assertEqual(result["status"], "completed_with_errors")
        self.assertEqual((result["approved"], result["exported"], result["errors"]), (1, 0, 1))
        self.assertNotIn("2600000001", self.text_ids(result))

    def test_tampered_archive_is_not_delivered(self):
        self.mark()
        archive = self.root / load_reviews(self.root)["reviews"]["2600000001"]["archive_file"]
        archive.write_bytes(b"wrong image")
        result = self.deliver()
        self.assertEqual(result["errors"], 1)
        self.assertEqual(list((Path(result["output_dir"]) / "审核通过照片").iterdir()), [])

    def test_source_choices_and_output_archive_guard(self):
        for source in [self.root, self.root.parent, self.root / "审核归档", self.root / "grade_roster.json"]:
            self.assertEqual(resolve_source(source), self.root)
        with self.assertRaises(DeliveryError):
            export_delivery(self.root, self.root / "审核归档" / "new")

    def test_missing_or_corrupt_roster_never_guesses_remaining_students(self):
        (self.root / "grade_roster.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(DeliveryError):
            self.deliver()
        self.assertFalse(self.destination.exists())

    def test_busy_main_application_blocks_export(self):
        with output_write_lock(self.root), self.assertRaises(DeliveryError):
            self.deliver()
        self.assertFalse(self.destination.exists())

    def test_every_run_is_new_and_source_bytes_are_unchanged(self):
        self.mark()
        before = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        first, second = self.deliver(), self.deliver()
        self.assertNotEqual(first["output_dir"], second["output_dir"])
        after = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_no_approvals_still_outputs_all_ids(self):
        result = self.deliver()
        self.assertEqual(len(self.text_ids(result)), 4)
        self.assertEqual(result["status"], "completed")

    def test_exporter_snapshot_exactly_matches_v18(self):
        records = json.loads((self.root / "export_state.json").read_text(encoding="utf-8"))["records"]
        for sid, record in records.items():
            self.assertEqual(current_snapshot(self.root, record), student_detail(self.root, sid)["snapshot"])

    def test_leading_zeroes_and_non_jpeg_extension_are_preserved(self):
        from photo_review import save_roster
        sid = "000123"
        (self.root / "原始图片/000123.png").write_bytes(b"png-test-fixture")
        state_path = self.root / "export_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["records"][sid] = {"student_id": sid, "original_file": "原始图片/000123.png",
                                  "processing": {"status": "success", "last_processed_at": "test"}}
        atomic_json(state_path, state)
        save_roster(self.root, [sid, "000124"], confirmed_complete=True)
        detail = student_detail(self.root, sid)
        digest = detail["snapshot"]["result"]["sha256"]
        archive = self.root / "审核归档" / sid / (digest + ".png")
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"png-test-fixture")
        atomic_json(self.root / "review_state.json", {"schema_version": 1, "reviews": {sid: {
            "status": "approved", "snapshot": detail["snapshot"], "archive_file": archive.relative_to(self.root).as_posix(),
            "revision": "test"}}, "actions": []})
        result = self.deliver()
        self.assertEqual(self.text_ids(result), ["000124"])
        self.assertEqual(self.text_ids(result, "审核通过学号.txt"), [sid])
        self.assertTrue((Path(result["output_dir"]) / "审核通过照片/000123.png").is_file())

    def test_exported_version_comes_from_current_review_not_directory_scan(self):
        self.mark()
        extra = self.root / "审核归档/2600000001/unreviewed-newer.jpg"
        extra.write_bytes(b"not-approved")
        result = self.deliver()
        exported = Path(result["output_dir"]) / "审核通过照片/2600000001.jpg"
        self.assertNotEqual(exported.read_bytes(), extra.read_bytes())
        self.assertEqual(result["exported"], 1)

    def test_standard_library_only_subprocess(self):
        self.mark()
        script = Path(__file__).resolve().parents[1] / "export_reviewed_photos.py"
        # -I -S removes site-packages and project imports; this must still work standalone.
        result = subprocess.run([sys.executable, "-I", "-S", str(script), str(self.root), "-o", str(self.destination)],
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(len(list(self.destination.glob("*/审核通过照片/2600000001.jpg"))), 1)

    def test_state_change_mid_export_marks_result_failed(self):
        self.mark()

        def change_state(_message):
            path = self.root / "grade_roster.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state["enabled"] = False
            atomic_json(path, state)

        with self.assertRaises(DeliveryError):
            export_delivery(self.root, self.destination, progress=change_state)
        summary = next(self.destination.glob("*/导出摘要.json"))
        self.assertEqual(json.loads(summary.read_text(encoding="utf-8"))["status"], "failed")

    def test_outside_archive_path_is_rejected_without_reading_external_data(self):
        self.mark()
        state = load_reviews(self.root)
        state["reviews"]["2600000001"]["archive_file"] = "../../outside.jpg"
        atomic_json(self.root / "review_state.json", state)
        result = self.deliver()
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["exported"], 0)

    def test_initial_then_two_increments_make_numbered_folders_and_cumulative_lists(self):
        self.mark()
        initial = self.deliver()
        old_list = Path(initial["output_dir"]) / "审核通过学号.txt"
        old_bytes = old_list.read_bytes()
        self.mark("2600000002")
        first = export_delivery(self.root, self.destination, mode="incremental")
        first_dir = Path(first["output_dir"])
        self.assertEqual(first_dir.name, "新增(1)")
        self.assertEqual([p.name for p in first_dir.glob("*.jpg")], ["2600000002.jpg"])
        self.assertEqual(self.text_ids(first, "审核通过学号(1).txt"), ["2600000001", "2600000002"])
        self.assertEqual(first["skipped_previous"], 1)
        self.mark("2600000003")
        second = export_delivery(self.root, self.destination, mode="incremental")
        self.assertEqual(Path(second["output_dir"]).name, "新增(2)")
        self.assertEqual(self.text_ids(second, "本次导出学号.txt"), ["2600000003"])
        self.assertEqual(self.text_ids(second, "审核通过学号(2).txt"), ["2600000001", "2600000002", "2600000003"])
        self.assertEqual(self.text_ids(second), ["2600000004"])
        self.assertEqual(old_list.read_bytes(), old_bytes)

    def test_failed_copies_do_not_enter_history_and_retry_after_repair(self):
        self.mark()
        archive = self.root / load_reviews(self.root)["reviews"]["2600000001"]["archive_file"]
        data = archive.read_bytes()
        archive.write_bytes(b"wrong")
        initial = self.deliver()
        self.assertEqual(self.text_ids(initial, "审核通过学号.txt"), [])
        self.assertEqual(self.text_ids(initial, "当前有效审核通过学号.txt"), ["2600000001"])
        archive.write_bytes(data)
        result = export_delivery(self.root, self.destination, mode="incremental")
        self.assertEqual(result["exported"], 1)
        self.assertEqual(self.text_ids(result, "审核通过学号(1).txt"), ["2600000001"])

    def test_incremental_without_new_ids_exports_no_duplicate_photos(self):
        self.mark()
        self.deliver()
        result = export_delivery(self.root, self.destination, mode="incremental")
        self.assertEqual(result["exported"], 0)
        self.assertEqual(result["cumulative_exported"], 1)
        self.assertEqual(list(Path(result["output_dir"]).glob("*.jpg")), [])
        self.assertEqual(self.text_ids(result, "审核通过学号(1).txt"), ["2600000001"])

    def test_incremental_accepts_old_format_history_at_explicit_path(self):
        self.mark()
        self.mark("2600000002")
        previous = self.base / "审核通过学号.txt"
        previous.write_text("2600000001\n", encoding="utf-8-sig")
        result = export_delivery(self.root, self.destination, mode="incremental", previous_file=previous)
        self.assertEqual(self.text_ids(result, "本次导出学号.txt"), ["2600000002"])
        self.assertEqual(self.text_ids(result, "审核通过学号(1).txt"), ["2600000001", "2600000002"])

    def test_auto_history_accepts_previous_timestamp_delivery_layout(self):
        self.destination.mkdir()
        old = self.destination / "审核交付_20260831_old"
        old.mkdir()
        previous = old / "审核通过学号.txt"
        previous.write_text("2600000001\n", encoding="utf-8-sig")
        atomic_json(old / "导出摘要.json", {"schema_version": 1, "status": "completed"})
        self.assertEqual(find_previous_file(self.destination), previous)
        self.mark()
        result = export_delivery(self.root, self.destination, mode="incremental")
        self.assertEqual(result["exported"], 0)

    def test_history_uses_numeric_sequence_not_text_sort_or_mtime(self):
        self.destination.mkdir()
        tenth = self.destination / "审核通过学号(10).txt"
        tenth.write_text("2600000001\n", encoding="utf-8-sig")
        ninth = self.destination / "审核通过学号(9).txt"
        ninth.write_text("", encoding="utf-8-sig")
        self.assertEqual(find_previous_file(self.destination), tenth)
        result = export_delivery(self.root, self.destination, mode="incremental")
        self.assertEqual(Path(result["output_dir"]).name, "新增(11)")

    def test_missing_invalid_duplicate_and_failed_history_are_rejected(self):
        with self.assertRaises(DeliveryError):
            export_delivery(self.root, self.destination, mode="incremental")
        self.assertFalse(self.destination.exists())
        previous = self.base / "审核通过学号.txt"
        for content in ["bad/id\n", "2600000001\n2600000001\n"]:
            previous.write_text(content, encoding="utf-8-sig")
            with self.assertRaises(DeliveryError):
                export_delivery(self.root, self.destination, mode="incremental", previous_file=previous)
        previous.write_text("2600000001\n", encoding="utf-8-sig")
        atomic_json(self.base / "导出摘要.json", {"status": "failed"})
        with self.assertRaises(DeliveryError):
            export_delivery(self.root, self.destination, mode="incremental", previous_file=previous)

    def test_failed_batch_is_not_selected_as_history_but_reserves_its_number(self):
        self.mark()
        self.deliver()
        failed = self.destination / "新增(1)"
        failed.mkdir()
        (failed / "审核通过学号(1).txt").write_text("2600000002\n", encoding="utf-8-sig")
        atomic_json(failed / "导出摘要.json", {"status": "running"})
        result = export_delivery(self.root, self.destination, mode="incremental")
        self.assertEqual(Path(result["output_dir"]).name, "新增(2)")
        self.assertEqual(self.text_ids(result, "审核通过学号(2).txt"), ["2600000001"])

    def test_same_number_with_conflicting_history_requires_explicit_choice(self):
        self.destination.mkdir()
        (self.destination / "审核通过学号(1).txt").write_text("2600000001\n", encoding="utf-8-sig")
        other = self.destination / "新增(1)"
        other.mkdir()
        (other / "审核通过学号(1).txt").write_text("2600000002\n", encoding="utf-8-sig")
        with self.assertRaises(DeliveryError):
            find_previous_file(self.destination)

    def test_changed_history_during_run_does_not_create_trusted_cumulative_list(self):
        self.mark()
        previous = self.base / "审核通过学号.txt"
        previous.write_text("", encoding="utf-8-sig")

        def change_history(message):
            if message.startswith("检查"):
                previous.write_text("2600000002\n", encoding="utf-8-sig")

        with self.assertRaises(DeliveryError):
            export_delivery(self.root, self.destination, progress=change_history, mode="incremental", previous_file=previous)
        folder = self.destination / "新增(1)"
        self.assertEqual(json.loads((folder / "导出摘要.json").read_text(encoding="utf-8"))["status"], "failed")
        self.assertFalse((folder / "审核通过学号(1).txt").exists())

    def test_incremental_cli_is_standalone(self):
        self.mark()
        self.deliver()
        self.mark("2600000002")
        script = Path(__file__).resolve().parents[1] / "export_reviewed_photos.py"
        result = subprocess.run([sys.executable, "-I", "-S", str(script), str(self.root), "-o", str(self.destination),
                                 "--mode", "incremental"], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue((self.destination / "新增(1)/2600000002.jpg").is_file())

    def test_initial_mode_reexports_all_and_does_not_accept_history_parameter(self):
        self.mark()
        first = self.deliver()
        self.assertEqual(self.deliver()["exported"], 1)
        with self.assertRaises(DeliveryError):
            export_delivery(self.root, self.destination, previous_file=Path(first["output_dir"]) / "审核通过学号.txt")


if __name__ == "__main__":
    unittest.main()
