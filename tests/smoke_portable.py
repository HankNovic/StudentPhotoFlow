"""Exercise the built EXE on disposable synthetic data, never real student output."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_photo_review import fixture
from photo_review import mark_review, student_detail, atomic_json


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="spf-v19-portable-smoke-") as directory:
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
        assert state["app_version"] == "1.9.0"
        assert state["batches"][-1]["summary"]["archived_skipped"] == 1
        assert student_detail(root, "2600000001")["status"] == "approved"
        print("Portable EXE smoke passed: initial / incremental / lists-only; approved force-processing skip; version 1.9.0.")


if __name__ == "__main__":
    main()
