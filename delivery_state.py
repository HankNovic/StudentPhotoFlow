"""Shared, version-independent confirmed-delivery lock state (standard library only).

This is NOT approval of the current image. Only explicit import/unlock operations
change these IDs. Image changes, settings, and roster enable flags never do.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

DELIVERY_LOCK_FILE = "delivered_state.json"


class DeliveryStateError(ValueError):
    pass


def ids_digest(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def empty_delivery_locks() -> dict:
    return dict(schema_version=1, student_ids=[], count=0, sha256=ids_digest([]), revision="", actions=[])


def validate_delivery_locks(value: dict) -> dict:
    """Missing legacy file means no locks; existing damaged state fails closed."""
    ids = value.get("student_ids") if isinstance(value, dict) else None
    if (not isinstance(ids, list) or any(not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid)
                                       for sid in ids)
            or len({sid.casefold() for sid in ids}) != len(ids)
            or value.get("schema_version") != 1 or value.get("count") != len(ids)
            or value.get("sha256") != ids_digest(ids)
            or not isinstance(value.get("revision"), str) or (ids and not value["revision"])
            or not isinstance(value.get("actions"), list)
            or any(not isinstance(action, dict) or action.get("action") not in {"import", "unlock"}
                   or not action.get("id") or not isinstance(action.get("student_ids"), list)
                   for action in value.get("actions", []))):
        raise DeliveryStateError("已交付锁定记录损坏或版本不支持；为避免重复处理/交付已停止，请恢复 delivered_state.json 备份，勿删除锁定文件。")
    return value


def load_delivery_locks(root: Path) -> dict:
    path = Path(root) / DELIVERY_LOCK_FILE
    if not path.exists():
        return empty_delivery_locks()
    try:
        return validate_delivery_locks(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as exc:
        raise DeliveryStateError(f"无法读取已交付锁定记录 {path.name}：{exc}") from exc


def read_delivery_ids(path: Path) -> list[str]:
    """JSON is canonical; TXT/CSV/XLSX are handled by the existing roster reader."""
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise DeliveryStateError("JSON 必须为学号数组，或包含 student_ids 数组的对象")
    if "sha256" in value or "actions" in value:
        return validate_delivery_locks(value)["student_ids"]
    if not isinstance(value.get("student_ids"), list):
        raise DeliveryStateError("JSON 缺少 student_ids 数组")
    return value["student_ids"]
