from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import os
import posixpath
import re
import shutil
import ssl
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from xml.etree import ElementTree as ET

from photo_pipeline import (
    PIPELINE_VERSION,
    PipelineError,
    PipelineOptions,
    run_pipeline,
    save_pipeline_stages,
)


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAW_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
ART_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

NS = {
    "m": MAIN_NS,
    "r": DOC_REL_NS,
    "p": PKG_REL_NS,
    "xdr": DRAW_NS,
    "a": ART_NS,
}

APP_VERSION = "1.4.0"
STATE_SCHEMA = 3
MAX_IMAGE_BYTES = 30 * 1024 * 1024
VOLATILE_QUERY_RE = re.compile(
    r"(?:sign|signature|token|expires?|timestamp|q-ak|q-key-time|q-sign-time|"
    r"x-amz-signature|x-amz-credential|x-amz-date|x-amz-expires)",
    re.IGNORECASE,
)
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class WorkbookError(RuntimeError):
    pass


class ExportError(RuntimeError):
    pass


@dataclass
class SourceRef:
    kind: str
    value: str = ""
    data: bytes | None = None
    hint: str = ""
    fingerprint: str = ""
    display: str = ""
    expires_at: str | None = None


@dataclass
class SelectedRow:
    row_number: int
    student_id: str
    source: SourceRef


@dataclass
class SheetInfo:
    name: str
    part: str
    state: str


@dataclass
class InspectionReport:
    workbook: str
    sheet: str
    header_row: int
    headers: list[str]
    rows: list[SelectedRow]
    summary: dict[str, Any]


@dataclass
class ExportOptions:
    xlsx_path: Path
    output_dir: Path
    sheet_name: str
    header_row: int
    id_col: int
    image_col: int
    face_detection: bool = False  # legacy CLI alias for the built-in face check
    quality_enabled: bool = False
    auto_orient: bool = True
    check_grayscale: bool = True
    check_face: bool = True
    check_glare: bool = True
    check_recapture: bool = True
    grayscale_ratio_threshold: float = 0.85
    grayscale_delta_limit: int = 10
    face_confidence_threshold: float = 0.75
    orientation_min_confidence: float = 0.85
    orientation_confidence_margin: float = 0.08
    glare_ratio_threshold: float = 0.08
    glare_luma_threshold: int = 245
    recapture_score_threshold: float = 0.72
    stop_on_reject: bool = True
    save_intermediate_steps: bool = True
    background_mode: str = "ai"  # none | quick | ai | hivision
    background_color: str = "#438EDB"
    hivision_url: str = "http://127.0.0.1:8080"
    hivision_timeout: int = 120
    hivision_height: int = 413
    hivision_width: int = 295
    hivision_dpi: int = 300
    hivision_matting_model: str = "modnet_photographic_portrait_matting"
    hivision_face_model: str = "mtcnn"
    hivision_hd: bool = False
    hivision_face_align: bool = False
    hivision_head_measure_ratio: float = 0.20
    hivision_head_height_ratio: float = 0.45
    hivision_top_distance_max: float = 0.12
    hivision_top_distance_min: float = 0.10
    hivision_brightness_strength: float = 0.0
    hivision_contrast_strength: float = 0.0
    hivision_sharpen_strength: float = 0.0
    hivision_saturation_strength: float = 0.0
    crop_enabled: bool = False
    crop_width: int = 295
    crop_height: int = 413
    workers: int = 6
    force_refresh: bool = False
    timeout_seconds: int = 25


@dataclass
class JobResult:
    student_id: str
    row_number: int
    change: str
    status: str
    message: str = ""
    source_display: str = ""
    source_fingerprint: str = ""
    original_file: str | None = None
    original_sha256: str | None = None
    processed_file: str | None = None
    face_count: int | None = None
    quality_status: str = "not_requested"
    quality_reasons: list[dict[str, str]] = field(default_factory=list)
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    rotation_ccw: int = 0
    detector: str = "not_used"
    background_engine: str = "none"
    step_files: list[dict[str, Any]] = field(default_factory=list)
    processing_status: str = "not_requested"
    processing_message: str = ""
    executed: bool = True


@dataclass
class BatchResult:
    batch_id: str
    batch_dir: Path
    gallery_path: Path
    state_path: Path
    summary: dict[str, Any]
    results: list[JobResult] = field(default_factory=list)


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return now_local().isoformat(timespec="seconds")


def column_label(index: int) -> str:
    if index < 0:
        raise ValueError("列索引不能为负数")
    value = index + 1
    chars: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))


def column_index(label: str) -> int:
    text = label.strip().upper()
    if not re.fullmatch(r"[A-Z]+", text):
        raise ValueError(f"无效列标：{label}")
    value = 0
    for char in text:
        value = value * 26 + ord(char) - 64
    return value - 1


def resolve_column_spec(spec: str, headers: list[str]) -> int:
    text = str(spec).strip()
    if re.fullmatch(r"[A-Za-z]+", text):
        return column_index(text)
    if text.isdigit():
        index = int(text) - 1
        if index < 0:
            raise ValueError("列序号必须从 1 开始")
        return index
    for index, header in enumerate(headers):
        if header.strip() == text:
            return index
    raise ValueError(f"找不到列：{spec}")


def _rels_name(part: str) -> str:
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def _resolve_part(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _relationship_map(zf: zipfile.ZipFile, part: str) -> dict[str, dict[str, str]]:
    rels_name = _rels_name(part)
    if rels_name not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rels_name))
    result: dict[str, dict[str, str]] = {}
    for rel in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        attrs = dict(rel.attrib)
        if attrs.get("TargetMode") != "External":
            attrs["ResolvedTarget"] = _resolve_part(part, attrs.get("Target", ""))
        result[attrs.get("Id", "")] = attrs
    return result


def _office_document_part(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(zf.read("_rels/.rels"))
    for rel in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if rel.attrib.get("Type", "").endswith("/officeDocument"):
            return rel.attrib["Target"].lstrip("/")
    if "xl/workbook.xml" in zf.namelist():
        return "xl/workbook.xml"
    raise WorkbookError("不是有效的 Excel 工作簿：找不到 workbook.xml")


def _cell_ref_to_indexes(ref: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", ref)
    if not match:
        raise ValueError(ref)
    return int(match.group(2)), column_index(match.group(1))


def _numeric_text(raw: str, number_format: str | None = None) -> str:
    if not raw:
        return ""
    if number_format and re.fullmatch(r"0+", number_format) and re.fullmatch(r"-?\d+(?:\.0+)?", raw):
        negative = raw.startswith("-")
        digits = raw.lstrip("-").split(".", 1)[0]
        padded = digits.zfill(len(number_format))
        return ("-" if negative else "") + padded
    if re.fullmatch(r"-?\d+\.0+", raw):
        return raw.split(".", 1)[0]
    return raw


class WorkbookReader:
    """Small OOXML reader focused on values, hyperlinks, and worksheet pictures."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise WorkbookError(f"找不到工作簿：{self.path}")
        try:
            self.zf = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise WorkbookError(f"无法打开工作簿：{exc}") from exc
        self.names = set(self.zf.namelist())
        self.workbook_part = _office_document_part(self.zf)
        self.workbook_rels = _relationship_map(self.zf, self.workbook_part)
        self.shared_strings = self._read_shared_strings()
        self.number_formats = self._read_number_formats()
        self.sheets = self._read_sheets()

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> "WorkbookReader":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _read_shared_strings(self) -> list[str]:
        part = posixpath.join(posixpath.dirname(self.workbook_part), "sharedStrings.xml")
        if part not in self.names:
            return []
        root = ET.fromstring(self.zf.read(part))
        return ["".join(si.itertext()) for si in root.findall(f"{{{MAIN_NS}}}si")]

    def _read_number_formats(self) -> list[str | None]:
        part = posixpath.join(posixpath.dirname(self.workbook_part), "styles.xml")
        if part not in self.names:
            return []
        root = ET.fromstring(self.zf.read(part))
        custom: dict[int, str] = {}
        numfmts = root.find(f"{{{MAIN_NS}}}numFmts")
        if numfmts is not None:
            for fmt in numfmts.findall(f"{{{MAIN_NS}}}numFmt"):
                custom[int(fmt.attrib["numFmtId"])] = fmt.attrib.get("formatCode", "")
        formats: list[str | None] = []
        xfs = root.find(f"{{{MAIN_NS}}}cellXfs")
        if xfs is not None:
            for xf in xfs.findall(f"{{{MAIN_NS}}}xf"):
                formats.append(custom.get(int(xf.attrib.get("numFmtId", "0"))))
        return formats

    def _read_sheets(self) -> list[SheetInfo]:
        root = ET.fromstring(self.zf.read(self.workbook_part))
        result: list[SheetInfo] = []
        for sheet in root.findall(f".//{{{MAIN_NS}}}sheet"):
            rel_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id", "")
            rel = self.workbook_rels.get(rel_id)
            if not rel or "ResolvedTarget" not in rel:
                continue
            result.append(SheetInfo(
                name=sheet.attrib.get("name", ""),
                part=rel["ResolvedTarget"],
                state=sheet.attrib.get("state", "visible"),
            ))
        if not result:
            raise WorkbookError("工作簿中没有可读取的工作表")
        return result

    def sheet(self, name: str) -> SheetInfo:
        for item in self.sheets:
            if item.name == name:
                return item
        raise WorkbookError(f"找不到工作表：{name}")

    def _sheet_values(self, info: SheetInfo) -> tuple[dict[tuple[int, int], str], dict[tuple[int, int], str]]:
        root = ET.fromstring(self.zf.read(info.part))
        rels = _relationship_map(self.zf, info.part)
        values: dict[tuple[int, int], str] = {}
        hyperlinks: dict[tuple[int, int], str] = {}

        for cell in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row/{{{MAIN_NS}}}c"):
            ref = cell.attrib.get("r", "")
            if not ref:
                continue
            try:
                row_index, col_index = _cell_ref_to_indexes(ref)
            except ValueError:
                continue
            kind = cell.attrib.get("t", "")
            raw = cell.findtext(f"{{{MAIN_NS}}}v", default="")
            if kind == "s" and raw:
                try:
                    value = self.shared_strings[int(raw)]
                except (ValueError, IndexError):
                    value = raw
            elif kind == "inlineStr":
                inline = cell.find(f"{{{MAIN_NS}}}is")
                value = "".join(inline.itertext()) if inline is not None else ""
            elif kind == "b":
                value = "TRUE" if raw == "1" else "FALSE"
            else:
                style_index = int(cell.attrib.get("s", "0") or 0)
                fmt = self.number_formats[style_index] if style_index < len(self.number_formats) else None
                value = _numeric_text(raw, fmt)
            values[(row_index, col_index)] = value.strip()

        for link in root.findall(f".//{{{MAIN_NS}}}hyperlinks/{{{MAIN_NS}}}hyperlink"):
            ref = link.attrib.get("ref", "").split(":", 1)[0]
            try:
                indexes = _cell_ref_to_indexes(ref)
            except ValueError:
                continue
            rel_id = link.attrib.get(f"{{{DOC_REL_NS}}}id", "")
            rel = rels.get(rel_id)
            if rel and rel.get("TargetMode") == "External":
                hyperlinks[indexes] = rel.get("Target", "")
            elif link.attrib.get("location"):
                hyperlinks[indexes] = link.attrib["location"]
        return values, hyperlinks

    def _sheet_images(self, info: SheetInfo) -> dict[tuple[int, int], list[SourceRef]]:
        root = ET.fromstring(self.zf.read(info.part))
        sheet_rels = _relationship_map(self.zf, info.part)
        result: dict[tuple[int, int], list[SourceRef]] = defaultdict(list)
        for drawing in root.findall(f".//{{{MAIN_NS}}}drawing"):
            rel_id = drawing.attrib.get(f"{{{DOC_REL_NS}}}id", "")
            rel = sheet_rels.get(rel_id)
            if not rel or "ResolvedTarget" not in rel:
                continue
            drawing_part = rel["ResolvedTarget"]
            if drawing_part not in self.names:
                continue
            drawing_root = ET.fromstring(self.zf.read(drawing_part))
            drawing_rels = _relationship_map(self.zf, drawing_part)
            for anchor in list(drawing_root):
                from_node = anchor.find(f"{{{DRAW_NS}}}from")
                if from_node is None:
                    continue
                row_text = from_node.findtext(f"{{{DRAW_NS}}}row")
                col_text = from_node.findtext(f"{{{DRAW_NS}}}col")
                if row_text is None or col_text is None:
                    continue
                row_index = int(row_text) + 1
                col_index = int(col_text)
                for blip in anchor.findall(f".//{{{ART_NS}}}blip"):
                    embed_id = blip.attrib.get(f"{{{DOC_REL_NS}}}embed", "")
                    link_id = blip.attrib.get(f"{{{DOC_REL_NS}}}link", "")
                    image_rel = drawing_rels.get(embed_id or link_id)
                    if not image_rel:
                        continue
                    if image_rel.get("TargetMode") == "External":
                        result[(row_index, col_index)].append(
                            source_from_text(image_rel.get("Target", ""), self.path.parent)
                        )
                    else:
                        media_part = image_rel.get("ResolvedTarget", "")
                        if media_part not in self.names:
                            continue
                        data = self.zf.read(media_part)
                        digest = hashlib.sha256(data).hexdigest()
                        result[(row_index, col_index)].append(SourceRef(
                            kind="embedded",
                            data=data,
                            hint=media_part,
                            fingerprint=f"sha256:{digest}",
                            display=f"内嵌图片：{posixpath.basename(media_part)}",
                        ))
        return result

    def headers(self, sheet_name: str, header_row: int = 1) -> list[str]:
        info = self.sheet(sheet_name)
        values, _ = self._sheet_values(info)
        max_col = max((col for row, col in values if row == header_row), default=-1)
        if max_col < 0:
            raise WorkbookError(f"第 {header_row} 行没有表头")
        return [values.get((header_row, col), "") for col in range(max_col + 1)]

    def selected_rows(
        self,
        sheet_name: str,
        header_row: int,
        id_col: int,
        image_col: int,
    ) -> tuple[list[str], list[SelectedRow]]:
        info = self.sheet(sheet_name)
        values, hyperlinks = self._sheet_values(info)
        images = self._sheet_images(info)
        max_col = max(
            [id_col, image_col]
            + [col for row, col in values if row == header_row],
        )
        headers = [values.get((header_row, col), "") for col in range(max_col + 1)]
        max_row = max(
            [header_row]
            + [row for row, _ in values]
            + [row for row, _ in images],
        )
        rows: list[SelectedRow] = []
        for row_number in range(header_row + 1, max_row + 1):
            student_id = values.get((row_number, id_col), "").strip()
            embedded = images.get((row_number, image_col), [])
            cell_text = values.get((row_number, image_col), "").strip()
            link = hyperlinks.get((row_number, image_col), "").strip()
            if embedded:
                source = embedded[0]
            elif _looks_like_source(link):
                source = source_from_text(link, self.path.parent)
            elif cell_text:
                source = source_from_text(cell_text, self.path.parent)
            elif link:
                source = source_from_text(link, self.path.parent)
            else:
                source = SourceRef(kind="missing", display="空白")
            if not student_id and source.kind == "missing":
                continue
            rows.append(SelectedRow(row_number, student_id, source))
        return headers, rows


def _looks_like_source(value: str) -> bool:
    text = value.strip().lower()
    return text.startswith(("http://", "https://", "data:image/", "file://"))


def canonicalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    kept: list[tuple[str, str]] = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if not VOLATILE_QUERY_RE.search(key):
            kept.append((key, value))
    query = urllib.parse.urlencode(sorted(kept), doseq=True)
    hostname = (parsed.hostname or "").lower()
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), hostname, parsed.path, query, ""))


def signed_url_expiry(url: str) -> datetime | None:
    decoded = urllib.parse.unquote(url)
    match = re.search(r"q-sign-time=(\d{9,12})[;%3B]+(\d{9,12})", decoded, re.IGNORECASE)
    if not match:
        match = re.search(r"q-sign-time%3D(\d{9,12})%3B(\d{9,12})", url, re.IGNORECASE)
    if match:
        try:
            return datetime.fromtimestamp(int(match.group(2)), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    for key in ("Expires", "expires", "expiry"):
        if key in query and query[key].isdigit():
            try:
                return datetime.fromtimestamp(int(query[key]), tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                pass
    return None


def source_from_text(text: str, base_dir: Path) -> SourceRef:
    value = text.strip()
    lower = value.lower()
    if lower in {"", "-", "--", "无", "暂无", "未上传", "未填写", "null", "none", "n/a"}:
        return SourceRef(kind="missing", display=value or "空白")
    if lower.startswith(("http://", "https://")):
        canonical = canonicalize_url(value)
        expiry = signed_url_expiry(value)
        return SourceRef(
            kind="url",
            value=value,
            hint=urllib.parse.urlsplit(value).path,
            fingerprint=f"url:{canonical}",
            display=canonical,
            expires_at=expiry.isoformat() if expiry else None,
        )
    if lower.startswith("data:image/"):
        try:
            header, payload = value.split(",", 1)
            data = base64.b64decode(payload) if ";base64" in header.lower() else urllib.parse.unquote_to_bytes(payload)
            mime = header[5:].split(";", 1)[0]
            digest = hashlib.sha256(data).hexdigest()
            return SourceRef(
                kind="data_uri",
                data=data,
                hint=mime,
                fingerprint=f"sha256:{digest}",
                display=f"Data URI（{mime}）",
            )
        except (ValueError, base64.binascii.Error) as exc:
            return SourceRef(kind="unsupported", value=value, display=f"无效 Data URI：{exc}")
    if lower.startswith("file://"):
        parsed_path = urllib.request.url2pathname(urllib.parse.urlsplit(value).path)
        path = Path(parsed_path)
    else:
        path = Path(value)
        if not path.is_absolute():
            path = base_dir / path
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    if resolved.is_file():
        stat = resolved.stat()
        fingerprint = f"file:{resolved}|{stat.st_size}|{stat.st_mtime_ns}"
        return SourceRef(
            kind="file",
            value=str(resolved),
            hint=resolved.name,
            fingerprint=fingerprint,
            display=str(resolved),
        )
    return SourceRef(
        kind="unsupported",
        value=value,
        fingerprint="text:" + hashlib.sha256(value.encode("utf-8")).hexdigest(),
        display=value or "空白",
    )


def inspect_selection(
    path: str | Path,
    sheet_name: str,
    header_row: int,
    id_col: int,
    image_col: int,
) -> InspectionReport:
    with WorkbookReader(path) as reader:
        headers, rows = reader.selected_rows(sheet_name, header_row, id_col, image_col)
    ids = [row.student_id for row in rows if row.student_id]
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    expired = 0
    expiring = 0
    current = datetime.now(timezone.utc)
    for row in rows:
        if not row.source.expires_at:
            continue
        try:
            expiry = datetime.fromisoformat(row.source.expires_at)
        except ValueError:
            continue
        if expiry <= current:
            expired += 1
        elif (expiry - current).total_seconds() <= 900:
            expiring += 1
    source_counts = Counter(row.source.kind for row in rows)
    summary = {
        "data_rows": len(rows),
        "valid_ids": len(ids),
        "empty_ids": sum(1 for row in rows if not row.student_id),
        "duplicate_ids": duplicates,
        "duplicate_count": len(duplicates),
        "source_counts": dict(source_counts),
        "expired_links": expired,
        "expiring_links": expiring,
        "missing_sources": source_counts.get("missing", 0),
        "unsupported_sources": source_counts.get("unsupported", 0),
    }
    return InspectionReport(str(Path(path)), sheet_name, header_row, headers, rows, summary)


def validate_student_id(student_id: str) -> str | None:
    if not student_id:
        return "学号为空"
    if student_id in {".", ".."} or student_id.endswith((".", " ")):
        return "学号不能作为 Windows 文件名"
    if INVALID_FILENAME_RE.search(student_id):
        return "学号包含 Windows 文件名禁用字符"
    if student_id.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        return "学号是 Windows 保留文件名"
    return None


def detect_image_extension(data: bytes, hint: str = "") -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return ".tif"
    if len(data) >= 12 and data[8:12] == b"WEBP" and data[0:4] == b"RIFF":
        return ".webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"hevc", b"mif1"}:
        return ".heic"
    suffix = Path(urllib.parse.urlsplit(hint).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}:
        return ".jpg" if suffix == ".jpeg" else ".tif" if suffix == ".tiff" else suffix
    raise ExportError("内容不是受支持的图片格式，可能是过期链接返回的错误页面")


def _safe_state_file(root: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        return None
    return candidate


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".photo_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, value: Any) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(path, data)


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": STATE_SCHEMA,
            "app_version": APP_VERSION,
            "created_at": iso_now(),
            "updated_at": iso_now(),
            "records": {},
            "batches": [],
        }
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"状态文件损坏，已停止以避免丢失历史：{path}（{exc}）") from exc
    if not isinstance(state, dict) or not isinstance(state.get("records"), dict):
        raise ExportError(f"状态文件格式无效：{path}")
    if int(state.get("schema_version", 0)) > STATE_SCHEMA:
        raise ExportError("状态文件来自更高版本的工具，请升级本工具")
    state.setdefault("batches", [])
    state["schema_version"] = STATE_SCHEMA
    state["app_version"] = APP_VERSION
    return state


def _processing_fingerprint(options: ExportOptions) -> str:
    payload = json.dumps({
        "face_detection": options.face_detection,
        "quality_enabled": options.quality_enabled,
        "auto_orient": options.auto_orient,
        "check_grayscale": options.check_grayscale,
        "check_face": options.check_face,
        "check_glare": options.check_glare,
        "check_recapture": options.check_recapture,
        "grayscale_ratio_threshold": options.grayscale_ratio_threshold,
        "grayscale_delta_limit": options.grayscale_delta_limit,
        "face_confidence_threshold": options.face_confidence_threshold,
        "orientation_min_confidence": options.orientation_min_confidence,
        "orientation_confidence_margin": options.orientation_confidence_margin,
        "glare_ratio_threshold": options.glare_ratio_threshold,
        "glare_luma_threshold": options.glare_luma_threshold,
        "recapture_score_threshold": options.recapture_score_threshold,
        "stop_on_reject": options.stop_on_reject,
        "background_mode": options.background_mode,
        "background_color": options.background_color.upper(),
        "hivision_url": options.hivision_url.rstrip("/"),
        "hivision_height": options.hivision_height,
        "hivision_width": options.hivision_width,
        "hivision_dpi": options.hivision_dpi,
        "hivision_matting_model": options.hivision_matting_model,
        "hivision_face_model": options.hivision_face_model,
        "hivision_hd": options.hivision_hd,
        "hivision_face_align": options.hivision_face_align,
        "hivision_head_measure_ratio": options.hivision_head_measure_ratio,
        "hivision_head_height_ratio": options.hivision_head_height_ratio,
        "hivision_top_distance_max": options.hivision_top_distance_max,
        "hivision_top_distance_min": options.hivision_top_distance_min,
        "hivision_brightness_strength": options.hivision_brightness_strength,
        "hivision_contrast_strength": options.hivision_contrast_strength,
        "hivision_sharpen_strength": options.hivision_sharpen_strength,
        "hivision_saturation_strength": options.hivision_saturation_strength,
        "crop_enabled": options.crop_enabled,
        "crop_width": options.crop_width,
        "crop_height": options.crop_height,
        "pipeline_version": PIPELINE_VERSION,
    }, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _pipeline_options(options: ExportOptions) -> PipelineOptions:
    legacy_face_only = options.face_detection and not options.quality_enabled
    return PipelineOptions(
        quality_enabled=options.quality_enabled or options.face_detection,
        auto_orient=options.auto_orient,
        check_grayscale=options.check_grayscale if options.quality_enabled else False,
        check_face=options.check_face if options.quality_enabled else legacy_face_only,
        check_glare=options.check_glare if options.quality_enabled else False,
        check_recapture=options.check_recapture if options.quality_enabled else False,
        grayscale_ratio_threshold=options.grayscale_ratio_threshold,
        grayscale_delta_limit=options.grayscale_delta_limit,
        face_confidence_threshold=options.face_confidence_threshold,
        orientation_min_confidence=options.orientation_min_confidence,
        orientation_confidence_margin=options.orientation_confidence_margin,
        glare_ratio_threshold=options.glare_ratio_threshold,
        glare_luma_threshold=options.glare_luma_threshold,
        recapture_score_threshold=options.recapture_score_threshold,
        stop_on_reject=options.stop_on_reject,
        background_mode=options.background_mode,
        background_color=options.background_color,
        hivision_url=options.hivision_url,
        hivision_timeout=options.hivision_timeout,
        hivision_height=options.hivision_height,
        hivision_width=options.hivision_width,
        hivision_dpi=options.hivision_dpi,
        hivision_matting_model=options.hivision_matting_model,
        hivision_face_model=options.hivision_face_model,
        hivision_hd=options.hivision_hd,
        hivision_face_align=options.hivision_face_align,
        hivision_head_measure_ratio=options.hivision_head_measure_ratio,
        hivision_head_height_ratio=options.hivision_head_height_ratio,
        hivision_top_distance_max=options.hivision_top_distance_max,
        hivision_top_distance_min=options.hivision_top_distance_min,
        hivision_brightness_strength=options.hivision_brightness_strength,
        hivision_contrast_strength=options.hivision_contrast_strength,
        hivision_sharpen_strength=options.hivision_sharpen_strength,
        hivision_saturation_strength=options.hivision_saturation_strength,
        crop_enabled=options.crop_enabled,
        crop_width=options.crop_width,
        crop_height=options.crop_height,
    )


def _fetch_source(source: SourceRef, timeout: int) -> bytes:
    if source.kind in {"embedded", "data_uri"}:
        if source.data is None:
            raise ExportError("图片数据为空")
        return source.data
    if source.kind == "file":
        try:
            data = Path(source.value).read_bytes()
        except OSError as exc:
            raise ExportError(f"读取本地图片失败：{exc}") from exc
        if len(data) > MAX_IMAGE_BYTES:
            raise ExportError("图片超过 30 MB 限制")
        return data
    if source.kind != "url":
        if source.kind == "missing":
            raise ExportError("照片单元格为空")
        raise ExportError("照片单元格不是网址、本地文件或内嵌图片")
    expiry = signed_url_expiry(source.value)
    if expiry and expiry <= datetime.now(timezone.utc):
        raise ExportError("照片链接已过期；请重新导出最新 Excel 后立即运行")
    request = urllib.request.Request(
        source.value,
        headers={
            "User-Agent": "Mozilla/5.0 PhotoExporter/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                raise ExportError("图片超过 30 MB 限制")
            data = response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ExportError(f"下载失败（HTTP {exc.code}）：链接已过期或无权限，请重新导出 Excel") from exc
        raise ExportError(f"下载失败（HTTP {exc.code}）") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ExportError(f"下载失败：{exc}") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise ExportError("图片超过 30 MB 限制")
    if not data:
        raise ExportError("服务器返回了空文件")
    return data


def _archive_then_install(
    data: bytes,
    destination: Path,
    previous: Path | None,
    history_dir: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".incoming_", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if previous and previous.exists():
            archive = history_dir / previous.name
            if previous.resolve() == destination.resolve():
                shutil.copy2(previous, archive)
            else:
                if archive.exists():
                    archive = history_dir / f"{previous.stem}_{hashlib.sha1(str(previous).encode()).hexdigest()[:8]}{previous.suffix}"
                shutil.move(str(previous), str(archive))
        os.replace(temp_name, destination)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _parse_color(color: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", color.strip())
    if not match:
        raise ExportError(f"背景色格式无效：{color}")
    text = match.group(1)
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _load_upright_image(data: bytes):
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ExportError("缺少 Pillow；请重新运行“启动工具.bat”自动检查依赖") from exc
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return ImageOps.exif_transpose(image)
    except Exception as exc:
        raise ExportError(f"图片解码失败：{exc}") from exc


def _quick_replace_background(data: bytes, color: str) -> bytes:
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError as exc:
        raise ExportError("快速换背景需要 Pillow 和 NumPy；请重新运行“启动工具.bat”自动检查依赖") from exc
    image = _load_upright_image(data).convert("RGB")
    array = np.asarray(image).copy()
    height, width = array.shape[:2]
    block = max(3, min(height, width) // 40)
    corners = np.concatenate([
        array[:block, :block].reshape(-1, 3),
        array[:block, -block:].reshape(-1, 3),
        array[-block:, :block].reshape(-1, 3),
        array[-block:, -block:].reshape(-1, 3),
    ])
    estimated = np.median(corners, axis=0)
    distance = np.linalg.norm(array.astype(np.float32) - estimated.astype(np.float32), axis=2)
    candidate = (distance < 58).astype(np.uint8) * 255
    flood_mask = Image.fromarray(candidate, mode="L").copy()
    # Mark every candidate component touching the image boundary. Pillow's C-backed
    # flood fill keeps this fast without making the quick mode depend on OpenCV.
    for x in range(width):
        if flood_mask.getpixel((x, 0)) == 255:
            ImageDraw.floodfill(flood_mask, (x, 0), 128, thresh=0)
        if flood_mask.getpixel((x, height - 1)) == 255:
            ImageDraw.floodfill(flood_mask, (x, height - 1), 128, thresh=0)
    for y in range(height):
        if flood_mask.getpixel((0, y)) == 255:
            ImageDraw.floodfill(flood_mask, (0, y), 128, thresh=0)
        if flood_mask.getpixel((width - 1, y)) == 255:
            ImageDraw.floodfill(flood_mask, (width - 1, y), 128, thresh=0)
    connected = np.asarray(flood_mask) == 128
    if not connected.any():
        raise ExportError("无法识别连通的纯色背景；可改用 AI 智能抠图")
    radius = max(1.0, min(height, width) / 320.0)
    mask_image = Image.fromarray(connected.astype(np.uint8) * 255, mode="L")
    mask = np.asarray(mask_image.filter(ImageFilter.GaussianBlur(radius=radius)))
    alpha = mask.astype(np.float32)[..., None] / 255.0
    target = np.array(_parse_color(color), dtype=np.float32).reshape(1, 1, 3)
    output = array.astype(np.float32) * (1.0 - alpha) + target * alpha
    final = Image.fromarray(np.clip(output, 0, 255).astype(np.uint8), mode="RGB")
    stream = io.BytesIO()
    final.save(stream, format="JPEG", quality=95, subsampling=0, optimize=True)
    return stream.getvalue()


_REMBG_LOCK = threading.Lock()
_REMBG_SESSION: Any = None


def _ai_replace_background(data: bytes, color: str) -> bytes:
    global _REMBG_SESSION
    try:
        from PIL import Image
        from rembg import new_session, remove
    except ImportError as exc:
        raise ExportError("AI 换背景组件不可用；请使用完整便携版，或重新运行“启动工具.bat”自动修复依赖") from exc
    image = _load_upright_image(data).convert("RGBA")
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    bundled_model = resource_root / "models" / "u2netp" / "u2netp.onnx"
    if bundled_model.is_file():
        os.environ.setdefault("U2NET_HOME", str(resource_root))
    # rembg may initialize a model; serializing initialization avoids duplicate sessions.
    with _REMBG_LOCK:
        if _REMBG_SESSION is None:
            _REMBG_SESSION = new_session("u2netp")
        session = _REMBG_SESSION
    cutout = remove(image, session=session)
    if isinstance(cutout, bytes):
        cutout = Image.open(io.BytesIO(cutout)).convert("RGBA")
    else:
        cutout = cutout.convert("RGBA")
    background = Image.new("RGBA", cutout.size, _parse_color(color) + (255,))
    composited = Image.alpha_composite(background, cutout).convert("RGB")
    stream = io.BytesIO()
    composited.save(stream, format="JPEG", quality=95, subsampling=0, optimize=True)
    return stream.getvalue()


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _execute_job(
    row: SelectedRow,
    change: str,
    existing: dict[str, Any] | None,
    options: ExportOptions,
    batch_id: str,
) -> JobResult:
    root = options.output_dir
    result = JobResult(
        student_id=row.student_id,
        row_number=row.row_number,
        change=change,
        status="failed",
        source_display=row.source.display,
        source_fingerprint=row.source.fingerprint,
    )
    try:
        previous_original = _safe_state_file(root, existing.get("original_file") if existing else None)
        if change == "reprocessed":
            if previous_original is None or not previous_original.is_file():
                raise ExportError("历史原图不存在，无法重新处理")
            data = previous_original.read_bytes()
            extension = previous_original.suffix.lower()
            original_path = previous_original
        else:
            data = _fetch_source(row.source, options.timeout_seconds)
            extension = detect_image_extension(data, row.source.hint)
            original_path = root / "原始图片" / f"{row.student_id}{extension}"
            history = root / "历史版本" / batch_id / "原始图片"
            _archive_then_install(data, original_path, previous_original, history)
            # Once the source photo changes, any older processed image is stale.
            # Archive it before attempting the new processing so a processing
            # failure cannot leave an outdated file looking current.
            if change == "updated" and existing:
                stale_processed = _safe_state_file(
                    root,
                    existing.get("processing", {}).get("processed_file"),
                )
                if stale_processed and stale_processed.is_file():
                    stale_history = root / "历史版本" / batch_id / "处理后图片"
                    stale_history.mkdir(parents=True, exist_ok=True)
                    archive_target = stale_history / stale_processed.name
                    if archive_target.exists():
                        archive_target = stale_history / (
                            f"{stale_processed.stem}_旧_{hashlib.sha1(str(stale_processed).encode()).hexdigest()[:8]}"
                            f"{stale_processed.suffix}"
                        )
                    shutil.move(str(stale_processed), str(archive_target))
        digest = hashlib.sha256(data).hexdigest()
        result.original_file = _relative(root, original_path)
        result.original_sha256 = digest
        result.status = "success"

        processing_requested = (
            options.quality_enabled
            or options.face_detection
            or options.background_mode != "none"
            or options.crop_enabled
        )
        if not processing_requested:
            result.processing_status = "not_requested"
            return result

        try:
            pipeline = run_pipeline(data, _pipeline_options(options))
            result.face_count = pipeline.face_count
            result.quality_status = pipeline.status if (options.quality_enabled or options.face_detection) else "not_requested"
            result.quality_reasons = pipeline.reasons
            result.quality_metrics = pipeline.metrics
            result.rotation_ccw = pipeline.rotation_ccw
            result.detector = pipeline.detector
            result.background_engine = pipeline.background_engine
            if options.save_intermediate_steps:
                stage_dir = root / "批次记录" / batch_id / "处理步骤" / row.student_id
                saved_steps = save_pipeline_stages(pipeline, stage_dir)
                for saved in saved_steps:
                    saved["file"] = _relative(root, Path(saved["file"]))
                result.step_files = saved_steps

            if pipeline.status == "rejected":
                result.processing_status = "rejected"
                result.processing_message = "；".join(reason["message"] for reason in pipeline.reasons)
                return result

            should_write_output = (
                pipeline.output_bytes is not None
                and (
                    options.background_mode != "none"
                    or pipeline.rotation_ccw != 0
                    or options.crop_enabled
                )
            )
            if should_write_output:
                processed_path = root / "处理后图片" / f"{row.student_id}.jpg"
                previous_processed = _safe_state_file(
                    root,
                    existing.get("processing", {}).get("processed_file") if existing else None,
                )
                history = root / "历史版本" / batch_id / "处理后图片"
                _archive_then_install(pipeline.output_bytes, processed_path, previous_processed, history)
                result.processed_file = _relative(root, processed_path)
            result.processing_status = "warning" if pipeline.status == "warning" else "success"
            if pipeline.reasons:
                result.processing_message = "；".join(reason["message"] for reason in pipeline.reasons)
        except PipelineError as exc:
            result.processing_status = "warning"
            result.processing_message = str(exc)
        return result
    except (ExportError, OSError, ValueError) as exc:
        result.status = "failed"
        result.message = str(exc)
        return result
    except Exception as exc:  # keep one bad row from stopping the batch
        result.status = "failed"
        result.message = f"未预期错误：{type(exc).__name__}: {exc}"
        return result


def _copy_batch_images(root: Path, batch_dir: Path, result: JobResult) -> dict[str, Any]:
    group_names = {
        "new": "本次新增",
        "updated": "本次更新",
        "repair": "本次修复",
        "reprocessed": "本次重新处理",
    }
    group = group_names.get(result.change)
    copied: dict[str, Any] = {}
    if not group or result.status != "success":
        return copied
    if result.original_file:
        source = _safe_state_file(root, result.original_file)
        if source and source.is_file():
            destination = batch_dir / group / "原始" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied["original"] = destination.relative_to(batch_dir).as_posix()
    if result.processed_file:
        source = _safe_state_file(root, result.processed_file)
        if source and source.is_file():
            destination = batch_dir / group / "处理后" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied["processed"] = destination.relative_to(batch_dir).as_posix()
    step_paths: list[dict[str, Any]] = []
    for step in result.step_files:
        source = _safe_state_file(root, step.get("file"))
        if source and source.is_file():
            try:
                relative = source.relative_to(batch_dir).as_posix()
            except ValueError:
                destination = batch_dir / group / "处理步骤" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                relative = destination.relative_to(batch_dir).as_posix()
            step_paths.append({**step, "file": relative})
    if step_paths:
        copied["steps"] = step_paths
    return copied


def _write_manifest(batch_dir: Path, results: list[JobResult]) -> None:
    path = batch_dir / "manifest.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "学号", "Excel行号", "变更类型", "导出状态", "预检状态", "退回原因",
            "人脸数", "旋转角度", "检测器", "处理引擎", "图片来源",
            "原图文件", "处理后文件", "处理状态", "说明",
        ])
        for item in results:
            writer.writerow([
                item.student_id,
                item.row_number,
                item.change,
                item.status,
                item.quality_status,
                "；".join(reason.get("message", "") for reason in item.quality_reasons),
                "" if item.face_count is None else item.face_count,
                item.rotation_ccw,
                item.detector,
                item.background_engine,
                item.source_display,
                item.original_file or "",
                item.processed_file or "",
                item.processing_status,
                item.message or item.processing_message,
            ])


def _write_reupload_manifest(batch_dir: Path, results: list[JobResult]) -> Path | None:
    rejected = [item for item in results if item.quality_status == "rejected"]
    if not rejected:
        return None
    path = batch_dir / "需重传名单.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["学号", "Excel行号", "退回原因代码", "给学生的说明"])
        for item in rejected:
            writer.writerow([
                item.student_id,
                item.row_number,
                ",".join(reason.get("code", "") for reason in item.quality_reasons),
                "；".join(reason.get("message", "") for reason in item.quality_reasons),
            ])
    return path


def _write_gallery(
    batch_dir: Path,
    batch_id: str,
    results: list[JobResult],
    copied: dict[tuple[str, int], dict[str, Any]],
    summary: dict[str, Any],
) -> Path:
    cards: list[str] = []
    change_labels = {
        "new": "新增", "updated": "更新", "repair": "修复",
        "reprocessed": "重新处理", "unchanged": "未变化", "invalid": "失败",
    }
    visible = [item for item in results if item.change != "unchanged" or item.status == "failed"]
    for item in visible:
        paths = copied.get((item.student_id, item.row_number), {})
        step_figures: list[str] = []
        for step in paths.get("steps", []):
            step_path = step.get("file", "")
            if not step_path:
                continue
            encoded = "/".join(urllib.parse.quote(part) for part in step_path.split("/"))
            label = html.escape(str(step.get("label", step.get("code", "处理步骤"))))
            detail = html.escape(str(step.get("detail", "")))
            step_status = html.escape(str(step.get("status", "")))
            step_figures.append(
                f'<figure><img loading="lazy" src="{encoded}" alt="{label}">'
                f'<figcaption><strong>{label}</strong><span class="stage-status {step_status}">{step_status}</span>'
                f'<small>{detail}</small></figcaption></figure>'
            )
        image_path = paths.get("processed") or paths.get("original")
        if step_figures:
            visual = f'<div class="steps">{"".join(step_figures)}</div>'
        elif image_path:
            encoded = "/".join(urllib.parse.quote(part) for part in image_path.split("/"))
            visual = f'<div class="steps"><figure><img loading="lazy" src="{encoded}" alt="{html.escape(item.student_id)}"><figcaption><strong>结果</strong></figcaption></figure></div>'
        else:
            visual = '<div class="no-image">无可用图片</div>'
        status_class = "reject" if item.quality_status == "rejected" else (
            "ok" if item.status == "success" and item.processing_status != "warning" else "warn"
        )
        note = item.message or item.processing_message or "处理完成"
        face = "" if item.face_count is None else f" · 人脸 {item.face_count}"
        cards.append(f"""
        <article class="card {status_class}">
          <div class="meta">
            <h2>{html.escape(item.student_id or '(空学号)')}</h2>
            <p>{html.escape(change_labels.get(item.change, item.change))} · Excel 第 {item.row_number} 行{face}</p>
            <p class="note">{html.escape(note)}</p>
          </div>
          {visual}
        </article>""")
    if not cards:
        cards.append('<div class="empty">本批次没有新增、更新或失败记录。</div>')
    summary_text = " · ".join([
        f"新增 {summary.get('new', 0)}",
        f"更新 {summary.get('updated', 0)}",
        f"重新处理 {summary.get('reprocessed', 0)}",
        f"未变化 {summary.get('unchanged', 0)}",
        f"需重传 {summary.get('quality_rejected', 0)}",
        f"失败 {summary.get('failed', 0)}",
    ])
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>学生照片批次 {html.escape(batch_id)}</title>
  <style>
    :root {{ color-scheme: light; font-family: "Microsoft YaHei", system-ui, sans-serif; }}
    body {{ margin: 0; background: #f4f7fb; color: #172033; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 22px 28px; background: #17365d; color: white; box-shadow: 0 2px 12px #0002; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }} header p {{ margin: 0; opacity: .9; }}
    main {{ padding: 24px; display: grid; gap: 18px; }}
    .card {{ overflow: hidden; border-radius: 12px; background: white; box-shadow: 0 5px 20px #1c355714; border: 1px solid #dce5ef; }}
    .card.warn {{ border-color: #e4a11b; }}
    .card.reject {{ border-color: #d92d20; }}
    .steps {{ display: flex; gap: 14px; padding: 0 14px 16px; overflow-x: auto; scroll-snap-type: x proximity; }}
    figure {{ flex: 0 0 220px; margin: 0; scroll-snap-align: start; border: 1px solid #dce5ef; border-radius: 10px; overflow: hidden; }}
    img, .no-image {{ width: 100%; aspect-ratio: 3/4; object-fit: contain; background: #e8edf4; display: block; }}
    figcaption {{ padding: 9px; display: grid; grid-template-columns: 1fr auto; gap: 5px; font-size: 12px; }}
    figcaption small {{ grid-column: 1 / -1; color: #667085; line-height: 1.45; }}
    .stage-status {{ border-radius: 999px; padding: 1px 7px; background: #eef2f6; }}
    .stage-status.passed, .stage-status.adjusted {{ color: #067647; background: #ecfdf3; }}
    .stage-status.rejected {{ color: #b42318; background: #fef3f2; }}
    .no-image {{ display: grid; place-items: center; color: #758195; }}
    .meta {{ padding: 14px; }} h2 {{ margin: 0 0 7px; font-size: 18px; }}
    .meta p {{ margin: 4px 0; color: #5d6879; font-size: 13px; }} .note {{ color: #9a5c00 !important; }}
    .empty {{ grid-column: 1 / -1; padding: 40px; text-align: center; background: white; border-radius: 12px; }}
  </style>
</head>
<body>
  <header><h1>学生照片批次 {html.escape(batch_id)}</h1><p>{html.escape(summary_text)}</p></header>
  <main>{''.join(cards)}</main>
</body>
</html>"""
    path = batch_dir / "index.html"
    path.write_text(document, encoding="utf-8")
    return path


def _execute_job_after_resume(
    run_event: threading.Event | None,
    row: SelectedRow,
    change: str,
    existing: dict[str, Any] | None,
    options: ExportOptions,
    batch_id: str,
) -> JobResult:
    if run_event is not None:
        run_event.wait()
    return _execute_job(row, change, existing, options, batch_id)


def run_export(
    options: ExportOptions,
    progress: Callable[[int, int, str], None] | None = None,
    run_event: threading.Event | None = None,
) -> BatchResult:
    options.xlsx_path = Path(options.xlsx_path)
    options.output_dir = Path(options.output_dir)
    if options.header_row < 1:
        raise ExportError("表头行必须大于等于 1")
    if options.id_col < 0 or options.image_col < 0:
        raise ExportError("列索引无效")
    if options.id_col == options.image_col:
        raise ExportError("学号列和图片列不能是同一列")
    if options.background_mode not in {"none", "quick", "ai", "hivision"}:
        raise ExportError("背景处理模式无效")
    _parse_color(options.background_color)
    if options.crop_enabled and not (
        32 <= int(options.crop_width) <= 10000
        and 32 <= int(options.crop_height) <= 10000
    ):
        raise ExportError("最终裁切宽高必须在 32 到 10000 像素之间")
    options.workers = max(1, min(16, int(options.workers)))
    options.output_dir.mkdir(parents=True, exist_ok=True)

    report = inspect_selection(
        options.xlsx_path,
        options.sheet_name,
        options.header_row,
        options.id_col,
        options.image_col,
    )
    batch_id = now_local().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    batch_dir = options.output_dir / "批次记录" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    state_path = options.output_dir / "export_state.json"
    state = _load_state(state_path)
    records: dict[str, dict[str, Any]] = state["records"]
    processing_fp = _processing_fingerprint(options)

    id_counts = Counter(row.student_id for row in report.rows if row.student_id)
    duplicate_ids = {student_id for student_id, count in id_counts.items() if count > 1}
    invalid_ids: dict[str, str] = {}
    for row in report.rows:
        reason = validate_student_id(row.student_id)
        if reason:
            invalid_ids[f"{row.student_id}\0{row.row_number}"] = reason

    immediate: list[JobResult] = []
    jobs: list[tuple[SelectedRow, str, dict[str, Any] | None]] = []
    unchanged_results: list[JobResult] = []

    for row in report.rows:
        invalid_key = f"{row.student_id}\0{row.row_number}"
        if invalid_key in invalid_ids:
            immediate.append(JobResult(
                student_id=row.student_id,
                row_number=row.row_number,
                change="invalid",
                status="failed",
                message=invalid_ids[invalid_key],
                source_display=row.source.display,
                source_fingerprint=row.source.fingerprint,
            ))
            continue
        if row.student_id in duplicate_ids:
            immediate.append(JobResult(
                student_id=row.student_id,
                row_number=row.row_number,
                change="invalid",
                status="failed",
                message="同一工作表中学号重复，已跳过以避免覆盖",
                source_display=row.source.display,
                source_fingerprint=row.source.fingerprint,
            ))
            continue
        existing = records.get(row.student_id)
        previous_file = _safe_state_file(
            options.output_dir,
            existing.get("original_file") if existing else None,
        )
        if existing is None:
            change = "new"
        elif options.force_refresh or existing.get("source_fingerprint") != row.source.fingerprint:
            change = "updated"
        elif previous_file is None or not previous_file.is_file():
            change = "repair"
        else:
            previous_processing = existing.get("processing", {})
            needs_processing = (
                (
                    options.quality_enabled
                    or options.face_detection
                    or options.background_mode != "none"
                    or options.crop_enabled
                )
                and (
                    previous_processing.get("config_fingerprint") != processing_fp
                    or previous_processing.get("status") not in {"success", "warning"}
                    or (
                        options.background_mode != "none"
                        or options.crop_enabled
                    )
                    and not (
                        _safe_state_file(
                            options.output_dir,
                            previous_processing.get("processed_file"),
                        )
                        or Path()
                    ).is_file()
                )
            )
            change = "reprocessed" if needs_processing else "unchanged"
        if change == "unchanged":
            unchanged_results.append(JobResult(
                student_id=row.student_id,
                row_number=row.row_number,
                change="unchanged",
                status="success",
                source_display=row.source.display,
                source_fingerprint=row.source.fingerprint,
                original_file=existing.get("original_file") if existing else None,
                original_sha256=existing.get("original_sha256") if existing else None,
                processed_file=(existing or {}).get("processing", {}).get("processed_file"),
                face_count=(existing or {}).get("processing", {}).get("face_count"),
                quality_status=(existing or {}).get("processing", {}).get("quality_status", "not_requested"),
                quality_reasons=(existing or {}).get("processing", {}).get("quality_reasons", []),
                quality_metrics=(existing or {}).get("processing", {}).get("quality_metrics", {}),
                rotation_ccw=(existing or {}).get("processing", {}).get("rotation_ccw", 0),
                detector=(existing or {}).get("processing", {}).get("detector", "not_used"),
                background_engine=(existing or {}).get("processing", {}).get("background_engine", "none"),
                step_files=(existing or {}).get("processing", {}).get("step_files", []),
                processing_status=(existing or {}).get("processing", {}).get("status", "not_requested"),
                processing_message=(existing or {}).get("processing", {}).get("message", ""),
                executed=False,
            ))
        else:
            jobs.append((row, change, existing))

    total = len(jobs) + len(immediate)
    completed = len(immediate)
    if progress:
        progress(completed, total, f"已检查 {len(report.rows)} 行，需处理 {len(jobs)} 条")
    executed_results: list[JobResult] = list(immediate)
    if jobs:
        with ThreadPoolExecutor(max_workers=options.workers, thread_name_prefix="photo-export") as pool:
            futures = {
                pool.submit(
                    _execute_job_after_resume,
                    run_event,
                    row,
                    change,
                    existing,
                    options,
                    batch_id,
                ): (row, change)
                for row, change, existing in jobs
            }
            for future in as_completed(futures):
                row, change = futures[future]
                try:
                    item = future.result()
                except Exception as exc:
                    item = JobResult(
                        student_id=row.student_id,
                        row_number=row.row_number,
                        change=change,
                        status="failed",
                        message=f"任务异常：{type(exc).__name__}: {exc}",
                        source_display=row.source.display,
                        source_fingerprint=row.source.fingerprint,
                    )
                executed_results.append(item)
                completed += 1
                if progress:
                    label = "成功" if item.status == "success" else "失败"
                    progress(completed, total, f"{item.student_id}：{label}")

    all_results = sorted(executed_results + unchanged_results, key=lambda item: item.row_number)
    row_by_key = {(row.student_id, row.row_number): row for row in report.rows}
    current_time = iso_now()
    for item in all_results:
        if not item.student_id:
            continue
        existing = records.get(item.student_id)
        if item.status == "success" and item.original_file:
            processing = {
                "config_fingerprint": processing_fp,
                "status": item.processing_status,
                "processed_file": item.processed_file,
                "face_count": item.face_count,
                "quality_status": item.quality_status,
                "quality_reasons": item.quality_reasons,
                "quality_metrics": item.quality_metrics,
                "rotation_ccw": item.rotation_ccw,
                "detector": item.detector,
                "background_engine": item.background_engine,
                "step_files": item.step_files,
                "message": item.processing_message,
                "last_processed_at": current_time if item.executed else (existing or {}).get("processing", {}).get("last_processed_at"),
            }
            if not item.executed and existing:
                processing = existing.get("processing", processing)
            records[item.student_id] = {
                "student_id": item.student_id,
                "sheet": options.sheet_name,
                "row": item.row_number,
                "source_fingerprint": item.source_fingerprint,
                "source_display": item.source_display,
                "original_file": item.original_file,
                "original_sha256": item.original_sha256,
                "first_exported_at": (existing or {}).get("first_exported_at", current_time),
                "last_exported_at": current_time if item.executed and item.change != "reprocessed" else (existing or {}).get("last_exported_at", current_time),
                "last_seen_at": current_time,
                "last_batch_id": batch_id if item.executed else (existing or {}).get("last_batch_id"),
                "processing": processing,
            }

    counts = Counter(item.change for item in all_results if item.status == "success")
    failed = sum(1 for item in all_results if item.status == "failed")
    processing_warnings = sum(1 for item in all_results if item.processing_status == "warning")
    quality_rejected = sum(1 for item in all_results if item.quality_status == "rejected")
    current_ids = {row.student_id for row in report.rows if row.student_id}
    missing_current = sum(1 for student_id in records if student_id not in current_ids)
    summary = {
        "total_rows": len(report.rows),
        "new": counts.get("new", 0),
        "updated": counts.get("updated", 0),
        "repair": counts.get("repair", 0),
        "reprocessed": counts.get("reprocessed", 0),
        "unchanged": counts.get("unchanged", 0),
        "failed": failed,
        "processing_warnings": processing_warnings,
        "quality_rejected": quality_rejected,
        "missing_from_current_workbook": missing_current,
    }

    copied: dict[tuple[str, int], dict[str, Any]] = {}
    for item in all_results:
        try:
            copied[(item.student_id, item.row_number)] = _copy_batch_images(options.output_dir, batch_dir, item)
        except OSError as exc:
            item.processing_status = "warning"
            item.processing_message = (item.processing_message + "；" if item.processing_message else "") + f"复制批次合集失败：{exc}"

    _write_manifest(batch_dir, all_results)
    _write_reupload_manifest(batch_dir, all_results)
    batch_payload = {
        "batch_id": batch_id,
        "created_at": current_time,
        "source_workbook": str(options.xlsx_path),
        "sheet": options.sheet_name,
        "header_row": options.header_row,
        "id_column": column_label(options.id_col),
        "image_column": column_label(options.image_col),
        "options": {
            "face_detection": options.face_detection,
            "quality_enabled": options.quality_enabled,
            "auto_orient": options.auto_orient,
            "check_grayscale": options.check_grayscale,
            "check_face": options.check_face,
            "check_glare": options.check_glare,
            "check_recapture": options.check_recapture,
            "grayscale_ratio_threshold": options.grayscale_ratio_threshold,
            "grayscale_delta_limit": options.grayscale_delta_limit,
            "face_confidence_threshold": options.face_confidence_threshold,
            "orientation_min_confidence": options.orientation_min_confidence,
            "orientation_confidence_margin": options.orientation_confidence_margin,
            "glare_ratio_threshold": options.glare_ratio_threshold,
            "glare_luma_threshold": options.glare_luma_threshold,
            "recapture_score_threshold": options.recapture_score_threshold,
            "stop_on_reject": options.stop_on_reject,
            "background_mode": options.background_mode,
            "background_color": options.background_color,
            "hivision_url": options.hivision_url,
            "hivision_timeout": options.hivision_timeout,
            "hivision_height": options.hivision_height,
            "hivision_width": options.hivision_width,
            "hivision_dpi": options.hivision_dpi,
            "hivision_matting_model": options.hivision_matting_model,
            "hivision_face_model": options.hivision_face_model,
            "hivision_hd": options.hivision_hd,
            "hivision_face_align": options.hivision_face_align,
            "hivision_head_measure_ratio": options.hivision_head_measure_ratio,
            "hivision_head_height_ratio": options.hivision_head_height_ratio,
            "hivision_top_distance_max": options.hivision_top_distance_max,
            "hivision_top_distance_min": options.hivision_top_distance_min,
            "hivision_brightness_strength": options.hivision_brightness_strength,
            "hivision_contrast_strength": options.hivision_contrast_strength,
            "hivision_sharpen_strength": options.hivision_sharpen_strength,
            "hivision_saturation_strength": options.hivision_saturation_strength,
            "crop_enabled": options.crop_enabled,
            "crop_width": options.crop_width,
            "crop_height": options.crop_height,
            "force_refresh": options.force_refresh,
        },
        "summary": summary,
        "results": [asdict(item) for item in all_results],
    }
    _atomic_write_json(batch_dir / "batch.json", batch_payload)
    gallery = _write_gallery(batch_dir, batch_id, all_results, copied, summary)
    latest = options.output_dir / "查看最新批次.html"
    relative_gallery = gallery.relative_to(options.output_dir).as_posix()
    latest.write_text(
        '<!doctype html><meta charset="utf-8"><title>最新批次</title>'
        f'<meta http-equiv="refresh" content="0; url={html.escape(relative_gallery, quote=True)}">'
        f'<a href="{html.escape(relative_gallery, quote=True)}">打开最新批次</a>',
        encoding="utf-8",
    )

    source_stat = options.xlsx_path.stat()
    batch_history = {
        "batch_id": batch_id,
        "created_at": current_time,
        "source_workbook": str(options.xlsx_path),
        "source_size": source_stat.st_size,
        "source_modified_at": datetime.fromtimestamp(source_stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "sheet": options.sheet_name,
        "id_column": column_label(options.id_col),
        "image_column": column_label(options.image_col),
        "batch_directory": _relative(options.output_dir, batch_dir),
        "summary": summary,
    }
    state["updated_at"] = current_time
    state["last_batch_id"] = batch_id
    state["last_configuration"] = batch_payload["options"] | {
        "sheet": options.sheet_name,
        "header_row": options.header_row,
        "id_column": column_label(options.id_col),
        "image_column": column_label(options.image_col),
    }
    state["batches"].append(batch_history)
    _atomic_write_json(state_path, state)
    return BatchResult(batch_id, batch_dir, gallery, state_path, summary, all_results)


def suggest_columns(headers: Iterable[str]) -> tuple[int, int]:
    header_list = [str(value).strip() for value in headers]
    id_patterns = [r"^学号$", r"学生.*编号", r"student.*id", r"^id$"]
    image_patterns = [r"照片", r"相片", r"图片", r"头像", r"photo", r"image"]

    def find(patterns: list[str], default: int) -> int:
        for pattern in patterns:
            for index, value in enumerate(header_list):
                if re.search(pattern, value, re.IGNORECASE):
                    return index
        return min(default, max(0, len(header_list) - 1))

    return find(id_patterns, 0), find(image_patterns, 1)
