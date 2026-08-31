"""Exercise the built EXE on disposable synthetic data, never real student output."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_photo_review import fixture
from photo_review import mark_review, student_detail, atomic_json, load_delivery_locks


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="spf-v110-portable-smoke-") as directory:
        root = Path(directory) / "source"
        fixture(root)
        detail = student_detail(root, "2600000001")
        mark_review(root, "2600000001", "approved", detail["version"], detail["revision"])
        destination = Path(directory) / "delivery"
        for mode in ["initial", "lists", "incremental"]:
            result = subprocess.run([str(exe), "--review-export", mode, "--output", str(root),
                                     "--delivery-output", str(destination)], timeout=45, capture_output=True)
            assert result.returncode == 0, (mode, result.returncode, result.stderr)
        initial = next(destination.glob("初次导入_*/导出摘要.json"))
        assert json.loads(initial.read_text(encoding="utf-8"))["exported"] == 1
        assert (destination / "新增(1)/审核通过学号(1).txt").read_text(encoding="utf-8-sig").splitlines() == ["2600000001"]
        lists = next(destination.glob("名单查询_*/已处理但未审核通过学号.txt"))
        assert lists.read_text(encoding="utf-8-sig").splitlines() == ["2600000002", "2600000003"]
        state = json.loads((root / "export_state.json").read_text(encoding="utf-8"))
        state["records"] = {"2600000001": state["records"]["2600000001"]}
        atomic_json(root / "export_state.json", state)
        result = subprocess.run([str(exe), "--cli", "--operation", "process", "--output", str(root),
                                 "--quality-check", "--force-process"], timeout=45, capture_output=True)
        assert result.returncode == 0, (result.returncode, result.stderr)
        state = json.loads((root / "export_state.json").read_text(encoding="utf-8"))
        assert state["app_version"] == "1.10.0"
        assert state["batches"][-1]["summary"]["archived_skipped"] == 1
        assert student_detail(root, "2600000001")["status"] == "approved"
        ids = Path(directory) / "delivered.json"
        atomic_json(ids, ["2600000001"])
        result = subprocess.run([str(exe), "--import-delivered", str(ids), "--confirm-delivered", "--output", str(root)],
                                timeout=45, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert load_delivery_locks(root)["count"] == 1
        # Real executable must skip even when the current original is missing.
        (root / "原始图片/2600000001.jpg").unlink()
        result = subprocess.run([str(exe), "--cli", "--operation", "process", "--output", str(root),
                                 "--quality-check", "--force-process"], timeout=45, capture_output=True)
        assert result.returncode == 0, result.stderr
        state = json.loads((root / "export_state.json").read_text(encoding="utf-8"))
        assert state["batches"][-1]["summary"]["delivered_skipped"] == 1
        fresh = Path(directory) / "locked-delivery"
        result = subprocess.run([str(exe), "--review-export", "incremental", "--output", str(root),
                                 "--delivery-output", str(fresh)], timeout=45, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert not list(fresh.rglob("*.jpg"))
        assert (fresh / "新增(1)/审核通过学号(1).txt").read_text(encoding="utf-8-sig").splitlines() == ["2600000001"]
        result = subprocess.run([str(exe), "--unlock-delivered", str(ids), "--confirm-unlock", "--unlock-reason", "smoke test",
                                 "--output", str(root)], timeout=45, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert load_delivery_locks(root)["count"] == 0
        print("Portable EXE smoke passed: delivery modes; archived skip; JSON import/unlock; delivered skip with missing photo; incremental lock baseline; version 1.10.0.")


if __name__ == "__main__":
    main()
