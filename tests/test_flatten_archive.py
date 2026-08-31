from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "归档照片转学号.py"
SPEC = importlib.util.spec_from_file_location("flatten_archive", SCRIPT)
flatten = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flatten)


class FlattenArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "导出结果"
        self.archive = self.root / "审核归档"
        self.archive.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def photo(self, sid="000123", data=b"original-image-bytes", suffix=".jpg"):
        digest = hashlib.sha256(data).hexdigest()
        path = self.archive / sid / (digest + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def review(self, photo, status="approved"):
        return dict(status=status, archive_file=photo.relative_to(self.root).as_posix(),
                    snapshot={"result": {"sha256": hashlib.sha256(photo.read_bytes()).hexdigest()}})

    def write_reviews(self, reviews):
        (self.root / "review_state.json").write_text(json.dumps({"schema_version": 1, "reviews": reviews}), encoding="utf-8")

    def test_standalone_file_copies_exact_bytes_and_keeps_leading_zeroes(self):
        source = self.photo(suffix=".PNG")
        before = source.read_bytes()
        result = flatten.convert(self.archive)
        self.assertEqual((result["output"] / "000123.png").read_bytes(), before)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.root / "review_state.json").exists())
        self.assertEqual(result["counts"], {"已复制": 1})
        self.assertTrue(result["report"].is_file())

    def test_selects_reviewed_version_not_newest_or_arbitrary_file(self):
        chosen = self.photo(data=b"approved")
        other = self.photo(data=b"unreviewed-later")
        self.write_reviews({"000123": self.review(chosen)})
        before = (self.root / "review_state.json").read_bytes()
        result = flatten.convert(self.root)
        self.assertEqual((result["output"] / "000123.jpg").read_bytes(), b"approved")
        self.assertTrue(other.is_file())
        self.assertEqual((self.root / "review_state.json").read_bytes(), before)

    def test_multiple_versions_without_state_are_reported_not_chosen(self):
        self.photo(data=b"one")
        self.photo(data=b"two")
        result = flatten.convert(self.archive)
        self.assertEqual(result["counts"], {"待确认": 1})
        self.assertEqual(list(result["output"].glob("*.jpg")), [])

    def test_revoked_and_rejected_are_not_copied(self):
        first = self.photo("001")
        self.photo("002")
        self.write_reviews({"001": self.review(first, "rejected")})
        result = flatten.convert(self.archive)
        self.assertEqual(result["counts"], {"跳过": 2})

    def test_corrupt_state_does_not_fall_back_or_create_output(self):
        self.photo()
        (self.root / "review_state.json").write_text("{broken", encoding="utf-8")
        output = self.root / "new"
        with self.assertRaises(ValueError):
            flatten.convert(self.archive, output)
        self.assertFalse(output.exists())

    def test_missing_approved_image_reported_even_if_folder_missing(self):
        source = self.photo()
        review = self.review(source)
        source.unlink()
        source.parent.rmdir()
        self.write_reviews({"000123": review})
        result = flatten.convert(self.archive)
        self.assertEqual(result["counts"], {"待确认": 1})

    def test_hash_mismatch_does_not_copy(self):
        source = self.photo()
        self.write_reviews({"000123": self.review(source)})
        source.write_bytes(b"changed")
        result = flatten.convert(self.archive)
        self.assertEqual(result["counts"], {"失败": 1})
        self.assertEqual(list(result["output"].glob("*.jpg")), [])

    def test_dry_run_never_writes(self):
        self.photo()
        output = self.root / "preview"
        result = flatten.convert(self.archive, output, dry_run=True)
        self.assertEqual(result["counts"], {"待复制": 1})
        self.assertFalse(output.exists())

    def test_nonempty_destination_and_source_overlap_are_rejected(self):
        self.photo()
        output = self.root / "existing"
        output.mkdir()
        preserved = output / "000123.jpg"
        preserved.write_bytes(b"existing")
        for target in (output, self.archive, self.archive / "nested", self.root):
            with self.subTest(target=target), self.assertRaises(ValueError):
                flatten.convert(self.archive, target)
        self.assertEqual(preserved.read_bytes(), b"existing")

    def test_copy_function_never_overwrites_existing_file(self):
        source = self.photo()
        target = self.root / "existing.jpg"
        target.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            flatten.copy_new_file(source, target)
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertEqual(list(self.root.glob(".copy_*")), [])

    def test_unsafe_review_path_never_read(self):
        source = self.photo()
        review = self.review(source)
        review["archive_file"] = "../outside.jpg"
        self.write_reviews({"000123": review})
        result = flatten.convert(self.archive)
        self.assertEqual(result["counts"], {"待确认": 1})

    def test_script_has_no_project_or_third_party_imports(self):
        import ast
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imports = [node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
        imports += [name.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names]
        self.assertTrue(set(imports).issubset(sys.stdlib_module_names))

    def test_real_cli_handles_chinese_paths_and_reports_success(self):
        import subprocess
        self.photo()
        output = self.root / "命令行 交付结果"
        completed = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), str(self.archive),
                                    "--output", str(output)], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("已复制：1 张", completed.stdout)
        self.assertEqual((output / "000123.jpg").read_bytes(), b"original-image-bytes")


if __name__ == "__main__":
    unittest.main()
