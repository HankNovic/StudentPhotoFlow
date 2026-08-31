"""Disposable local browser QA: 676 synthetic IDs, 483 delivered, one reviewable result."""
import copy
import json
import shutil
import tempfile
import threading
from pathlib import Path

from test_photo_review import fixture
from photo_exporter import GalleryReportServer
from photo_review import atomic_json, import_delivery_locks, mark_review, save_roster, set_review_enabled, student_detail


def main():
    with tempfile.TemporaryDirectory(prefix="spf-lock-browser-") as directory:
        root = Path(directory)
        report = fixture(root)
        detail = student_detail(root, "2600000001")
        mark_review(root, "2600000001", "approved", detail["version"], detail["revision"])
        ids = [f"26{index:08d}" for index in range(1, 677)]
        save_roster(root, ids, confirmed_complete=True)
        set_review_enabled(root, True)
        state = json.loads((root / "export_state.json").read_text(encoding="utf-8"))
        record = copy.deepcopy(state["records"]["2600000003"])
        record["student_id"] = "2600000500"
        for folder in ["原始图片", "处理后图片"]:
            shutil.copy2(root / folder / "2600000003.jpg", root / folder / "2600000500.jpg")
        record["original_file"] = "原始图片/2600000500.jpg"
        record["processing"]["processed_file"] = "处理后图片/2600000500.jpg"
        state["records"]["2600000500"] = record
        atomic_json(root / "export_state.json", state)
        import_delivery_locks(root, ids[:483], confirmed_sent=True, source="synthetic-demo.json")
        server = GalleryReportServer(lambda *_: (False, "测试数据，不运行图片处理"))
        try:
            print(server.register(report), flush=True)
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            server.close()


if __name__ == "__main__":
    main()
