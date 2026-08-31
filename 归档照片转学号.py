#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将“审核归档/学号/内容指纹.jpg”复制为“新目录/学号.jpg”。

独立脚本：Python 3.10+，仅用标准库，不需要安装第三方依赖。
双击后选择“审核归档”或“导出结果”目录；也可在命令行传入目录。
请先结束审核并关闭主程序。只复制文件，不改动原归档或任何审核记录。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif"}
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def valid_student_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)) and value.upper() not in RESERVED_NAMES


def resolve_archive(directory: Path) -> Path:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"目录不存在：{directory}")
    for candidate in (directory / "审核归档", directory / "导出结果" / "审核归档"):
        if candidate.is_dir():
            return candidate.resolve()
    return directory


def load_review_records(archive: Path) -> dict | None:
    state_path = archive.parent / "review_state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("reviews"), dict):
            raise ValueError("审核记录格式或版本不受支持")
        if any(not isinstance(review, dict) for review in state["reviews"].values()):
            raise ValueError("审核记录应为对象")
        return state["reviews"]
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取 {state_path.name}，已停止整理，不会猜测版本：{exc}") from exc


def plan_copies(archive: Path) -> tuple[list[dict], bool]:
    reviews = load_review_records(archive)
    folders = {p.name: p for p in archive.iterdir() if p.is_dir()}
    # Include missing approved folders in the report instead of silently omitting them.
    ids = set(folders)
    if reviews is not None:
        ids.update(sid for sid, review in reviews.items() if review.get("status") == "approved")
    if not ids:
        raise ValueError("未找到学号子文件夹，请选择包含“学号文件夹”的审核归档目录。")
    collisions = Counter(sid.casefold() for sid in ids)
    rows = []
    for sid in sorted(ids):
        row = dict(student_id=sid, status="待复制", source="", target="", sha256="", note="")
        rows.append(row)
        try:
            if not valid_student_id(sid):
                raise ValueError("文件夹名不是安全的学号（只接受字母、数字、短横线、下划线）")
            if collisions[sid.casefold()] > 1:
                raise ValueError("学号只在字母大小写上有区别，Windows 下会重名")
            if reviews is not None:
                review = reviews.get(sid, {})
                if review.get("status") != "approved":
                    row.update(status="跳过", note="审核记录没有通过标记（可能已撤销、不通过或跳过），不复制历史副本")
                    continue
                relative = review.get("archive_file")
                if not isinstance(relative, str) or not relative:
                    raise ValueError("通过记录缺少 archive_file，无法确定版本")
                source = (archive.parent / relative).resolve()
                # State paths are data, never permission to read outside this archive.
                if source.parent != archive / sid:
                    raise ValueError("审核记录中的图片路径不在对应学号的归档文件夹内")
                snapshot = review.get("snapshot", {})
                result = snapshot.get("result", {}) if isinstance(snapshot, dict) else {}
                digest = result.get("sha256", "") if isinstance(result, dict) else ""
                if digest and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
                    raise ValueError("审核记录中的图片指纹格式无效")
                row["sha256"] = digest.lower()
                row["note"] = "使用 review_state.json 中标记为通过的归档版本；未重新执行照片审核"
            else:
                folder = folders[sid]
                if folder.resolve().parent != archive or folder.resolve().name != sid:
                    raise ValueError("学号文件夹指向归档目录之外，不读取链接目标")
                images = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
                if len(images) != 1:
                    raise ValueError(f"没有审核记录，文件夹内有 {len(images)} 张图片；仅单张时可自动整理，请人工确认版本")
                source = images[0].resolve()
                if source.parent != folder:
                    raise ValueError("图片指向学号文件夹之外，不读取链接目标")
                row["note"] = "未找到 review_state.json，使用该学号文件夹内唯一图片"
            if not source.is_file() or source.suffix.lower() not in IMAGE_EXTENSIONS:
                raise ValueError("归档图片不存在或扩展名不受支持")
            row.update(source=str(source), target=sid + source.suffix.lower())
        except (OSError, ValueError) as exc:
            row.update(status="待确认", note=str(exc))
    return rows, reviews is not None


def copy_new_file(source: Path, target: Path, expected_sha256: str = "") -> str:
    """Publish a complete copy; never overwrite any existing target file."""
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and expected_sha256 != digest:
        raise ValueError("归档图片内容与审核记录指纹不一致，已跳过")
    descriptor, name = tempfile.mkstemp(prefix=".copy_", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            os.rename(temporary, target)  # Windows rename fails if target already exists.
        else:
            os.link(temporary, target)  # Atomic no-overwrite publication on POSIX.
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def convert(directory: Path, output: Path | None = None, *, dry_run: bool = False) -> dict:
    archive = resolve_archive(directory)
    rows, used_reviews = plan_copies(archive)
    output = (output.expanduser().resolve() if output is not None else archive.parent /
              ("审核归档_学号命名_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")))
    if output == archive or archive in output.parents or output in archive.parents:
        raise ValueError("输出目录不能是原归档目录、其子目录或其上级目录，请使用旁边的新目录。")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("输出目录已存在且非空。为防止覆盖照片或混入旧结果，请指定新的空目录。")
    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
    for row in rows:
        if row["status"] != "待复制":
            continue
        try:
            source = Path(row["source"])
            if dry_run:
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                if row["sha256"] and row["sha256"] != digest:
                    raise ValueError("归档图片内容与审核记录指纹不一致，已跳过")
                row["sha256"] = digest
            else:
                row["sha256"] = copy_new_file(source, output / row["target"], row["sha256"])
                row["status"] = "已复制"
        except (OSError, ValueError) as exc:
            row.update(status="失败", note=str(exc))
    report = None
    if not dry_run:
        report = output / "整理记录.csv"
        with report.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["学号", "结果", "原归档文件", "整理后文件", "SHA256", "说明"])
            for row in rows:
                # Protect Excel users from formula interpretation in any text cell.
                values = [row[key] for key in ("student_id", "status", "source", "target", "sha256", "note")]
                writer.writerow(["'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in values])
    return dict(archive=archive, output=output, rows=rows, counts=dict(Counter(row["status"] for row in rows)),
                used_reviews=used_reviews, report=report, dry_run=dry_run)


def format_summary(result: dict) -> str:
    counts = result["counts"]
    return (f"{'预览完成（没有写入文件）' if result['dry_run'] else '整理完成'}\n"
            f"{'可复制' if result['dry_run'] else '已复制'}：{counts.get('待复制' if result['dry_run'] else '已复制', 0)} 张\n"
            f"跳过：{counts.get('跳过', 0)}；待确认：{counts.get('待确认', 0)}；失败：{counts.get('失败', 0)}\n"
            f"输出目录：{result['output']}\n"
            "图片名称为学号，保留原扩展名；原归档和审核记录未修改。")


def gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        directory = filedialog.askdirectory(title="选择“审核归档”目录（先关闭照片工具，避免审核状态变化）", mustexist=True)
        if not directory:
            return 0
        result = convert(Path(directory))
        text = format_summary(result) + "\n\n详情见输出目录中的“整理记录.csv”。"
        has_issues = any(result["counts"].get(key, 0) for key in ("待确认", "失败"))
        (messagebox.showwarning if has_issues else messagebox.showinfo)("归档照片转学号", text)
        if os.name == "nt":
            os.startfile(result["output"])
        return 2 if has_issues else 0
    except Exception as exc:
        messagebox.showerror("整理未完成", f"{exc}\n\n原归档未修改。")
        return 1
    finally:
        root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", nargs="?", type=Path, help="审核归档、导出结果或便携版目录；不填则弹出选择窗口")
    parser.add_argument("-o", "--output", type=Path, help="输出到指定的新目录或空目录；默认自动新建带时间的同级目录")
    parser.add_argument("--dry-run", action="store_true", help="仅检查并显示计划，不创建目录或复制图片")
    args = parser.parse_args(argv)
    if args.directory is None:
        if args.output is not None or args.dry_run:
            parser.error("使用 --output 或 --dry-run 时，请同时填写归档目录")
        try:
            return gui()
        except Exception as exc:
            print(f"无法打开选择窗口：{exc}\n可改用命令行：python 归档照片转学号.py \"审核归档目录\"", file=sys.stderr)
            if sys.stdin is not None and sys.stdin.isatty():
                input("按回车退出…")
            return 1
    try:
        result = convert(args.directory, args.output, dry_run=args.dry_run)
        print(format_summary(result))
        for row in result["rows"]:
            if args.dry_run or row["status"] in {"失败", "待确认"}:
                print(f"[{row['status']}] {row['student_id']} → {row['target']}：{row['note']}")
        return 2 if any(result["counts"].get(key, 0) for key in ("失败", "待确认")) else 0
    except (OSError, ValueError) as exc:
        print(f"整理未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
