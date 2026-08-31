from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from export_reviewed_photos import DeliveryError, export_delivery, find_previous_file
from photo_review import atomic_json, mark_review, student_detail
from test_photo_review import fixture


class ProcessingListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "portable" / "导出结果"
        fixture(self.root)
        self.destination = self.base / "delivery"
        atomic_json(self.root / "review_state.json", {"schema_version": 1, "reviews": {}, "actions": []})

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return json.loads((self.root / "export_state.json").read_text(encoding="utf-8"))

    def save(self, state):
        atomic_json(self.root / "export_state.json", state)

    def mark(self, sid="2600000001", status="approved"):
        detail = student_detail(self.root, sid)
        return mark_review(self.root, sid, status, detail["version"], detail["revision"])

    def query(self, **options):
        return export_delivery(self.root, self.destination, lists_only=True, **options)

    def ids(self, result, filename="已处理但未审核通过学号.txt"):
        return (Path(result["output_dir"]) / filename).read_text(encoding="utf-8-sig").splitlines()

    def details(self, result):
        with (Path(result["output_dir"]) / "已处理但未审核通过明细.csv").open(encoding="utf-8-sig", newline="") as stream:
            return {row["学号"]: row for row in csv.DictReader(stream)}

    def batch(self, bid, results, *, at="2026-08-31T11:00:00", operation="process"):
        path = self.root / "批次记录" / bid / "batch.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, {"batch_id": bid, "operation": operation, "created_at": at, "results": results})
        return path

    def pending(self, *ids):
        state = self.state()
        for sid in ids:
            state["records"][sid]["processing"] = {"status": "pending", "message": "等待第二步图片处理"}
        self.save(state)

    def test_query_only_does_not_copy_photos_create_history_or_modify_source(self):
        self.mark()
        before = {p.relative_to(self.root): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        result = self.query()
        folder = Path(result["output_dir"])
        self.assertTrue(folder.name.startswith("名单查询_"))
        self.assertEqual(self.ids(result), ["2600000002", "2600000003"])
        self.assertEqual(self.ids(result, "未发现处理记录学号.txt"), ["2600000004"])
        self.assertEqual(self.ids(result, "未审核通过学号.txt"), ["2600000002", "2600000003", "2600000004"])
        self.assertEqual(result["processed_categories"], {"已处理待审核": 2})
        self.assertEqual((result["approved"], result["exported"], result["not_approved"]), (1, 0, 3))
        self.assertIsNone(result["cumulative_exported"])
        self.assertIsNone(result["cumulative_list"])
        self.assertFalse((folder / "审核通过照片").exists())
        self.assertEqual(list(folder.rglob("*.jpg")), [])
        with self.assertRaises(DeliveryError):
            find_previous_file(self.destination)
        after = {p.relative_to(self.root): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_manual_categories_and_machine_results_are_separate(self):
        self.mark("2600000001", "rejected")
        self.mark("2600000002", "skipped")
        self.mark("2600000003")
        state = self.state()
        state["records"]["2600000003"]["processing"]["config_fingerprint"] = "new-config"
        self.save(state)
        result = self.query()
        rows = self.details(result)
        self.assertEqual([rows[sid]["分类"] for sid in sorted(rows)], ["人工审核不通过", "审核跳过", "旧审核失效"])
        self.assertTrue(all(row["最近处理分类"] == "处理成功" for row in rows.values()))
        for category, count in result["processed_categories"].items():
            self.assertEqual(len(self.ids(result, f"未通过分类名单/{category}.txt")), count)

    def test_warning_and_preflight_rejection_are_not_mislabeled_manual_rejection(self):
        state = self.state()
        state["records"]["2600000001"]["processing"].update(status="warning", message="无法连接 Hivision API: timed out")
        state["records"]["2600000002"]["processing"].update(status="rejected", quality_status="rejected",
            message="无人脸", quality_reasons=[{"message": "无人脸"}])
        self.save(state)
        rows = self.details(self.query())
        self.assertEqual(rows["2600000001"]["分类"], "处理警告或异常")
        self.assertIn("timed out", rows["2600000001"]["处理说明"])
        self.assertEqual(rows["2600000002"]["分类"], "机器检查不通过")
        self.assertEqual(rows["2600000002"]["人工审核状态"], "未人工审核")
        self.assertEqual(rows["2600000002"]["处理说明"], "无人脸")

    def test_failed_attempt_is_recovered_when_state_still_says_pending(self):
        self.pending("2600000001")
        self.batch("later-failed", [{"student_id": "2600000001", "status": "failed", "change": "reprocessed",
                                     "message": "原图不存在", "processing_status": "not_requested", "executed": True}])
        row = self.details(self.query())["2600000001"]
        self.assertEqual(row["分类"], "处理失败")
        self.assertEqual(row["处理说明"], "原图不存在")
        self.assertEqual(row["主状态处理状态"], "pending")
        self.assertEqual(row["处理批次"], "later-failed")

    def test_latest_failed_attempt_overrides_old_success_and_is_deduplicated(self):
        for hour in [11, 12]:
            self.batch(f"failed-{hour}", [{"student_id": "2600000001", "status": "failed", "message": f"失败-{hour}"}],
                       at=f"2026-08-31T{hour}:00:00")
        result = self.query()
        self.assertEqual(self.ids(result).count("2600000001"), 1)
        self.assertEqual(self.details(result)["2600000001"]["处理说明"], "失败-12")

    def test_newer_success_supersedes_old_failure_using_recorded_time_not_file_mtime(self):
        self.batch("old-failure-written-last", [{"student_id": "2600000001", "status": "failed", "message": "旧失败"}],
                   at="2026-08-31T09:00:00")
        self.assertEqual(self.details(self.query())["2600000001"]["分类"], "已处理待审核")
        self.batch("new-success", [{"student_id": "2600000001", "status": "success", "processing_status": "success",
                                    "processing_message": "批次补查的成功"}], at="2026-08-31T12:00:00")
        row = self.details(self.query())["2600000001"]
        self.assertEqual(row["分类"], "已处理待审核")
        self.assertEqual(row["处理说明"], "批次补查的成功")

    def test_export_failures_cached_rows_and_unstarted_jobs_are_not_processing(self):
        self.pending("2600000001", "2600000002", "2600000003")
        self.batch("export", [{"student_id": "2600000001", "status": "failed"}], operation="export")
        self.batch("not-executed", [
            {"student_id": "2600000001", "status": "success", "processing_status": "success", "executed": False},
            {"student_id": "2600000002", "status": "success", "processing_status": "success", "change": "unchanged"},
            {"student_id": "2600000003", "status": "cancelled"}])
        result = self.query()
        self.assertEqual(self.ids(result), [])
        self.assertEqual(result["no_processing_evidence"], 4)

    def test_checkpoint_completed_ids_only_not_planned_or_unidentified_failures(self):
        self.pending("2600000001", "2600000002", "2600000003")
        checkpoint = {"batch_id": "crashed", "operation": "process", "status": "running",
                      "checkpoint_at": "2026-08-31T12:00:00", "completed_ids": ["2600000001"],
                      "planned": {"2600000001": "reprocessed", "2600000002": "reprocessed"},
                      "failed_count": 1, "last_student_id": "2600000002"}
        state = self.state()
        state["active_run"] = checkpoint
        state["resume_runs"] = {"process": checkpoint}
        self.save(state)
        path = self.root / "批次记录/crashed/run_status.json"
        path.parent.mkdir()
        atomic_json(path, checkpoint)
        result = self.query()
        self.assertEqual(self.ids(result), ["2600000001"])
        self.assertEqual(self.details(result)["2600000001"]["分类"], "处理结果待核实")
        self.assertEqual(result["query_warnings"], 1)
        self.assertFalse(result["history_scan_complete"])
        self.assertEqual(result["status"], "completed_with_errors")

    def test_checkpoint_does_not_overwrite_richer_same_batch_record(self):
        state = self.state()
        state["active_run"] = {"batch_id": "test-old", "operation": "process", "status": "running",
            "checkpoint_at": "2026-08-31T12:00:00", "completed_ids": ["2600000001"]}
        self.save(state)
        row = self.details(self.query())["2600000001"]
        self.assertEqual(row["分类"], "已处理待审核")
        self.assertEqual(row["依据文件"], "export_state.json/records")

    def test_complete_cancelled_batch_supersedes_checkpoint_without_guessing_pending_ids(self):
        self.pending("2600000001", "2600000002", "2600000003")
        self.batch("cancelled", [{"student_id": "2600000001", "status": "success", "processing_status": "rejected"}])
        atomic_json(self.root / "批次记录/cancelled/run_status.json", {"batch_id": "cancelled", "operation": "process",
            "status": "cancelled", "failed_count": 0, "completed_ids": ["2600000001"], "planned": {"2600000002": "reprocessed"}})
        result = self.query()
        self.assertEqual(self.ids(result), ["2600000001"])
        self.assertEqual(result["query_warnings"], 0)

    def test_valid_approval_is_excluded_even_with_failed_history(self):
        self.mark()
        self.batch("failed-later", [{"student_id": "2600000001", "status": "failed"}])
        self.assertNotIn("2600000001", self.ids(self.query()))

    def test_replacement_input_is_labeled_historical_not_successfully_processed(self):
        self.batch("old-input", [{"student_id": "2600000001", "status": "success", "processing_status": "success",
                                  "original_sha256": self.state()["records"]["2600000001"]["original_sha256"]}])
        self.pending("2600000001")
        state = self.state()
        state["records"]["2600000001"].update(original_sha256="replacement", last_exported_at="2026-08-31T12:00:00")
        self.save(state)
        row = self.details(self.query())["2600000001"]
        self.assertEqual(row["分类"], "仅历史处理记录")
        self.assertIn("仅历史版本", row["版本说明"])

    def test_stale_review_snapshot_preserves_evidence_after_pending_reset(self):
        self.mark()
        self.pending("2600000001")
        row = self.details(self.query())["2600000001"]
        self.assertEqual(row["分类"], "旧审核失效")
        self.assertIn("仅历史版本", row["版本说明"])

    def test_batch_only_student_is_counted_but_outside_roster_is_not(self):
        self.batch("missing-record", [{"student_id": sid, "status": "failed", "message": "失败"}
                                      for sid in ["2600000004", "outside"]])
        result = self.query()
        self.assertIn("2600000004", self.ids(result))
        self.assertNotIn("outside", self.ids(result))
        self.assertIn("outside", result["ignored_outside_roster"])
        self.assertIn("缺少当前", self.details(result)["2600000004"]["版本说明"])

    def test_missing_and_corrupt_history_raise_visible_completeness_warnings(self):
        path = self.batch("broken", [])
        path.write_text("{not-json", encoding="utf-8")
        state = self.state()
        state["batches"] = [{"batch_id": "missing", "operation": "process"}]
        self.save(state)
        result = self.query()
        self.assertEqual(result["query_warnings"], 2)
        self.assertEqual(result["status"], "completed_with_errors")
        self.assertFalse(result["history_scan_complete"])

    def test_history_change_during_export_invalidates_result(self):
        path = self.batch("mutable", [])

        def mutate(message):
            if message.startswith("检查"):
                atomic_json(path, {"operation": "process", "results": [{"student_id": "2600000004", "status": "failed"}]})

        with self.assertRaisesRegex(DeliveryError, "历史批次记录发生变化"):
            self.query(progress=mutate)
        summary = next(self.destination.glob("*/导出摘要.json"))
        self.assertEqual(json.loads(summary.read_text(encoding="utf-8"))["status"], "failed")
        self.assertFalse((summary.parent / "已处理但未审核通过学号.txt").exists())

    def test_undated_conflicting_history_is_flagged_not_silently_sorted_by_mtime(self):
        self.batch("unknown-time", [{"student_id": "2600000001", "status": "failed"}], at="unknown")
        result = self.query()
        self.assertGreater(result["query_warnings"], 0)
        self.assertFalse(result["history_scan_complete"])

    def test_incremental_full_lists_include_previous_deliveries_that_are_now_rejected(self):
        self.mark()
        initial = export_delivery(self.root, self.destination)
        self.mark("2600000001", "rejected")
        result = export_delivery(self.root, self.destination, mode="incremental")
        self.assertIn("2600000001", self.ids(result))
        self.assertEqual(self.ids(result, "审核通过学号(1).txt"), ["2600000001"])
        self.query(mode="incremental")
        self.assertEqual(find_previous_file(self.destination), Path(result["output_dir"]) / "审核通过学号(1).txt")
        self.assertEqual(initial["exported"], 1)

    def test_query_does_not_require_history_or_change_next_increment(self):
        self.mark()
        initial = export_delivery(self.root, self.destination)
        self.query(mode="incremental")
        self.mark("2600000002")
        result = export_delivery(self.root, self.destination, mode="incremental")
        self.assertEqual(Path(result["output_dir"]).name, "新增(1)")
        self.assertEqual(self.ids(result, "本次导出学号.txt"), ["2600000002"])
        with self.assertRaises(DeliveryError):
            self.query(mode="incremental", previous_file=Path(initial["output_dir"]) / "审核通过学号.txt")

    def test_lists_only_cli_is_standalone_and_warning_exit_code_is_nonzero(self):
        script = Path(__file__).resolve().parents[1] / "export_reviewed_photos.py"
        command = [sys.executable, "-I", "-S", str(script), str(self.root), "-o", str(self.destination), "--lists-only"]
        result = subprocess.run(command, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(len(list(self.destination.glob("名单查询_*/已处理但未审核通过学号.txt"))), 1)
        self.assertFalse(list(self.destination.rglob("审核通过学号*.txt")))
        path = self.batch("bad-json", [])
        path.write_text("bad", encoding="utf-8")
        result = subprocess.run(command, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 2, result.stderr.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
