#!/usr/bin/env python3
"""Standalone StudentPhotoFlow 1.8 review exporter; Python 3.10+, standard library only.

Usage: python export_reviewed_photos.py "D:\\StudentPhotoFlow\\导出结果" --output "D:\\交付"
No arguments: choose initial/incremental mode, source and destination folders.
Never edits review state or archives. Each run creates a new delivery directory.
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
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

STATE_FILES = ("grade_roster.json", "review_state.json", "export_state.json")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
HISTORY_NAME = re.compile(r"审核通过学号(?:[（(](\d+)[）)])?\.txt", re.IGNORECASE)
INCREMENT_NAME = re.compile(r"新增[（(](\d+)[）)]")


class DeliveryError(ValueError):
    """Invalid or inconsistent source data; do not silently export a partial roster."""


def resolve_source(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file() and candidate.name in STATE_FILES:
        candidate = candidate.parent
    options = [candidate, candidate / "导出结果"]
    if candidate.name == "审核归档":
        options.append(candidate.parent)
    for root in options:
        if all((root / name).is_file() for name in STATE_FILES):
            return root
    raise DeliveryError("请选择包含 grade_roster.json、review_state.json、export_state.json 的“导出结果”目录。\n"
                        "只复制“审核归档”文件夹不足以判断哪些审核已撤销，也无法得到全年级剩余学号。")


@contextmanager
def source_lock(root: Path):
    """Use the existing app lock without creating or changing any source files."""
    path = root / ".studentphotoflow.lock"
    if not path.exists():
        # A copied backup may omit the hidden lock. State hashes are checked at the end too.
        yield
        return
    try:
        stream = path.open("r+b")
    except OSError as exc:
        raise DeliveryError("无法锁定源目录。请关闭主程序，确认目录可读写，或复制完整输出目录后再导出。") from exc
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise DeliveryError("主程序正在处理或保存审核，请先结束 / 中断任务后再导出；暂停任务仍占用写锁。") from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def load_states(root: Path) -> tuple[dict, dict[str, str]]:
    states, hashes = {}, {}
    for name in STATE_FILES:
        try:
            data = (root / name).read_bytes()
            payload = json.loads(data.decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("应为 JSON 对象")
        except (OSError, ValueError) as exc:
            raise DeliveryError(f"{name} 无法读取：{exc}") from exc
        states[name] = payload
        hashes[name] = hashlib.sha256(data).hexdigest()
    return states, hashes


def validate_states(states: dict) -> tuple[list[str], dict, dict]:
    roster = states["grade_roster.json"]
    ids = roster.get("student_ids")
    if not isinstance(ids, list) or not ids:
        raise DeliveryError("全年级名单缺失或为空，不能计算剩余全部学号。")
    seen = set()
    for sid in ids:
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
            raise DeliveryError("全年级名单含空学号或无效学号，请在主程序重新校验保存。")
        if sid.casefold() in seen:
            raise DeliveryError(f"学号重复或在 Windows 下文件名冲突：{sid}")
        if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", sid):
            raise DeliveryError(f"学号是 Windows 保留文件名，无法安全导出：{sid}")
        seen.add(sid.casefold())
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    if (roster.get("schema_version") != 1 or roster.get("count") != len(ids)
            or roster.get("sha256") != digest or roster.get("confirmed_complete") is not True):
        raise DeliveryError("全年级名单的校验信息无效，请在主程序重新校验保存。")
    reviews = states["review_state.json"]
    records = states["export_state.json"]
    if reviews.get("schema_version") != 1 or not isinstance(reviews.get("reviews"), dict):
        raise DeliveryError("审核状态文件格式不受支持（需要 v1.8 的 schema_version=1）。")
    if records.get("schema_version") != 3 or not isinstance(records.get("records"), dict):
        raise DeliveryError("导出状态文件格式不受支持（需要 schema_version=3）。")
    for sid, item in reviews["reviews"].items():
        if (not isinstance(item, dict) or item.get("status") not in {"approved", "rejected", "skipped"}
                or not isinstance(item.get("snapshot"), dict)):
            raise DeliveryError(f"审核记录格式错误：{sid}")
    for sid, item in records["records"].items():
        if not isinstance(item, dict) or not isinstance(item.get("processing", {}), dict):
            raise DeliveryError(f"流程记录格式错误：{sid}")
    return ids, reviews["reviews"], records["records"]


def contained_file(root: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise DeliveryError("状态文件中的图片路径必须是输出目录内的相对路径。")
    candidate = (root / relative).resolve()
    if root not in candidate.parents:
        raise DeliveryError("状态文件中的图片路径越出源目录，已拒绝读取。")
    return candidate if candidate.is_file() else None


def file_info(root: Path, relative: str | None) -> dict:
    path = contained_file(root, relative)
    if path is None:
        return {}
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise DeliveryError("读取时图片正在变化，请关闭主程序后重试。")
    return {"file": path.relative_to(root).as_posix(), "size": before.st_size,
            "mtime_ns": before.st_mtime_ns, "sha256": digest}


def current_snapshot(root: Path, record: dict) -> dict:
    """Keep identical to photo_review.result_snapshot in v1.8, including check-only results."""
    process = record.get("processing", {})
    final = process.get("processed_file")
    if not final and process.get("status") == "success":
        final = record.get("original_file")
    return {"original": file_info(root, record.get("original_file")), "result": file_info(root, final),
            "processing_fingerprint": process.get("config_fingerprint"),
            "processed_at": process.get("last_processed_at"), "processing_status": process.get("status", "pending")}


def effective_status(root: Path, review: dict, record: dict) -> tuple[str, str]:
    if not review:
        if not record:
            return "pending", "尚未上传 / 导出照片"
        if record.get("processing", {}).get("status", "pending") == "pending":
            return "pending", "尚未完成图片处理或审核"
        return "pending", "尚未人工审核（可能包含处理失败或机器检查不通过）"
    if review["snapshot"] != current_snapshot(root, record):
        return "stale", "照片或流程版本已变化，原审核失效，需重新审核"
    status = review["status"]
    return status, {"approved": "当前有效审核通过", "rejected": "人工审核不通过", "skipped": "审核时跳过"}[status]


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    def safe_cell(value: str) -> str:
        value = str(value)
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows([[safe_cell(cell) for cell in row] for row in rows])


def history_number(path: Path) -> int:
    match = HISTORY_NAME.fullmatch(path.name)
    return int(match[1] or 0) if match else 0


def history_is_complete(path: Path) -> bool:
    summary_path = path.parent / "导出摘要.json"
    if not summary_path.is_file():
        return True  # Allows a user to carry just the TXT file to a new destination.
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        return isinstance(summary, dict) and summary.get("status") in {"completed", "completed_with_errors"}
    except (OSError, ValueError):
        return False


def find_previous_file(destination: Path) -> Path:
    candidates = []
    if destination.is_dir():
        folders = [destination] + [path for path in destination.iterdir() if path.is_dir() and (
            path.name.startswith(("审核交付_", "初次导入_")) or INCREMENT_NAME.fullmatch(path.name))]
        for folder in folders:
            candidates.extend(path for path in folder.glob("*.txt")
                              if HISTORY_NAME.fullmatch(path.name) and history_is_complete(path))
    if not candidates:
        raise DeliveryError("新增模式没有找到历史“审核通过学号.txt”或“审核通过学号(数字).txt”。\n"
                            "请选择上次使用的交付目录，或用 --previous 指定名单文件；第一次使用请选择“初次导入”。")
    latest_number = max(map(history_number, candidates))
    choices = [path for path in candidates if history_number(path) == latest_number]
    if latest_number and len({hashlib.sha256(path.read_bytes()).hexdigest() for path in choices}) > 1:
        raise DeliveryError(f"找到内容不同的同号名单 ({latest_number})，请用 --previous 明确选择历史文件。")
    return max(choices, key=lambda path: path.stat().st_mtime_ns)


def read_previous_file(path: Path) -> tuple[list[str], str]:
    if not history_is_complete(path):
        raise DeliveryError("该历史名单所在批次未完成或已失败，不能作为新增导出的跳过依据。")
    try:
        data = path.read_bytes()
        content = data.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise DeliveryError(f"无法读取历史名单：{path}\n{exc}") from exc
    ids, seen = [], set()
    for number, value in enumerate(content.splitlines(), 1):
        sid = value.strip()
        if not sid:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
            raise DeliveryError(f"历史名单第 {number} 行不是有效学号：{sid[:40]}")
        if sid.casefold() in seen:
            raise DeliveryError(f"历史名单含重复学号：{sid}；请核对文件。")
        seen.add(sid.casefold())
        ids.append(sid)
    return ids, hashlib.sha256(data).hexdigest()


def reserve_incremental_directory(destination: Path, previous: Path) -> tuple[Path, int]:
    largest = history_number(previous)
    for child in destination.iterdir():
        match = INCREMENT_NAME.fullmatch(child.name)
        if match:
            largest = max(largest, int(match[1]))
        largest = max(largest, history_number(child))
    number = largest + 1
    while True:
        folder = destination / f"新增({number})"
        try:
            folder.mkdir()  # Exclusive reservation also prevents clobbering a concurrent run.
            return folder, number
        except FileExistsError:
            number += 1


def export_delivery(source: str | Path, output_parent: str | Path | None = None,
                    progress: Callable[[str], None] | None = None, *, mode: str = "initial",
                    previous_file: str | Path | None = None) -> dict:
    if mode not in {"initial", "incremental"}:
        raise DeliveryError("模式只能是 initial（初次导入）或 incremental（新增）。")
    if previous_file is not None and mode != "incremental":
        raise DeliveryError("--previous 仅用于新增模式；初次导入不会跳过历史学号。")
    root = resolve_source(source)
    destination = Path(output_parent).expanduser().resolve() if output_parent else root.parent / "审核交付导出"
    archive_root = root / "审核归档"
    if destination == archive_root or archive_root in destination.parents:
        raise DeliveryError("交付目录不能放在原“审核归档”内，请另选一个位置。")
    with source_lock(root):
        states, hashes = load_states(root)
        ids, reviews, records = validate_states(states)
        previous, previous_ids, previous_hash = None, [], ""
        if mode == "incremental":
            previous = (Path(previous_file).expanduser().resolve() if previous_file is not None
                        else find_previous_file(destination))
            previous_ids, previous_hash = read_previous_file(previous)
            if progress:
                progress(f"新增模式：读取 {previous}，忽略历史名单中的 {len(previous_ids)} 个学号。")
        previous_set = set(previous_ids)
        destination.mkdir(parents=True, exist_ok=True)
        if mode == "incremental":
            delivery, number = reserve_incremental_directory(destination, previous)
            images = delivery
            list_name = f"审核通过学号({number}).txt"
        else:
            delivery = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("初次导入_%Y%m%d_%H%M%S_"), dir=destination))
            number, list_name = 0, "审核通过学号.txt"
            images = delivery / "审核通过照片"
            images.mkdir()
        summary = {"schema_version": 2, "mode": mode, "number": number,
                   "source": str(root), "output_dir": str(delivery), "photos_dir": str(images),
                   "history_file": str(previous) if previous else None, "history_sha256": previous_hash,
                   "history_count": len(previous_ids), "skipped_previous": 0,
                   "cumulative_list": list_name, "cumulative_exported": len(previous_ids),
                   "history_outside_roster": [sid for sid in previous_ids if sid not in set(ids)],
                   "created_at": datetime.now().astimezone().isoformat(timespec="seconds"), "status": "running",
                   "roster_total": len(ids), "approved": 0, "exported": 0, "not_approved": 0, "errors": 0,
                   "ignored_outside_roster": sorted((set(reviews) | set(records)) - set(ids)),
                   "source_state_sha256": hashes}
        write_json(delivery / "导出摘要.json", summary)
        approved_ids, remaining, details, manifest, failures = [], [], [], [], []
        try:
            for index, sid in enumerate(ids, 1):
                if progress and (index == 1 or index % 100 == 0 or index == len(ids)):
                    progress(f"检查 {index}/{len(ids)}")
                review, record = reviews.get(sid, {}), records.get(sid, {})
                status, reason = effective_status(root, review, record)
                if status != "approved":
                    remaining.append(sid)
                    details.append([sid, status, reason, record.get("processing", {}).get("status", "pending")])
                    continue
                approved_ids.append(sid)
                if sid in previous_set:
                    summary["skipped_previous"] += 1
                    continue
                try:
                    archived = contained_file(root, review.get("archive_file"))
                    if archived is None:
                        raise DeliveryError("审核状态为通过，但归档副本丢失；请恢复归档文件后重试。")
                    if archive_root not in archived.parents or archived.parent.name != sid:
                        raise DeliveryError("归档副本路径与该学号不匹配。")
                    if archived.suffix.lower() not in IMAGE_SUFFIXES:
                        raise DeliveryError("归档副本不是受支持的图片扩展名。")
                    data = archived.read_bytes()
                    expected = review["snapshot"].get("result", {}).get("sha256")
                    actual = hashlib.sha256(data).hexdigest()
                    if not expected or actual != expected:
                        raise DeliveryError("归档副本内容与审核时的图片指纹不符，未导出。")
                    filename = sid + archived.suffix.lower()
                    # Flat names, original bytes, no conversion and no overwrite.
                    temporary = images / (filename + ".part")
                    try:
                        with temporary.open("xb") as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                        temporary.rename(images / filename)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
                    manifest.append([sid, filename, review.get("reviewed_at", ""), actual])
                except (OSError, DeliveryError) as exc:
                    failures.append([sid, str(exc)])
            _, current_hashes = load_states(root)
            if current_hashes != hashes:
                raise DeliveryError("导出期间名单 / 审核 / 流程状态发生变化。本次结果不可交付，请关闭主程序后重试。")
            if previous and hashlib.sha256(previous.read_bytes()).hexdigest() != previous_hash:
                raise DeliveryError("导出期间历史学号文件发生变化。本次结果不可交付，请保持名单不变后重试。")
            exported_ids = [row[0] for row in manifest]
            cumulative_ids = previous_ids + [sid for sid in exported_ids if sid not in previous_set]
            (delivery / "未审核通过学号.txt").write_text("".join(sid + "\n" for sid in remaining), encoding="utf-8-sig")
            # The cumulative list tracks delivered photos, not failed attempts; failures can retry next run.
            (delivery / list_name).write_text("".join(sid + "\n" for sid in cumulative_ids), encoding="utf-8-sig")
            (delivery / "当前有效审核通过学号.txt").write_text("".join(sid + "\n" for sid in approved_ids), encoding="utf-8-sig")
            (delivery / "本次导出学号.txt").write_text("".join(sid + "\n" for sid in exported_ids), encoding="utf-8-sig")
            write_csv(delivery / "未审核通过明细.csv", ["学号", "审核状态", "原因", "处理状态"], details)
            write_csv(delivery / "已导出照片明细.csv", ["学号", "图片文件名", "审核时间", "SHA256"], manifest)
            write_csv(delivery / "导出异常.csv", ["学号", "异常说明"], failures)
            summary.update(status="completed_with_errors" if failures else "completed", approved=len(approved_ids),
                           exported=len(manifest), not_approved=len(remaining), errors=len(failures),
                           cumulative_exported=len(cumulative_ids))
            assert summary["approved"] + summary["not_approved"] == summary["roster_total"]
            write_json(delivery / "导出摘要.json", summary)
            return summary
        except Exception as exc:
            summary.update(status="failed", error=str(exc), approved=len(approved_ids), exported=len(manifest))
            write_json(delivery / "导出摘要.json", summary)
            raise DeliveryError(f"{exc}\n未完成目录（不可交付）：{delivery}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="独立导出 v1.8 已审核照片（按学号命名）与全年级未通过学号；无需第三方依赖。")
    parser.add_argument("source", nargs="?", help="导出结果 / 审核归档 / 便携版目录")
    parser.add_argument("--output", "-o", help="交付结果的父目录；每次自动创建新子目录，不覆盖上次结果")
    parser.add_argument("--mode", choices=["initial", "incremental"], help="initial=初次导入；incremental=新增；传源路径时默认初次导入")
    parser.add_argument("--previous", help="新增模式的历史学号 TXT；不指定时自动识别交付目录内最新的累计名单")
    parser.add_argument("--no-gui", action="store_true", help="不用文件夹选择窗口，缺少路径时改为控制台输入")
    args = parser.parse_args(argv)
    gui_root = None
    interactive = not args.source
    try:
        if interactive and not args.no_gui:
            try:
                import tkinter as tk
                from tkinter import filedialog, messagebox
                gui_root = tk.Tk()
                gui_root.withdraw()
            except Exception:
                print("无法打开目录选择窗口，改用控制台输入。")
            else:
                if args.mode is None:
                    args.mode = choose_mode(gui_root)
                    if args.mode is None:
                        return 0
                args.source = filedialog.askdirectory(parent=gui_root, title="选择源目录：导出结果（或审核归档 / 便携版目录）")
                if not args.source:
                    return 0
                if not args.output:
                    args.output = filedialog.askdirectory(parent=gui_root, title="选择交付文件夹：新增时选择上次的交付父目录")
                    if not args.output:
                        return 0
        if args.mode is None:
            if interactive:
                value = input("选择模式：1=初次导入，2=新增（默认 1）：").strip() or "1"
                if value not in {"1", "2"}:
                    raise DeliveryError("模式请输入 1 或 2。")
                args.mode = "initial" if value == "1" else "incremental"
            else:
                args.mode = "initial"
        if not args.source:
            args.source = input("请输入“导出结果”目录路径：").strip().strip('"')
            if not args.source:
                return 0
        if args.mode == "incremental" and not args.previous and interactive:
            root = resolve_source(args.source)
            destination = Path(args.output) if args.output else root.parent / "审核交付导出"
            try:
                args.previous = str(find_previous_file(destination))
            except DeliveryError as exc:
                if gui_root is not None:
                    messagebox.showinfo("请选择历史学号名单", str(exc), parent=gui_root)
                    args.previous = filedialog.askopenfilename(parent=gui_root, title="选择上次生成的审核通过学号 TXT",
                        filetypes=[("学号名单", "*.txt")])
                else:
                    print(str(exc))
                    args.previous = input("请输入历史审核通过学号 TXT 路径（空白取消）：").strip().strip('"')
                if not args.previous:
                    return 0
        result = export_delivery(args.source, args.output, progress=print, mode=args.mode, previous_file=args.previous)
        message = (f"模式：{'新增' if result['mode'] == 'incremental' else '初次导入'}\n"
                   f"全年级 {result['roster_total']} 人；当前有效通过 {result['approved']} 人；"
                   f"本次导出照片 {result['exported']} 张；忽略已导出 {result['skipped_previous']} 人。\n"
                   f"累计已导出 {result['cumulative_exported']} 人；其余未审核通过 {result['not_approved']} 人。\n"
                   f"导出异常 {result['errors']} 项；名单外记录 {len(result['ignored_outside_roster'])} 项已忽略。\n"
                   f"下次使用的累计名单：{result['cumulative_list']}\n"
                   f"结果目录：{result['output_dir']}")
        print(message)
        if gui_root is not None:
            (messagebox.showwarning if result["errors"] else messagebox.showinfo)("审核结果导出", message, parent=gui_root)
        elif interactive and sys.stdin.isatty():
            input("按回车键退出…")
        return 2 if result["errors"] else 0
    except (DeliveryError, OSError, ValueError) as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        if gui_root is not None:
            from tkinter import messagebox
            messagebox.showerror("导出失败", str(exc), parent=gui_root)
        elif interactive and sys.stdin.isatty():
            input("按回车键退出…")
        return 1
    finally:
        if gui_root is not None:
            gui_root.destroy()


def choose_mode(parent) -> str | None:
    """Two clearly named choices for double-click users; no external GUI dependencies."""
    import tkinter as tk
    from tkinter import ttk
    choice = {"value": None}
    window = tk.Toplevel(parent)
    window.title("审核照片导出 · 选择模式")
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="初次导入：导出全部当前有效通过的照片。\n"
              "新增：读取已有名单，只导出尚未交付的学生照片。", justify="left").pack(pady=(0, 18))
    buttons = ttk.Frame(frame)
    buttons.pack()

    def select(value):
        choice["value"] = value
        window.destroy()

    ttk.Button(buttons, text="初次导入", command=lambda: select("initial")).pack(side="left", padx=6)
    ttk.Button(buttons, text="新增", command=lambda: select("incremental")).pack(side="left", padx=6)
    ttk.Button(buttons, text="取消", command=lambda: select(None)).pack(side="left", padx=6)
    window.protocol("WM_DELETE_WINDOW", lambda: select(None))
    window.grab_set()
    parent.wait_window(window)
    return choice["value"]


if __name__ == "__main__":
    raise SystemExit(main())
