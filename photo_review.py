"""Roster-gated human review, version-bound archives, and per-output write locks."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

from delivery_state import (DELIVERY_LOCK_FILE, DeliveryStateError, ids_digest,
                            load_delivery_locks as _load_delivery_locks, read_delivery_ids,
                            validate_delivery_locks)

ROSTER_FILE = "grade_roster.json"
REVIEW_FILE = "review_state.json"


class ReviewError(ValueError):
    pass


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".review_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError("应为 JSON 对象")
        return result
    except (OSError, ValueError) as exc:
        raise ReviewError(f"{path.name} 无法读取；为保护归档已停止：{exc}") from exc


@contextmanager
def output_write_lock(root: Path):
    """OS releases the lock on process death; no stale-PID lock deletion needed."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stream = (root / ".studentphotoflow.lock").open("a+b")
    locked = False
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise ReviewError("此输出目录正在导出、处理或保存审核；请稍后重试，或先中断任务。") from exc
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


def locked_batch(function):
    @wraps(function)
    def wrapped(options, *args, **kwargs):
        with output_write_lock(Path(options.output_dir)):
            return function(options, *args, **kwargs)
    return wrapped


def validate_roster(values: list[str]) -> list[str]:
    if not isinstance(values, list):
        raise ReviewError("名单必须是学号列表")
    if not values:
        raise ReviewError("名单为空")
    ids, seen, errors = [], set(), []
    for line, raw in enumerate(values, 1):
        sid = raw.strip() if isinstance(raw, str) else ""
        if not sid:
            errors.append(f"第 {line} 项学号为空")
        elif not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
            errors.append(f"第 {line} 项学号格式无效：{sid[:40]}")
        elif sid in seen:
            errors.append(f"重复学号：{sid}")
        else:
            seen.add(sid)
            ids.append(sid)
    if errors:
        raise ReviewError("名单未保存，请修正：\n" + "\n".join(errors[:20]))
    return ids


def parse_roster_text(text: str) -> list[str]:
    rows = re.split(r"[\s,，;；]+", text.strip().lstrip("\ufeff"))
    if rows and rows[0].lower() in {"学号", "student_id", "studentid"}:
        rows = rows[1:]
    return validate_roster(rows)


def _ids_from_table(rows: list[list[str]], label: str) -> list[str]:
    rows = [row for row in rows if any(str(value).strip() for value in row)]
    if not rows:
        raise ReviewError(f"{label} 没有学号")
    header = [str(value).strip().lower() for value in rows[0]]
    matches = [i for i, value in enumerate(header) if value in {"学号", "学生学号", "student_id", "studentid"}]
    if len(matches) == 1:
        index, data = matches[0], rows[1:]
    elif max(map(len, rows)) == 1 and not matches:
        index, data = 0, rows
    else:
        raise ReviewError(f"{label} 需要唯一的“学号”表头，或仅包含一列学号")
    return [str(row[index]).strip() if index < len(row) else "" for row in data]


def read_roster_file(path: Path) -> list[str]:
    if path.suffix.lower() == ".xlsx":
        from xlsx_photo_core import WorkbookReader
        ids = []
        with WorkbookReader(path) as reader:
            for sheet in reader.sheets:
                values, _ = reader._sheet_values(sheet)
                if not values:
                    continue
                row_numbers = sorted({r for r, _ in values})
                width = max(c for _, c in values) + 1
                rows = [[values.get((r, c), "") for c in range(width)] for r in row_numbers]
                ids.extend(_ids_from_table(rows, sheet.name))
        return validate_roster(ids)
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    if path.suffix.lower() == ".csv":
        return validate_roster(_ids_from_table(list(csv.reader(io.StringIO(text))), path.name))
    return parse_roster_text(text)


def roster_digest(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def load_roster(root: Path) -> dict:
    value = read_json(root / ROSTER_FILE, {})
    if not value:
        return {}
    ids = validate_roster(value.get("student_ids", []))
    if (value.get("schema_version") != 1 or value.get("count") != len(ids)
            or value.get("sha256") != roster_digest(ids) or value.get("confirmed_complete") is not True):
        raise ReviewError("全年级名单校验信息无效，请重新校验保存；审核归档未启用")
    return value


def load_delivery_locks(root: Path) -> dict:
    try:
        return _load_delivery_locks(root)
    except DeliveryStateError as exc:
        raise ReviewError(str(exc)) from exc


def read_delivered_file(path: Path) -> list[str]:
    ids = read_delivery_ids(path) if path.suffix.lower() == ".json" else read_roster_file(path)
    return validate_roster(ids)


def _write_delivery_change(root: Path, state: dict, ids: list[str], action: dict) -> dict:
    """Caller holds the output lock. Back up previous JSON before atomic replacement."""
    target = root / DELIVERY_LOCK_FILE
    if target.exists():
        atomic_json(root / "交付锁定历史" / f"{action['id']}_before.json", state)
    updated = dict(schema_version=1, student_ids=ids, count=len(ids), sha256=ids_digest(ids),
                   revision=action["id"], updated_at=action["at"], actions=state["actions"] + [action])
    validate_delivery_locks(updated)
    atomic_json(target, updated)
    return updated


def import_delivery_locks(root: Path, ids: list[str], *, confirmed_sent: bool,
                          source: str = "手动输入") -> dict:
    ids = validate_roster(ids)
    if not confirmed_sent:
        raise ReviewError("必须确认这些学生的照片已经实际交付；仅处理通过或仅导出不等于已发送")
    if len({sid.casefold() for sid in ids}) != len(ids):
        raise ReviewError("学号大小写冲突，请核对名单")
    with output_write_lock(root):
        roster = load_roster(root)
        if not roster:
            raise ReviewError("请先校验保存全年级名单，再导入已交付名单；无需开启审核归档开关")
        outside = sorted(set(ids) - set(roster["student_ids"]))
        if outside:
            raise ReviewError("以下学号不在已保存的全年级名单内，未导入：" + "、".join(outside[:20]))
        state = load_delivery_locks(root)
        locked = set(state["student_ids"])
        added = [sid for sid in ids if sid not in locked]
        if added:
            action = dict(id=uuid.uuid4().hex, action="import", at=timestamp(), student_ids=added,
                          source=source, input_count=len(ids), input_sha256=ids_digest(ids), confirmed_sent=True)
            state = _write_delivery_change(root, state, state["student_ids"] + added, action)
        return dict(added=len(added), already_locked=len(ids) - len(added), total=state["count"], revision=state["revision"])


def unlock_delivery_ids(root: Path, ids: list[str], *, reason: str, expected_revision: str) -> dict:
    ids = validate_roster(ids)
    if not reason.strip():
        raise ReviewError("手动解除交付锁定必须填写原因")
    with output_write_lock(root):
        state = load_delivery_locks(root)
        if state["revision"] != expected_revision:
            raise ReviewError("交付锁定名单已经变化，请刷新后再解除")
        missing = set(ids) - set(state["student_ids"])
        if missing:
            raise ReviewError("以下学号当前未锁定，未执行解除：" + "、".join(sorted(missing)[:20]))
        action = dict(id=uuid.uuid4().hex, action="unlock", at=timestamp(), student_ids=ids, reason=reason.strip())
        state = _write_delivery_change(root, state, [sid for sid in state["student_ids"] if sid not in set(ids)], action)
        return dict(unlocked=len(ids), total=state["count"], revision=state["revision"])


def save_roster(root: Path, ids: list[str], *, confirmed_complete: bool, source: str = "手动输入") -> dict:
    ids = validate_roster(ids)
    if not confirmed_complete:
        raise ReviewError("请先确认这是全年级完整学号名单")
    value = dict(schema_version=1, student_ids=ids, count=len(ids), sha256=roster_digest(ids),
                 confirmed_complete=True, source=source, validated_at=timestamp(), enabled=False)
    with output_write_lock(root):
        atomic_json(root / ROSTER_FILE, value)
    return value


def set_review_enabled(root: Path, enabled: bool) -> dict:
    with output_write_lock(root):
        roster = load_roster(root)
        if not roster:
            raise ReviewError("请先校验并保存全年级学号名单")
        roster.update(enabled=bool(enabled), updated_at=timestamp())
        atomic_json(root / ROSTER_FILE, roster)
    return roster


def load_reviews(root: Path) -> dict:
    state = read_json(root / REVIEW_FILE, {"schema_version": 1, "reviews": {}, "actions": []})
    if state.get("schema_version") != 1 or not isinstance(state.get("reviews"), dict) or not isinstance(state.get("actions"), list):
        raise ReviewError("审核状态文件格式无效，请恢复备份后再操作")
    if any(not isinstance(item, dict) or item.get("status") not in {"approved", "rejected", "skipped"}
           or not isinstance(item.get("snapshot"), dict) or not item.get("revision")
           for item in state["reviews"].values()):
        raise ReviewError("审核记录不完整，请恢复备份后再操作")
    if any(not isinstance(item, dict) or not item.get("id") or not item.get("student_id")
           for item in state["actions"]):
        raise ReviewError("审核操作历史不完整，请恢复备份后再操作")
    return state


def archive_context(root: Path) -> tuple[dict, dict]:
    return load_roster(root), load_reviews(root)


def safe_file(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    candidate = (root / value).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _file_info(root: Path, relative: str | None, *, digest: bool) -> dict:
    path = safe_file(root, relative)
    if path is None:
        return {}
    stat = path.stat()
    info = {"file": path.relative_to(root.resolve()).as_posix(), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if digest:
        info["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return info


def result_snapshot(root: Path, record: dict, *, digest: bool = True) -> dict:
    process = record.get("processing", {})
    final_file = process.get("processed_file")
    if not final_file and process.get("status") == "success":
        final_file = record.get("original_file")  # successful check-only pipeline
    return {
        "original": _file_info(root, record.get("original_file"), digest=digest),
        "result": _file_info(root, final_file, digest=digest),
        "processing_fingerprint": process.get("config_fingerprint"),
        "processed_at": process.get("last_processed_at"),
        "processing_status": process.get("status", "pending"),
    }


def review_status(root: Path, sid: str, record: dict, reviews: dict, *, digest: bool = False) -> str:
    review = reviews.get("reviews", {}).get(sid, {})
    status = review.get("status", "pending")
    if status == "pending":
        return status
    old = review.get("snapshot", {})
    now = result_snapshot(root, record, digest=digest)
    if not digest:
        old = json.loads(json.dumps(old))
        for key in ("original", "result"):
            old.get(key, {}).pop("sha256", None)
    return status if old == now else "stale"


def is_archived(root: Path, sid: str, record: dict, context: tuple[dict, dict]) -> bool:
    roster, reviews = context
    return bool(roster.get("enabled") and sid in roster.get("student_ids", [])
                and review_status(root, sid, record, reviews, digest=True) == "approved")


def _export_state(root: Path) -> dict:
    value = read_json(root / "export_state.json", {"records": {}, "batches": []})
    if not isinstance(value.get("records"), dict):
        raise ReviewError("导出状态文件格式错误")
    return value


def student_detail(root: Path, sid: str) -> dict:
    delivered = sid in set(load_delivery_locks(root)["student_ids"])
    roster, reviews = archive_context(root)
    state = _export_state(root)
    record = state["records"].get(sid, {})
    process = record.get("processing", {})
    snapshot = result_snapshot(root, record)
    version = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return {
        "student_id": sid, "in_roster": sid in roster.get("student_ids", []),
        "exists": bool(record), "review_enabled": bool(roster.get("enabled")),
        "status": "delivered" if delivered else review_status(root, sid, record, reviews, digest=True),
        "delivery_locked": delivered,
        "saved_review_status": reviews.get("reviews", {}).get(sid, {}).get("status", "pending"),
        "revision": reviews.get("reviews", {}).get(sid, {}).get("revision", ""),
        "version": version, "snapshot": snapshot,
        "result_file": snapshot["result"].get("file"), "original_file": record.get("original_file"),
        "can_approve": not delivered and bool(snapshot["original"] and snapshot["result"]),
        "processing_status": process.get("status", "pending"),
        "message": ("历史已交付锁定：跳过处理、审核与照片交付；不代表当前新照片已通过。仅可在主程序手动解锁。"
                    if delivered else process.get("message", "尚未导出或处理")),
        "quality_status": process.get("quality_status", "not_requested"),
        "steps": process.get("step_files", []),
        "last_batch_id": record.get("last_batch_id"),
        "archive_file": reviews.get("reviews", {}).get(sid, {}).get("archive_file"),
    }


def review_queue(root: Path, filter_name: str = "pending") -> dict:
    if filter_name not in {"all", "pending", "approved", "rejected", "skipped", "stale"}:
        raise ReviewError("审核筛选条件无效")
    roster, reviews = archive_context(root)
    delivered = set(load_delivery_locks(root)["student_ids"])
    records = _export_state(root)["records"]
    counts = {"total": len(roster.get("student_ids", [])), "processed": 0, "approved": 0,
              "rejected": 0, "skipped": 0, "pending": 0, "stale": 0, "missing": 0, "delivered": 0}
    ids, statuses = [], {}
    for sid in roster.get("student_ids", []):
        if sid in delivered:
            statuses[sid] = "delivered"
            counts["delivered"] += 1
            continue  # Even "all" is a review queue, never include delivered students.
        record = records.get(sid, {})
        process = record.get("processing", {})
        attempted = process.get("status") in {"success", "warning", "rejected", "failed"} or bool(process.get("last_processed_at"))
        if not attempted:
            statuses[sid] = "pending"
            counts["missing"] += 1
            continue
        counts["processed"] += 1
        status = review_status(root, sid, record, reviews)
        statuses[sid] = status
        counts[status if status in counts else "pending"] += 1
        if filter_name == "all" or status == filter_name or (filter_name == "pending" and status == "stale"):
            ids.append(sid)
    undoable = [action for action in reviews["actions"] if not action.get("undone")]
    return {"enabled": bool(roster.get("enabled")), "roster_saved": bool(roster), "counts": counts,
            "student_ids": ids, "statuses": statuses,
            "last_action_id": undoable[-1]["id"] if undoable and undoable[-1]["student_id"] not in delivered else None}


def mark_review(root: Path, sid: str, decision: str, expected_version: str, expected_revision: str) -> dict:
    if decision not in {"approved", "rejected", "skipped"}:
        raise ReviewError("审核操作无效")
    with output_write_lock(root):
        detail = student_detail(root, sid)
        if detail["delivery_locked"]:
            raise ReviewError("该学号已交付锁定，不能重新审核；请在主程序手动解除交付锁定")
        if not detail["review_enabled"] or not detail["in_roster"]:
            raise ReviewError("审核归档未启用，或学号不在已保存的全年级名单中")
        if detail["version"] != expected_version or detail["revision"] != expected_revision:
            raise ReviewError("图片或审核状态已经变化，请刷新后再审核")
        if not detail["exists"]:
            raise ReviewError("该学生尚未导出照片，不能标记处理结果")
        if decision == "approved" and not detail["can_approve"]:
            raise ReviewError("没有可用的最终处理结果，不能通过归档；请先完成图片处理")
        state = load_reviews(root)
        previous = state["reviews"].get(sid)
        action_id = uuid.uuid4().hex
        review = {"status": decision, "reviewed_at": timestamp(), "snapshot": detail["snapshot"], "revision": action_id}
        if decision == "approved":
            source = safe_file(root, detail["result_file"])
            digest = detail["snapshot"]["result"]["sha256"]
            relative = Path("审核归档") / sid / f"{digest}{source.suffix.lower()}"
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Copy, never move: normal results and historical reports stay readable.
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ReviewError("结果图片已经变化，请刷新后重新审核")
            fd, temp = tempfile.mkstemp(prefix=".archive_", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            review["archive_file"] = relative.as_posix()
        state["reviews"][sid] = review
        state["actions"].append({"id": action_id, "student_id": sid, "decision": decision,
                                 "at": timestamp(), "previous": previous, "undone": False})
        atomic_json(root / REVIEW_FILE, state)
    return {"action_id": action_id, "student_id": sid, "status": decision}


def undo_review(root: Path, action_id: str) -> dict:
    with output_write_lock(root):
        roster = load_roster(root)
        if not roster.get("enabled"):
            raise ReviewError("审核归档未启用")
        state = load_reviews(root)
        actions = [action for action in state["actions"] if not action.get("undone")]
        if not actions or actions[-1]["id"] != action_id:
            raise ReviewError("最近审核操作已经变化，请刷新后撤销")
        action = actions[-1]
        sid = action["student_id"]
        if sid in set(load_delivery_locks(root)["student_ids"]):
            raise ReviewError("该学号已交付锁定，不能撤销审核；请先在主程序手动解除交付锁定")
        if state["reviews"].get(sid, {}).get("revision") != action_id:
            raise ReviewError("该学号已有新的审核，不能撤销旧操作")
        if action["previous"] is None:
            state["reviews"].pop(sid, None)
        else:
            state["reviews"][sid] = action["previous"]
        action.update(undone=True, undone_at=timestamp())
        atomic_json(root / REVIEW_FILE, state)
    return {"student_id": sid, "status": "undone"}
