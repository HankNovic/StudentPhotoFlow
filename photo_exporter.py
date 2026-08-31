from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import secrets
import sys
import tempfile
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
import urllib.parse

from photo_pipeline import HIVISION_FACE_MODELS, HIVISION_MATTING_MODELS, PipelineOptions, test_hivision_api
from photo_review import (
    ReviewError, load_roster, mark_review, parse_roster_text, read_roster_file,
    review_queue, save_roster, set_review_enabled, student_detail, undo_review,
    import_delivery_locks, load_delivery_locks, read_delivered_file, unlock_delivery_ids, atomic_json,
)
from review_web import enhance_gallery_html, review_page
from export_reviewed_photos import (DeliveryError, choose_mode, export_delivery, find_previous_file,
                                    resolve_source, roster_overview)
from xlsx_photo_core import (
    APP_VERSION,
    ExportOptions,
    ProcessingOptions,
    WorkbookError,
    WorkbookReader,
    column_label,
    inspect_selection,
    resolve_column_spec,
    run_export,
    run_processing,
    suggest_columns,
)


BACKGROUND_MODES = {
    "不处理": "none",
    "快速纯色背景替换": "quick",
    "AI 智能抠图换背景": "ai",
    "Hivision API（可选）": "hivision",
}

COLOR_PRESETS = {
    "标准蓝 #438EDB": "#438EDB",
    "白色 #FFFFFF": "#FFFFFF",
    "红色 #D9001B": "#D9001B",
}

SETTINGS_FILENAME = "StudentPhotoFlow.settings.json"
SETTINGS_SCHEMA = 1


def _startup_trace(stage: str) -> None:
    trace_path = os.environ.get("STUDENT_PHOTO_FLOW_STARTUP_TRACE", "").strip()
    if not trace_path:
        return
    try:
        with Path(trace_path).open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now().isoformat(timespec='milliseconds')} {stage}\n")
    except OSError:
        pass


def load_portable_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"配置文件无法读取：{exc}") from exc
    if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != SETTINGS_SCHEMA:
        raise ValueError("配置文件版本不受支持")
    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("配置文件内容无效")
    return settings


def save_portable_settings(path: Path, settings: dict[str, Any]) -> None:
    payload = {
        "schema_version": SETTINGS_SCHEMA,
        "app_version": APP_VERSION,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "settings": settings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".settings_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def pipeline_options_from_settings(settings: dict[str, Any]) -> PipelineOptions:
    mode_value = str(settings.get("background_mode", "不处理"))
    background_mode = BACKGROUND_MODES.get(mode_value, mode_value)
    if background_mode not in set(BACKGROUND_MODES.values()):
        background_mode = "none"
    preset = str(settings.get("color_preset", "标准蓝 #438EDB"))
    background_color = COLOR_PRESETS.get(preset, str(settings.get("custom_color", "#438EDB")))
    return PipelineOptions(
        quality_enabled=bool(settings.get("quality_enabled", False)),
        auto_orient=bool(settings.get("auto_orient", True)),
        check_grayscale=bool(settings.get("check_grayscale", True)),
        check_face=bool(settings.get("check_face", True)),
        check_glare=bool(settings.get("check_glare", True)),
        check_recapture=bool(settings.get("check_recapture", True)),
        stop_on_reject=bool(settings.get("stop_on_reject", True)),
        grayscale_ratio_threshold=float(settings.get("grayscale_ratio_threshold", 0.85)),
        grayscale_delta_limit=int(settings.get("grayscale_delta_limit", 10)),
        face_confidence_threshold=float(settings.get("face_confidence_threshold", 0.75)),
        orientation_min_confidence=float(settings.get("orientation_min_confidence", 0.85)),
        orientation_confidence_margin=float(settings.get("orientation_confidence_margin", 0.08)),
        glare_ratio_threshold=float(settings.get("glare_ratio_threshold", 0.08)),
        glare_luma_threshold=int(settings.get("glare_luma_threshold", 245)),
        recapture_score_threshold=float(settings.get("recapture_score_threshold", 0.72)),
        background_mode=background_mode,
        background_color=background_color,
        hivision_url=str(settings.get("hivision_url", "http://127.0.0.1:8080")).strip(),
        hivision_timeout=int(settings.get("hivision_timeout", 120)),
        hivision_height=int(settings.get("hivision_height", 413)),
        hivision_width=int(settings.get("hivision_width", 295)),
        hivision_dpi=int(settings.get("hivision_dpi", 300)),
        hivision_matting_model=str(settings.get("hivision_matting_model", HIVISION_MATTING_MODELS[0])).strip(),
        hivision_face_model=str(settings.get("hivision_face_model", HIVISION_FACE_MODELS[0])).strip(),
        hivision_hd=bool(settings.get("hivision_hd", False)),
        hivision_face_align=bool(settings.get("hivision_face_align", False)),
        hivision_head_measure_ratio=float(settings.get("hivision_head_measure_ratio", 0.20)),
        hivision_head_height_ratio=float(settings.get("hivision_head_height_ratio", 0.45)),
        hivision_top_distance_max=float(settings.get("hivision_top_distance_max", 0.12)),
        hivision_top_distance_min=float(settings.get("hivision_top_distance_min", 0.10)),
        hivision_brightness_strength=float(settings.get("hivision_brightness", 0.0)),
        hivision_contrast_strength=float(settings.get("hivision_contrast", 0.0)),
        hivision_sharpen_strength=float(settings.get("hivision_sharpen", 0.0)),
        hivision_saturation_strength=float(settings.get("hivision_saturation", 0.0)),
        crop_enabled=bool(settings.get("crop_enabled", False)),
        crop_width=int(settings.get("crop_width", 295)),
        crop_height=int(settings.get("crop_height", 413)),
    )


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class GalleryReportServer:
    def __init__(self, on_reprocess: Callable[[Path, list[str]], tuple[bool, str]]):
        self.on_reprocess = on_reprocess
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.reports: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def send_json(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/api/review":
                    query = urllib.parse.parse_qs(parsed.query)
                    token = query.get("report_token", [""])[0]
                    with owner.lock:
                        entry = owner.reports.get(token)
                    if entry is None:
                        self.send_json(403, {"message": "报告令牌无效，请从主程序重新打开报告"})
                        return
                    try:
                        root = Path(entry["output_root"])
                        action = query.get("action", [""])[0]
                        if action == "queue":
                            data = review_queue(root, query.get("filter", ["pending"])[0])
                        elif action == "student":
                            data = student_detail(root, query.get("student_id", [""])[0].strip())
                        elif action == "overview":
                            data = roster_overview(root)
                        else:
                            raise ReviewError("查询操作无效")
                        self.send_json(200, data)
                    except (ReviewError, OSError, DeliveryError) as exc:
                        self.send_json(409, {"message": str(exc)})
                    return
                parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
                if len(parts) < 3 or parts[0] != "reports":
                    self.send_error(404)
                    return
                token = parts[1]
                with owner.lock:
                    entry = owner.reports.get(token)
                if entry is None:
                    self.send_error(404)
                    return
                if parts[2:] == ["review.html"]:
                    self.send_content(review_page().encode("utf-8"), "text/html; charset=utf-8")
                    return
                base = Path(entry["report_path"]).parent.resolve()
                relative = Path(*parts[2:])
                if parts[2] == "output":
                    base = Path(entry["output_root"]).resolve()
                    relative = Path(*parts[3:])
                    if relative.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}:
                        self.send_error(403)
                        return
                candidate = (base / relative).resolve()
                if candidate != base and base not in candidate.parents:
                    self.send_error(403)
                    return
                if not candidate.is_file():
                    self.send_error(404)
                    return
                try:
                    if candidate.suffix.lower() in {".html", ".htm"}:
                        data = enhance_gallery_html(candidate.read_text(encoding="utf-8")).replace(
                            "__STUDENT_PHOTO_FLOW_REPORT_TOKEN__", token,
                        ).encode("utf-8")
                    else:
                        data = candidate.read_bytes()
                except OSError:
                    self.send_error(500)
                    return
                content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                self.send_content(data, content_type)

            def send_content(self, data: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                route = urllib.parse.urlparse(self.path).path
                if route not in {"/api/reprocess", "/api/review"}:
                    self.send_error(404)
                    return
                origin = self.headers.get("Origin")
                if origin and origin != f"http://127.0.0.1:{owner.server.server_address[1]}":
                    self.send_json(403, {"message": "不允许跨站修改本地结果"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length < 2 or length > 1024 * 1024:
                    self.send_json(400, {"message": "请求内容大小无效"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict) or not isinstance(payload.get("student_ids", []), list):
                        raise TypeError("请求必须是对象，学号必须是列表")
                    token = str(payload.get("report_token", ""))
                    student_ids = list(dict.fromkeys(
                        str(value).strip() for value in payload.get("student_ids", []) if str(value).strip()
                    ))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError):
                    self.send_json(400, {"message": "请求格式无效"})
                    return
                with owner.lock:
                    entry = owner.reports.get(token)
                if entry is None:
                    self.send_json(403, {"message": "报告令牌无效，请从主程序重新打开报告"})
                    return
                if route == "/api/review":
                    try:
                        root = Path(entry["output_root"])
                        if payload.get("action") == "mark":
                            data = mark_review(
                                root, str(payload.get("student_id", "")).strip(), str(payload.get("decision", "")),
                                str(payload.get("expected_version", "")), str(payload.get("expected_revision", "")),
                            )
                        elif payload.get("action") == "undo":
                            data = undo_review(root, str(payload.get("action_id", "")))
                        else:
                            raise ReviewError("审核操作无效")
                        self.send_json(200, data)
                    except (ReviewError, OSError) as exc:
                        self.send_json(409, {"message": str(exc)})
                    return
                if not student_ids or len(student_ids) > 5000:
                    self.send_json(400, {"message": "请选择 1–5000 名学生"})
                    return
                allowed_ids = entry["student_ids"]
                if any(student_id not in allowed_ids for student_id in student_ids):
                    self.send_json(400, {"message": "选中内容与当前报告不匹配"})
                    return
                accepted, message = owner.on_reprocess(Path(entry["output_root"]), student_ids)
                self.send_json(202 if accepted else 409, {"message": message})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="gallery-report-server", daemon=True)
        self.thread.start()

    def register(self, report_path: Path) -> str:
        if self.server is None:
            self.start()
        report_path = report_path.resolve()
        batch_payload_path = report_path.parent / "batch.json"
        try:
            batch_payload = json.loads(batch_payload_path.read_text(encoding="utf-8"))
            student_ids = {
                str(item.get("student_id", "")).strip()
                for item in batch_payload.get("results", [])
                if str(item.get("student_id", "")).strip()
            }
        except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise RuntimeError(f"批次报告数据无法读取：{exc}") from exc
        token = secrets.token_urlsafe(24)
        output_root = report_path.parent.parent.parent.resolve()
        with self.lock:
            self.reports[token] = {
                "report_path": report_path,
                "output_root": output_root,
                "student_ids": student_ids,
            }
            if len(self.reports) > 20:
                oldest = next(iter(self.reports))
                self.reports.pop(oldest, None)
        port = self.server.server_address[1]
        return f"http://127.0.0.1:{port}/reports/{token}/{urllib.parse.quote(report_path.name)}"

    def close(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        self.server = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Excel 学生照片增量导出工具")
    parser.add_argument("xlsx", nargs="?", help="Excel 文件路径；不加其他参数时会打开图形界面")
    parser.add_argument("--cli", action="store_true", help="使用命令行模式")
    parser.add_argument("--inspect", action="store_true", help="仅检查工作簿，不导出")
    parser.add_argument("--operation", choices=["export", "process"], default="export", help="export 只导出原图；process 只处理已导出的原图")
    parser.add_argument("--test-hivision-api", action="store_true", help="只测试 Hivision OpenAPI 和 /idphoto 参数兼容性")
    parser.add_argument("--output", help="输出目录")
    parser.add_argument("--review-export", choices=["initial", "incremental", "lists"], help="导出审核结果：初次 / 新增 / 仅名单；--output 为已有源目录")
    parser.add_argument("--delivery-output", help="审核照片交付或名单查询的保存父目录")
    parser.add_argument("--previous-delivery", help="新增审核交付使用的历史累计学号 TXT")
    parser.add_argument("--import-delivered", help="导入已实际交付学号 JSON/TXT/CSV/XLSX 并锁定；必须同时 --confirm-delivered")
    parser.add_argument("--confirm-delivered", action="store_true", help="确认导入名单的照片已经实际发送（非仅导出）")
    parser.add_argument("--unlock-delivered", help="手动解锁名单文件；必须提供 --unlock-reason 和 --confirm-unlock")
    parser.add_argument("--unlock-reason", default="", help="解除交付锁定的原因，写入 JSON 历史")
    parser.add_argument("--confirm-unlock", action="store_true", help="确认手动解除指定学号的交付锁定")
    parser.add_argument("--sheet", help="工作表名称，默认第一张表")
    parser.add_argument("--header-row", type=int, default=1, help="表头行号，默认 1")
    parser.add_argument("--id-column", help="学号列：列字母、列序号或表头名称")
    parser.add_argument("--image-column", help="图片列：列字母、列序号或表头名称")
    parser.add_argument("--face-detection", action="store_true", help="兼容参数：启用内置人脸检查")
    parser.add_argument("--quality-check", action="store_true", help="启用 Pillow/OpenCV/YuNet 内置预检")
    parser.add_argument("--no-auto-orient", action="store_true", help="预检时不自动修正图片方向")
    parser.add_argument(
        "--background-mode",
        choices=["none", "quick", "ai", "hivision"],
        default="none",
        help="背景处理：none/quick/ai/hivision，默认 none",
    )
    parser.add_argument("--background-color", default="#438EDB", help="背景色，例如 #438EDB")
    parser.add_argument("--crop", action="store_true", help="启用最终成片裁切")
    parser.add_argument("--crop-width", type=int, default=295, help="最终图片宽度，默认 295")
    parser.add_argument("--crop-height", type=int, default=413, help="最终图片高度，默认 413")
    parser.add_argument("--hivision-url", default="http://127.0.0.1:8080", help="可选 Hivision API 地址")
    parser.add_argument("--hivision-timeout", type=int, default=120, help="Hivision 请求超时秒数")
    parser.add_argument("--hivision-width", type=int, default=295, help="Hivision 标准照宽度")
    parser.add_argument("--hivision-height", type=int, default=413, help="Hivision 标准照高度")
    parser.add_argument("--hivision-dpi", type=int, default=300, help="Hivision 输出 DPI")
    parser.add_argument("--hivision-matting-model", default=HIVISION_MATTING_MODELS[0], help="Hivision 抠图模型")
    parser.add_argument("--hivision-face-model", default=HIVISION_FACE_MODELS[0], help="Hivision 人脸检测模型")
    parser.add_argument("--hivision-hd", action="store_true", help="请求并使用 Hivision 高清结果")
    parser.add_argument("--hivision-face-align", action="store_true", help="启用 Hivision 人脸对齐")
    parser.add_argument("--hivision-head-measure-ratio", type=float, default=0.20, help="Hivision 面部占比")
    parser.add_argument("--hivision-head-height-ratio", type=float, default=0.45, help="Hivision 面部中心高度")
    parser.add_argument("--hivision-top-distance-max", type=float, default=0.12, help="Hivision 头顶留白最大值")
    parser.add_argument("--hivision-top-distance-min", type=float, default=0.10, help="Hivision 头顶留白最小值")
    parser.add_argument("--hivision-brightness", type=float, default=0.0, help="Hivision 亮度强度")
    parser.add_argument("--hivision-contrast", type=float, default=0.0, help="Hivision 对比度强度")
    parser.add_argument("--hivision-sharpen", type=float, default=0.0, help="Hivision 锐化强度")
    parser.add_argument("--hivision-saturation", type=float, default=0.0, help="Hivision 饱和度强度")
    parser.add_argument("--workers", type=int, default=6, help="并发数 1-16，默认 6")
    parser.add_argument("--force-refresh", action="store_true", help="重新下载已存在照片")
    parser.add_argument("--force-process", action="store_true", help="忽略处理指纹，重新处理全部已导出原图")
    return parser


def _pipeline_options_from_args(args: argparse.Namespace) -> PipelineOptions:
    return PipelineOptions(
        quality_enabled=args.quality_check or args.face_detection,
        auto_orient=not args.no_auto_orient,
        check_grayscale=args.quality_check,
        check_face=args.quality_check or args.face_detection,
        check_glare=args.quality_check,
        check_recapture=args.quality_check,
        background_mode=args.background_mode,
        background_color=args.background_color,
        crop_enabled=args.crop,
        crop_width=args.crop_width,
        crop_height=args.crop_height,
        hivision_url=args.hivision_url,
        hivision_timeout=args.hivision_timeout,
        hivision_width=args.hivision_width,
        hivision_height=args.hivision_height,
        hivision_dpi=args.hivision_dpi,
        hivision_matting_model=args.hivision_matting_model,
        hivision_face_model=args.hivision_face_model,
        hivision_hd=args.hivision_hd,
        hivision_face_align=args.hivision_face_align,
        hivision_head_measure_ratio=args.hivision_head_measure_ratio,
        hivision_head_height_ratio=args.hivision_head_height_ratio,
        hivision_top_distance_max=args.hivision_top_distance_max,
        hivision_top_distance_min=args.hivision_top_distance_min,
        hivision_brightness_strength=args.hivision_brightness,
        hivision_contrast_strength=args.hivision_contrast,
        hivision_sharpen_strength=args.hivision_sharpen,
        hivision_saturation_strength=args.hivision_saturation,
    )


def _cli_main(args: argparse.Namespace) -> int:
    if args.import_delivered or args.unlock_delivered:
        if not args.output or (args.import_delivered and args.unlock_delivered) or args.review_export:
            raise SystemExit("请提供 --output 数据目录；导入锁定、解除锁定、照片交付必须分开执行")
        root = Path(args.output).resolve()
        path = Path(args.import_delivered or args.unlock_delivered)
        ids = read_delivered_file(path)
        if args.import_delivered:
            result = import_delivery_locks(root, ids, confirmed_sent=args.confirm_delivered, source=path.name)
        else:
            if not args.confirm_unlock:
                raise SystemExit("解除锁定必须明确提供 --confirm-unlock")
            result = unlock_delivery_ids(root, ids, reason=args.unlock_reason,
                                         expected_revision=load_delivery_locks(root)["revision"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.review_export:
        if not args.output:
            raise SystemExit("审核交付必须提供 --output 已有导出结果目录")
        result = export_delivery(args.output, args.delivery_output, mode="incremental" if args.review_export == "incremental" else "initial",
            previous_file=args.previous_delivery, lists_only=args.review_export == "lists")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result["errors"] or result["query_warnings"] else 0
    if args.test_hivision_api:
        print(json.dumps(test_hivision_api(args.hivision_url, args.hivision_timeout), ensure_ascii=False, indent=2))
        return 0
    if args.operation == "process":
        if not args.output:
            raise SystemExit("处理已导出原图时必须提供 --output 输出目录")
        result = run_processing(ProcessingOptions(
            output_dir=Path(args.output),
            pipeline=_pipeline_options_from_args(args),
            workers=args.workers,
            force_process=args.force_process,
        ), lambda done, total, message: print(f"[{done}/{total}] {message}", flush=True))
        print(json.dumps({
            "batch_id": result.batch_id,
            "operation": result.operation,
            "gallery": str(result.gallery_path),
            "state": str(result.state_path),
            "summary": result.summary,
        }, ensure_ascii=False, indent=2))
        return 2 if result.summary.get("failed") else 0
    if not args.xlsx:
        raise SystemExit("命令行模式必须提供 Excel 文件路径")
    xlsx = Path(args.xlsx)
    with WorkbookReader(xlsx) as reader:
        sheet_name = args.sheet or reader.sheets[0].name
        headers = reader.headers(sheet_name, args.header_row)
    suggested_id, suggested_image = suggest_columns(headers)
    id_col = resolve_column_spec(args.id_column, headers) if args.id_column else suggested_id
    image_col = resolve_column_spec(args.image_column, headers) if args.image_column else suggested_image
    report = inspect_selection(xlsx, sheet_name, args.header_row, id_col, image_col)
    if args.inspect:
        print(json.dumps({
            "workbook": str(xlsx),
            "sheet": sheet_name,
            "headers": [f"{column_label(index)} - {value}" for index, value in enumerate(headers)],
            "id_column": column_label(id_col),
            "image_column": column_label(image_col),
            "summary": report.summary,
        }, ensure_ascii=False, indent=2))
        return 0
    if not args.output:
        raise SystemExit("导出时必须提供 --output 输出目录")
    options = ExportOptions(
        xlsx_path=xlsx,
        output_dir=Path(args.output),
        sheet_name=sheet_name,
        header_row=args.header_row,
        id_col=id_col,
        image_col=image_col,
        workers=args.workers,
        force_refresh=args.force_refresh,
    )

    def progress(done: int, total: int, message: str) -> None:
        print(f"[{done}/{total}] {message}", flush=True)

    result = run_export(options, progress)
    print(json.dumps({
        "batch_id": result.batch_id,
        "gallery": str(result.gallery_path),
        "state": str(result.state_path),
        "summary": result.summary,
    }, ensure_ascii=False, indent=2))
    return 2 if result.summary.get("failed") else 0


class PhotoExporterApp:
    def __init__(self, initial_xlsx: str | None = None):
        _startup_trace("app_init_enter")
        try:
            import tkinter as tk
            from tkinter import colorchooser, filedialog, messagebox, ttk
        except ImportError as exc:
            raise RuntimeError("当前 Python 没有 Tkinter，请安装带 Tcl/Tk 的 Windows Python") from exc
        _startup_trace("tkinter_imported")

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.colorchooser = colorchooser
        self.root = tk.Tk()
        _startup_trace("tk_root_created")
        self.root.title(f"StudentPhotoFlow｜学生照片导出与处理工具 {APP_VERSION}")
        self.root.geometry("1220x820")
        self.root.minsize(980, 690)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.sheet_names: list[str] = []
        self.header_values: list[str] = []
        self.column_choices: list[str] = []

        script_dir = application_directory()
        self.xlsx_var = tk.StringVar(value=initial_xlsx or "")
        self.output_var = tk.StringVar(value=str(script_dir / "导出结果"))
        self.sheet_var = tk.StringVar()
        self.header_row_var = tk.IntVar(value=1)
        self.id_col_var = tk.StringVar()
        self.image_col_var = tk.StringVar()
        self.quality_var = tk.BooleanVar(value=False)
        self.auto_orient_var = tk.BooleanVar(value=True)
        self.grayscale_var = tk.BooleanVar(value=True)
        self.face_var = tk.BooleanVar(value=True)
        self.glare_var = tk.BooleanVar(value=True)
        self.recapture_var = tk.BooleanVar(value=True)
        self.stop_on_reject_var = tk.BooleanVar(value=True)
        self.grayscale_threshold_var = tk.DoubleVar(value=0.85)
        self.grayscale_delta_var = tk.IntVar(value=10)
        self.face_confidence_var = tk.DoubleVar(value=0.75)
        self.orientation_min_confidence_var = tk.DoubleVar(value=0.85)
        self.orientation_confidence_margin_var = tk.DoubleVar(value=0.08)
        self.glare_threshold_var = tk.DoubleVar(value=0.08)
        self.glare_luma_var = tk.IntVar(value=245)
        self.recapture_threshold_var = tk.DoubleVar(value=0.72)
        self.background_mode_var = tk.StringVar(value="不处理")
        self.color_preset_var = tk.StringVar(value="标准蓝 #438EDB")
        self.custom_color_var = tk.StringVar(value="#438EDB")
        self.hivision_url_var = tk.StringVar(value="http://127.0.0.1:8080")
        self.hivision_timeout_var = tk.IntVar(value=120)
        self.hivision_height_var = tk.IntVar(value=413)
        self.hivision_width_var = tk.IntVar(value=295)
        self.hivision_dpi_var = tk.IntVar(value=300)
        self.hivision_matting_model_var = tk.StringVar(value=HIVISION_MATTING_MODELS[0])
        self.hivision_face_model_var = tk.StringVar(value=HIVISION_FACE_MODELS[0])
        self.hivision_hd_var = tk.BooleanVar(value=False)
        self.hivision_face_align_var = tk.BooleanVar(value=False)
        self.hivision_head_measure_ratio_var = tk.DoubleVar(value=0.20)
        self.hivision_head_height_ratio_var = tk.DoubleVar(value=0.45)
        self.hivision_top_distance_max_var = tk.DoubleVar(value=0.12)
        self.hivision_top_distance_min_var = tk.DoubleVar(value=0.10)
        self.hivision_brightness_var = tk.DoubleVar(value=0.0)
        self.hivision_contrast_var = tk.DoubleVar(value=0.0)
        self.hivision_sharpen_var = tk.DoubleVar(value=0.0)
        self.hivision_saturation_var = tk.DoubleVar(value=0.0)
        self.crop_enabled_var = tk.BooleanVar(value=False)
        self.crop_width_var = tk.IntVar(value=295)
        self.crop_height_var = tk.IntVar(value=413)
        self.workers_var = tk.IntVar(value=6)
        self.force_var = tk.BooleanVar(value=False)
        self.force_process_var = tk.BooleanVar(value=False)
        self.open_gallery_var = tk.BooleanVar(value=True)
        self.hivision_test_status_var = tk.StringVar(value="尚未测试；测试时不会上传学生照片。")
        self.summary_var = tk.StringVar(value="请选择 Excel 文件后点击“检查表格”。")
        self.progress_text_var = tk.StringVar(value="就绪")
        self.export_run_event = threading.Event()
        self.export_run_event.set()
        self.cancel_event = threading.Event()
        self.export_paused = False
        self.current_operation = ""
        self.settings_path = script_dir / SETTINGS_FILENAME
        self.settings_save_job: str | None = None
        self.settings_load_warning = ""
        self.web_action_pending = False
        self.web_action_lock = threading.Lock()
        self.gallery_server = GalleryReportServer(self._queue_web_reprocess)
        _startup_trace("variables_created")

        self._load_saved_settings()
        _startup_trace("settings_loaded")
        self._build_ui()
        _startup_trace("ui_built")
        self._register_settings_autosave()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._poll_events)
        if self.settings_load_warning:
            self._append_log(self.settings_load_warning)
        if initial_xlsx:
            self.root.after(250, self.inspect_async)
        _startup_trace("app_init_complete")

    def _settings_bindings(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_var,
            "sheet_name": self.sheet_var,
            "header_row": self.header_row_var,
            "id_column": self.id_col_var,
            "image_column": self.image_col_var,
            "quality_enabled": self.quality_var,
            "auto_orient": self.auto_orient_var,
            "check_grayscale": self.grayscale_var,
            "check_face": self.face_var,
            "check_glare": self.glare_var,
            "check_recapture": self.recapture_var,
            "stop_on_reject": self.stop_on_reject_var,
            "grayscale_ratio_threshold": self.grayscale_threshold_var,
            "grayscale_delta_limit": self.grayscale_delta_var,
            "face_confidence_threshold": self.face_confidence_var,
            "orientation_min_confidence": self.orientation_min_confidence_var,
            "orientation_confidence_margin": self.orientation_confidence_margin_var,
            "glare_ratio_threshold": self.glare_threshold_var,
            "glare_luma_threshold": self.glare_luma_var,
            "recapture_score_threshold": self.recapture_threshold_var,
            "background_mode": self.background_mode_var,
            "color_preset": self.color_preset_var,
            "custom_color": self.custom_color_var,
            "hivision_url": self.hivision_url_var,
            "hivision_timeout": self.hivision_timeout_var,
            "hivision_height": self.hivision_height_var,
            "hivision_width": self.hivision_width_var,
            "hivision_dpi": self.hivision_dpi_var,
            "hivision_matting_model": self.hivision_matting_model_var,
            "hivision_face_model": self.hivision_face_model_var,
            "hivision_hd": self.hivision_hd_var,
            "hivision_face_align": self.hivision_face_align_var,
            "hivision_head_measure_ratio": self.hivision_head_measure_ratio_var,
            "hivision_head_height_ratio": self.hivision_head_height_ratio_var,
            "hivision_top_distance_max": self.hivision_top_distance_max_var,
            "hivision_top_distance_min": self.hivision_top_distance_min_var,
            "hivision_brightness": self.hivision_brightness_var,
            "hivision_contrast": self.hivision_contrast_var,
            "hivision_sharpen": self.hivision_sharpen_var,
            "hivision_saturation": self.hivision_saturation_var,
            "crop_enabled": self.crop_enabled_var,
            "crop_width": self.crop_width_var,
            "crop_height": self.crop_height_var,
            "workers": self.workers_var,
            "open_gallery": self.open_gallery_var,
        }

    def _load_saved_settings(self) -> None:
        try:
            settings = load_portable_settings(self.settings_path)
            self._apply_settings(settings)
        except Exception as exc:
            self.settings_load_warning = f"配置未载入，已使用安全默认值：{exc}"

    def _apply_settings(self, settings: dict[str, Any]) -> None:
        clean = dict(settings)
        if clean.get("background_mode") not in BACKGROUND_MODES:
            clean.pop("background_mode", None)
        if clean.get("color_preset") not in {*COLOR_PRESETS, ""}:
            clean.pop("color_preset", None)
        for key, variable in self._settings_bindings().items():
            if key in clean:
                variable.set(clean[key])

    def _collect_settings(self) -> dict[str, Any]:
        return {key: variable.get() for key, variable in self._settings_bindings().items()}

    def _save_settings(self, show_message: bool = False) -> bool:
        try:
            save_portable_settings(self.settings_path, self._collect_settings())
        except Exception as exc:
            if show_message:
                self.messagebox.showerror("保存配置失败", str(exc))
            return False
        if show_message:
            self._append_log(f"配置已保存：{self.settings_path.name}")
            self.messagebox.showinfo("配置已保存", "当前处理参数会在下次启动时自动恢复。")
        return True

    def save_settings_now(self) -> None:
        self._save_settings(show_message=True)

    def export_settings_file(self) -> None:
        path = self.filedialog.asksaveasfilename(
            title="导出 StudentPhotoFlow 配置",
            defaultextension=".json",
            initialfile="StudentPhotoFlow配置.json",
            filetypes=[("StudentPhotoFlow 配置", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            save_portable_settings(Path(path), self._collect_settings())
        except Exception as exc:
            self.messagebox.showerror("导出配置失败", str(exc))
            return
        self._append_log(f"配置已导出：{path}")
        self.messagebox.showinfo("配置已导出", f"当前脚本配置已导出到：\n{path}")

    def import_settings_file(self) -> None:
        path = self.filedialog.askopenfilename(
            title="导入 StudentPhotoFlow 配置",
            filetypes=[("StudentPhotoFlow 配置", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            settings = load_portable_settings(Path(path))
            self._apply_settings(settings)
            save_portable_settings(self.settings_path, self._collect_settings())
        except Exception as exc:
            self.messagebox.showerror("导入配置失败", str(exc))
            return
        self._append_log(f"配置已导入并保存：{path}")
        self.messagebox.showinfo("配置已导入", "配置已经应用，并保存为便携版当前配置。")

    def manage_grade_roster(self) -> None:
        if self.busy:
            self.messagebox.showwarning("任务运行中", "请先完成或中断当前任务，再修改全年级名单与归档开关。")
            return
        if not self.output_var.get().strip():
            self.messagebox.showwarning("请选择输出目录", "名单与审核状态保存在当前输出目录。")
            return
        root = Path(self.output_var.get()).resolve()
        tk, ttk = self.tk, self.ttk
        window = tk.Toplevel(self.root)
        window.title("全年级学号名单 · 审核归档")
        window.geometry("780x620")
        window.minsize(680, 540)
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text=f"当前输出目录：{root}", wraplength=720).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="导入 TXT / CSV / XLSX，或粘贴学号（一行一个）。保留前导零。\n"
                  "表格须有唯一的“学号”表头，或只含一列学号；重复、空学号、非法字符将阻止保存。",
                  wraplength=720).grid(row=1, column=0, sticky="w", pady=(10, 8))
        status = tk.StringVar(value="尚未读取名单")
        ttk.Label(frame, textvariable=status, wraplength=720).grid(row=2, column=0, sticky="w", pady=6)
        text_frame = ttk.Frame(frame)
        text_frame.grid(row=3, column=0, sticky="nsew")
        editor = tk.Text(text_frame, height=15, width=70, wrap="none", undo=True)
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=editor.yview)
        editor.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        editor.pack(side="left", fill="both", expand=True)
        confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, variable=confirmed, text="我确认这是全年级完整学号名单（程序无法仅凭已采集照片判断是否齐全）").grid(
            row=4, column=0, sticky="w", pady=(12, 6))
        ttk.Label(frame, text="保存名单后需单独启用。通过即保存归档副本，强制 / 批量重处理也会跳过；\n"
                  "新原图或结果变化后需重审。“撤销”可解除通过状态，归档副本仍保留。",
                  wraplength=720).grid(row=5, column=0, sticky="w", pady=6)
        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, sticky="w", pady=12)
        source = {"name": "手动输入"}

        def update_status() -> None:
            roster = load_roster(root)
            enabled = bool(roster.get("enabled"))
            status.set(f"已校验保存 {roster.get('count', 0)} 人｜审核归档：{'已启用' if enabled else '未启用'}")
            enable_button.configure(state="normal" if roster and not enabled else "disabled")
            disable_button.configure(state="normal" if enabled else "disabled")

        def import_file() -> None:
            path = self.filedialog.askopenfilename(parent=window, title="导入全年级学号名单",
                filetypes=[("名单文件", "*.txt *.csv *.xlsx"), ("所有文件", "*.*")])
            if not path:
                return
            try:
                ids = read_roster_file(Path(path))
                editor.delete("1.0", "end")
                editor.insert("1.0", "\n".join(ids))
                confirmed.set(False)
                source["name"] = Path(path).name
                status.set(f"文件校验通过：{len(ids)} 人；请确认完整名单后保存，当前编辑尚未生效。")
            except Exception as exc:
                self.messagebox.showerror("名单导入失败", str(exc), parent=window)

        def save() -> None:
            try:
                ids = parse_roster_text(editor.get("1.0", "end-1c"))
                if not confirmed.get():
                    raise ReviewError("请先确认这是全年级完整学号名单")
                if not self.messagebox.askyesno("保存全年级名单", f"校验通过：{len(ids)} 个唯一学号。\n"
                        "保存会关闭审核开关，需重新启用；既有审核记录不会删除。\n\n确认保存？", parent=window):
                    return
                save_roster(root, ids, confirmed_complete=True, source=source["name"])
                update_status()
                self._append_log(f"全年级名单已校验保存：{len(ids)} 人，目录 {root}")
            except Exception as exc:
                self.messagebox.showerror("名单未保存", str(exc), parent=window)

        def toggle(enabled: bool) -> None:
            try:
                roster = load_roster(root)
                if enabled and parse_roster_text(editor.get("1.0", "end-1c")) != roster.get("student_ids"):
                    raise ReviewError("编辑区与已保存名单不同，请先校验保存再启用")
                if not enabled and not self.messagebox.askyesno("关闭归档保护？",
                        "关闭后已通过的照片将参与普通和强制处理。既有审核记录及副本不会删除。确认关闭？", parent=window):
                    return
                set_review_enabled(root, enabled)
                update_status()
                self._append_log(f"审核归档{'已启用' if enabled else '已关闭'}：{root}")
            except Exception as exc:
                self.messagebox.showerror("无法更改审核开关", str(exc), parent=window)

        ttk.Button(actions, text="导入名单…", command=import_file).pack(side="left")
        ttk.Button(actions, text="校验并保存名单", command=save).pack(side="left", padx=8)
        enable_button = ttk.Button(actions, text="启用审核归档", command=lambda: toggle(True), state="disabled")
        enable_button.pack(side="left")
        disable_button = ttk.Button(actions, text="关闭归档保护", command=lambda: toggle(False), state="disabled")
        disable_button.pack(side="left", padx=8)
        ttk.Button(actions, text="完成", command=window.destroy).pack(side="left")
        try:
            roster = load_roster(root)
            editor.insert("1.0", "\n".join(roster.get("student_ids", [])))
            update_status()
        except ReviewError as exc:
            status.set(str(exc))

    def manage_delivery_locks(self) -> None:
        if self.busy:
            self.messagebox.showwarning("任务运行中", "请先完成或中断任务，再管理已交付锁定。")
            return
        if not self.output_var.get().strip():
            self.messagebox.showwarning("请选择输出目录", "已交付锁定记录保存在当前导出结果目录。")
            return
        root = Path(self.output_var.get()).resolve()
        try:
            roster = load_roster(root)
            state = load_delivery_locks(root)
            if not roster:
                raise ReviewError("请先校验保存全年级学号名单；不需要重新审核，也不需要开启归档开关。")
        except Exception as exc:
            self.messagebox.showerror("无法管理交付锁定", str(exc))
            return
        tk, ttk = self.tk, self.ttk
        window = tk.Toplevel(self.root)
        window.title("历史已交付名单 · 独立锁定")
        window.geometry("800x630")
        window.minsize(700, 560)
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text=f"当前数据目录：{root}", wraplength=740).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="导入已实际发送的学号 JSON / TXT / CSV / XLSX，或粘贴一行一个学号。\n"
                  "锁定后跳过处理、审核队列和照片交付；强制下载、改参、关闭审核保护均不解除。\n"
                  "这不是把当前照片改为审核通过。只导出、尚未发送的名单请勿锁定。",
                  wraplength=740).grid(row=1, column=0, sticky="w", pady=8)
        status = tk.StringVar()
        ttk.Label(frame, textvariable=status, wraplength=740).grid(row=2, column=0, sticky="w", pady=6)
        editor = tk.Text(frame, wrap="none", height=15)
        editor.grid(row=3, column=0, sticky="nsew")
        confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="我确认输入名单的照片已经实际交付，后续不再重复处理、审核或发送", variable=confirmed).grid(row=4, column=0, sticky="w", pady=10)
        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, sticky="w", pady=5)
        maintenance = ttk.Frame(frame)
        maintenance.grid(row=6, column=0, sticky="w", pady=8)
        source = {"name": "手动输入"}

        def refresh():
            nonlocal state
            state = load_delivery_locks(root)
            in_grade = len(set(state["student_ids"]) & set(load_roster(root)["student_ids"]))
            status.set(f"全年级 {roster['count']} 人｜已交付锁定 {in_grade} 人｜剩余待交付 {roster['count'] - in_grade} 人\n"
                       "输入区尚未保存；记录存于 delivered_state.json，变更前自动备份，解锁保留历史。")

        def import_file():
            path = self.filedialog.askopenfilename(parent=window, title="选择已经交付的学号名单",
                filetypes=[("学号名单", "*.json *.txt *.csv *.xlsx"), ("所有文件", "*.*")])
            if not path:
                return
            try:
                ids = read_delivered_file(Path(path))
                editor.delete("1.0", "end")
                editor.insert("1.0", "\n".join(ids))
                source["name"] = Path(path).name
                confirmed.set(False)
                status.set(f"已读取 {len(ids)} 个学号，尚未锁定。请确认已实际交付后点击“确认导入并锁定”。")
            except Exception as exc:
                self.messagebox.showerror("导入失败", str(exc), parent=window)

        def save():
            try:
                ids = parse_roster_text(editor.get("1.0", "end-1c"))
                if not confirmed.get():
                    raise ReviewError("请勾选确认这些照片已经实际交付")
                if not self.messagebox.askyesno("确认已交付锁定", f"即将按名单锁定 {len(ids)} 人（已有锁定不会重复添加）。\n"
                        "这些学号将跳过处理、审核和照片交付，直到手动解锁。确认？", parent=window):
                    return
                result = import_delivery_locks(root, ids, confirmed_sent=True, source=source["name"])
                refresh()
                confirmed.set(False)
                self._append_log(f"已交付锁定：新增 {result['added']} 人，原已锁定 {result['already_locked']} 人，总计 {result['total']} 人。")
            except Exception as exc:
                self.messagebox.showerror("锁定未保存", str(exc), parent=window)

        def show_locked():
            try:
                refresh()
                editor.delete("1.0", "end")
                editor.insert("1.0", "\n".join(state["student_ids"]))
                confirmed.set(False)
                source["name"] = "当前锁定名单"
            except Exception as exc:
                self.messagebox.showerror("读取失败", str(exc), parent=window)

        def unlock():
            from tkinter import simpledialog
            try:
                ids = parse_roster_text(editor.get("1.0", "end-1c"))
                if not self.messagebox.askyesno("手动解除交付锁定", f"将解除输入区 {len(ids)} 人的交付锁定。\n"
                        "他们会恢复普通处理/审核规则；旧累计交付TXT仍会排除已交付者，不会被自动改写。\n确认解除？", parent=window):
                    return
                reason = simpledialog.askstring("解锁原因", "请输入原因（保存至 JSON 操作历史）：", parent=window)
                if reason is None:
                    return
                result = unlock_delivery_ids(root, ids, reason=reason, expected_revision=state["revision"])
                refresh()
                confirmed.set(False)
                self._append_log(f"手动解除交付锁定 {result['unlocked']} 人，仍锁定 {result['total']} 人。")
            except Exception as exc:
                self.messagebox.showerror("未解除锁定", str(exc), parent=window)

        def export_json():
            try:
                refresh()
                path = self.filedialog.asksaveasfilename(parent=window, title="导出锁定记录 JSON",
                    initialfile="已交付锁定名单.json", defaultextension=".json", filetypes=[("JSON", "*.json")])
                if path:
                    target = Path(path).resolve()
                    if target == root or root in target.parents:
                        raise ReviewError("请导出到数据目录之外，避免覆盖运行记录")
                    atomic_json(target, state)
            except Exception as exc:
                self.messagebox.showerror("导出失败", str(exc), parent=window)

        ttk.Button(actions, text="导入名单…", command=import_file).pack(side="left")
        ttk.Button(actions, text="确认导入并锁定", command=save).pack(side="left", padx=8)
        ttk.Button(actions, text="查看当前锁定名单", command=show_locked).pack(side="left")
        ttk.Button(maintenance, text="导出锁定 JSON…", command=export_json).pack(side="left")
        ttk.Button(maintenance, text="解除输入区学号的锁定…", command=unlock).pack(side="left", padx=8)
        ttk.Button(maintenance, text="完成", command=window.destroy).pack(side="left")
        refresh()

    def _schedule_settings_save(self, *_args: Any) -> None:
        if self.settings_save_job is not None:
            try:
                self.root.after_cancel(self.settings_save_job)
            except Exception:
                pass
        self.settings_save_job = self.root.after(700, self._autosave_settings)

    def _autosave_settings(self) -> None:
        self.settings_save_job = None
        self._save_settings(show_message=False)

    def _register_settings_autosave(self) -> None:
        for variable in self._settings_bindings().values():
            variable.trace_add("write", self._schedule_settings_save)

    def _on_close(self) -> None:
        if self.busy:
            close_note = ("正在导出审核结果。强制关闭会留下未完成的交付目录，不能交付或用作新增依据；"
                          "源照片和审核状态不会修改。" if self.current_operation == "delivery" else
                          "当前批次仍在运行。每名已完成学生都已写入恢复检查点；强制关闭后，"
                          "下次点击同一阶段会跳过已完成项并继续。")
            confirmed = self.messagebox.askyesno(
                "中断当前任务？",
                close_note + "\n\n确定立即关闭吗？",
                icon="warning",
            )
            if not confirmed:
                return
            self._save_settings(show_message=False)
            self.gallery_server.close()
            self.root.destroy()
            os._exit(0)
        self._save_settings(show_message=False)
        self.gallery_server.close()
        self.root.destroy()

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        settings = ttk.Frame(self.root, padding=(14, 12, 14, 8))
        settings.grid(row=0, column=0, sticky="nsew")
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(4, weight=1)

        ttk.Label(settings, text="Excel 文件").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.xlsx_var).grid(row=0, column=1, columnspan=4, sticky="ew", pady=4)
        ttk.Button(settings, text="选择…", command=self.choose_xlsx).grid(row=0, column=5, padx=(8, 0), pady=4)

        ttk.Label(settings, text="输出目录").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.output_var).grid(row=1, column=1, columnspan=4, sticky="ew", pady=4)
        ttk.Button(settings, text="选择…", command=self.choose_output).grid(row=1, column=5, padx=(8, 0), pady=4)

        ttk.Label(settings, text="工作表").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        self.sheet_box = ttk.Combobox(settings, textvariable=self.sheet_var, state="readonly", width=18)
        self.sheet_box.grid(row=2, column=1, sticky="ew", pady=4)
        self.sheet_box.bind("<<ComboboxSelected>>", lambda _event: self.inspect_async())

        ttk.Label(settings, text="表头行").grid(row=2, column=2, sticky="e", padx=(16, 8), pady=4)
        ttk.Spinbox(settings, from_=1, to=9999, textvariable=self.header_row_var, width=8).grid(row=2, column=3, sticky="w", pady=4)
        self.inspect_button = ttk.Button(settings, text="检查表格", command=self.inspect_async)
        self.inspect_button.grid(row=2, column=5, padx=(8, 0), pady=4)

        ttk.Label(settings, text="学号列").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        self.id_box = ttk.Combobox(settings, textvariable=self.id_col_var, state="readonly", width=22)
        self.id_box.grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Label(settings, text="图片列").grid(row=3, column=2, sticky="e", padx=(16, 8), pady=4)
        self.image_box = ttk.Combobox(settings, textvariable=self.image_col_var, state="readonly", width=22)
        self.image_box.grid(row=3, column=3, columnspan=2, sticky="ew", pady=4)

        ttk.Label(
            settings,
            text="流程已分离：第一步导出原图；第二步处理已导出原图。每名完成后自动写入中断恢复检查点。",
            foreground="#475467",
        ).grid(row=4, column=0, columnspan=6, sticky="w", pady=(8, 2))

        options = ttk.LabelFrame(settings, text="第二步：图片处理参数", padding=(10, 7))
        options.grid(row=5, column=0, columnspan=6, sticky="ew", pady=(6, 2))
        ttk.Checkbutton(
            options,
            text="启用内置预检（Pillow + OpenCV + YuNet）",
            variable=self.quality_var,
        ).grid(row=0, column=0, columnspan=3, padx=(0, 12), pady=2, sticky="w")
        ttk.Button(options, text="预检参数…", command=self.open_quality_settings).grid(row=0, column=3, padx=(0, 8), pady=2)
        ttk.Button(options, text="打开可视化处理工作台…", command=self.open_workbench).grid(row=0, column=4, columnspan=3, pady=2, sticky="w")

        ttk.Label(options, text="换背景").grid(row=1, column=0, padx=(0, 6), pady=(7, 2), sticky="w")
        ttk.Combobox(
            options,
            textvariable=self.background_mode_var,
            values=list(BACKGROUND_MODES),
            state="readonly",
            width=20,
        ).grid(row=1, column=1, padx=(0, 18), pady=(7, 2), sticky="w")
        ttk.Label(options, text="背景色").grid(row=1, column=2, padx=(0, 6), pady=(7, 2))
        ttk.Combobox(
            options,
            textvariable=self.color_preset_var,
            values=list(COLOR_PRESETS),
            state="readonly",
            width=18,
        ).grid(row=1, column=3, padx=(0, 6), pady=(7, 2))
        ttk.Button(options, text="自定义…", command=self.choose_color).grid(row=1, column=4, padx=(0, 8), pady=(7, 2))
        ttk.Label(options, textvariable=self.custom_color_var, width=9).grid(row=1, column=5, pady=(7, 2), sticky="w")

        ttk.Checkbutton(
            options,
            text="最终图片裁切",
            variable=self.crop_enabled_var,
        ).grid(row=2, column=0, padx=(0, 6), pady=(7, 2), sticky="w")
        ttk.Label(options, text="宽").grid(row=2, column=1, pady=(7, 2), sticky="e")
        ttk.Spinbox(options, from_=32, to=10000, textvariable=self.crop_width_var, width=7).grid(
            row=2, column=2, padx=(5, 8), pady=(7, 2), sticky="w"
        )
        ttk.Label(options, text="×  高").grid(row=2, column=3, pady=(7, 2), sticky="e")
        ttk.Spinbox(options, from_=32, to=10000, textvariable=self.crop_height_var, width=7).grid(
            row=2, column=4, padx=(5, 8), pady=(7, 2), sticky="w"
        )
        ttk.Label(options, text="像素（默认 295×413）").grid(row=2, column=5, pady=(7, 2), sticky="w")

        advanced = ttk.Frame(settings)
        advanced.grid(row=6, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        ttk.Label(advanced, text="并发数").pack(side="left")
        ttk.Spinbox(advanced, from_=1, to=16, textvariable=self.workers_var, width=5).pack(side="left", padx=(6, 18))
        ttk.Checkbutton(advanced, text="强制重新下载已有照片", variable=self.force_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(advanced, text="强制重新处理全部原图", variable=self.force_process_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(advanced, text="完成后打开本次合集", variable=self.open_gallery_var).pack(side="left")

        config_actions = ttk.Frame(settings)
        config_actions.grid(row=7, column=0, columnspan=6, sticky="e", pady=(5, 0))
        ttk.Button(config_actions, text="保存当前配置", command=self.save_settings_now).pack(side="left")
        ttk.Button(config_actions, text="导出配置…", command=self.export_settings_file).pack(side="left", padx=(8, 0))
        ttk.Button(config_actions, text="导入配置…", command=self.import_settings_file).pack(side="left", padx=(8, 0))
        ttk.Button(config_actions, text="全年级名单 / 审核归档…", command=self.manage_grade_roster).pack(side="left", padx=(8, 0))
        self.delivery_button = ttk.Button(config_actions, text="审核结果导出…", command=self.export_review_delivery)
        self.delivery_button.pack(side="left", padx=(8, 0))
        self.delivery_lock_button = ttk.Button(config_actions, text="已交付锁定…", command=self.manage_delivery_locks)
        self.delivery_lock_button.pack(side="left", padx=(8, 0))

        content = ttk.Panedwindow(self.root, orient="vertical")
        content.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))

        preview_frame = ttk.Frame(content)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=1)
        ttk.Label(preview_frame, textvariable=self.summary_var).grid(row=0, column=0, sticky="ew", pady=(4, 7))
        self.tree = ttk.Treeview(
            preview_frame,
            columns=("row", "student_id", "source", "validity"),
            show="headings",
            height=13,
        )
        self.tree.heading("row", text="行号")
        self.tree.heading("student_id", text="学号")
        self.tree.heading("source", text="图片来源（签名已隐藏）")
        self.tree.heading("validity", text="检查结果")
        self.tree.column("row", width=60, anchor="center", stretch=False)
        self.tree.column("student_id", width=150, anchor="center", stretch=False)
        self.tree.column("source", width=620)
        self.tree.column("validity", width=150, anchor="center", stretch=False)
        scrollbar = ttk.Scrollbar(preview_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        content.add(preview_frame, weight=4)

        log_frame = ttk.Frame(content)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        ttk.Label(log_frame, text="运行记录").grid(row=0, column=0, sticky="w", pady=(7, 5))
        self.log = tk.Text(log_frame, height=7, wrap="word", state="disabled", font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.grid(row=1, column=0, sticky="nsew")
        log_scroll.grid(row=1, column=1, sticky="ns")
        content.add(log_frame, weight=2)

        footer = ttk.Frame(self.root, padding=(14, 5, 14, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(footer, maximum=100, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(footer, textvariable=self.progress_text_var, width=28).grid(row=0, column=1, sticky="w")
        ttk.Button(footer, text="打开输出目录", command=self.open_output).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(footer, text="查看最新批次", command=self.open_latest).grid(row=0, column=3, padx=(8, 0))
        self.pause_button = ttk.Button(footer, text="暂停", command=self.toggle_export_pause, state="disabled")
        self.pause_button.grid(row=0, column=4, padx=(12, 0))
        self.cancel_button = ttk.Button(footer, text="中断任务", command=self.cancel_current_task, state="disabled")
        self.cancel_button.grid(row=0, column=5, padx=(8, 0))
        self.export_button = ttk.Button(footer, text="1. 导出原图", command=self.export_async)
        self.export_button.grid(row=0, column=6, padx=(8, 0))
        self.process_button = ttk.Button(footer, text="2. 处理图片", command=self.process_async)
        self.process_button.grid(row=0, column=7, padx=(8, 0))

    def choose_xlsx(self) -> None:
        path = self.filedialog.askopenfilename(
            title="选择学生信息 Excel",
            filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")],
        )
        if path:
            self.xlsx_var.set(path)
            self.sheet_var.set("")
            self.inspect_async()

    def choose_output(self) -> None:
        path = self.filedialog.askdirectory(title="选择输出目录", initialdir=self.output_var.get())
        if path:
            self.output_var.set(path)

    def choose_color(self) -> None:
        chosen = self.colorchooser.askcolor(color=self.custom_color_var.get(), title="选择证件照背景色")
        if chosen and chosen[1]:
            self.custom_color_var.set(chosen[1].upper())
            self.color_preset_var.set("")

    def _selected_color(self) -> str:
        return COLOR_PRESETS.get(self.color_preset_var.get(), self.custom_color_var.get())

    def _pipeline_options_from_ui(self):
        return pipeline_options_from_settings(self._collect_settings())

    def open_quality_settings(self) -> None:
        ttk = self.ttk
        window = self.tk.Toplevel(self.root)
        window.title("内置预检与 Hivision 参数")
        window.transient(self.root)
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        width = min(920, max(720, screen_width - 160))
        height = min(760, max(580, screen_height - 160))
        window.geometry(f"{width}x{height}")
        window.minsize(min(720, width), min(580, height))
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        body = ttk.Frame(window, padding=14)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(body)
        notebook.grid(row=0, column=0, sticky="nsew")
        precheck_tab = ttk.Frame(notebook, padding=12)
        hivision_tab = ttk.Frame(notebook, padding=12)
        notebook.add(precheck_tab, text="内置预检")
        notebook.add(hivision_tab, text="Hivision API")
        precheck_tab.columnconfigure(0, weight=1)
        hivision_tab.columnconfigure(0, weight=1)

        steps = ttk.LabelFrame(precheck_tab, text="参与处理的步骤", padding=10)
        steps.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(steps, text="自动判断并安全修正 90°/180°/270°方向", variable=self.auto_orient_var).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="彩色/黑白检查", variable=self.grayscale_var).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="人脸数量检查", variable=self.face_var).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="反光与过曝检查", variable=self.glare_var).grid(row=3, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="二次拍摄检查", variable=self.recapture_var).grid(row=4, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="预检不合格时停止后续换背景并列入重传名单", variable=self.stop_on_reject_var).grid(row=5, column=0, sticky="w", pady=(6, 2))

        thresholds = ttk.LabelFrame(precheck_tab, text="判定阈值（可调参后重新输出）", padding=10)
        thresholds.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        rows = [
            ("灰度得分阈值", self.grayscale_threshold_var, 0.0, 1.0, 0.01),
            ("灰度通道差上限", self.grayscale_delta_var, 0, 60, 1),
            ("人脸置信度阈值", self.face_confidence_var, 0.05, 0.99, 0.01),
            ("旋转最低置信度", self.orientation_min_confidence_var, 0.05, 0.99, 0.01),
            ("旋转领先分数", self.orientation_confidence_margin_var, 0.0, 0.5, 0.01),
            ("反光面积阈值", self.glare_threshold_var, 0.0, 1.0, 0.01),
            ("反光亮度阈值", self.glare_luma_var, 1, 255, 1),
            ("二次拍摄得分阈值", self.recapture_threshold_var, 0.0, 1.0, 0.01),
        ]
        for row_index, (label, variable, start, end, increment) in enumerate(rows):
            ttk.Label(thresholds, text=label, width=20).grid(row=row_index, column=0, sticky="w", pady=3)
            ttk.Spinbox(
                thresholds,
                from_=start,
                to=end,
                increment=increment,
                textvariable=variable,
                width=10,
            ).grid(row=row_index, column=1, sticky="w", pady=3)

        connection = ttk.LabelFrame(hivision_tab, text="连接与输出", padding=10)
        connection.grid(row=0, column=0, sticky="ew")
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text="API 地址").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(connection, textvariable=self.hivision_url_var).grid(
            row=0, column=1, columnspan=7, sticky="ew", pady=3
        )
        ttk.Label(connection, text="可填服务根地址或以 /idphoto 结尾的完整地址", foreground="#667085").grid(
            row=1, column=1, columnspan=7, sticky="w", pady=(0, 5)
        )
        self.hivision_test_button = ttk.Button(connection, text="测试 API", command=self.test_hivision_connection)
        self.hivision_test_button.grid(row=2, column=0, sticky="w", pady=(2, 6))
        ttk.Label(connection, textvariable=self.hivision_test_status_var, foreground="#475467", wraplength=650).grid(
            row=2, column=1, columnspan=7, sticky="w", pady=(2, 6)
        )
        ttk.Label(connection, text="超时秒数").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(connection, from_=5, to=3600, textvariable=self.hivision_timeout_var, width=8).grid(row=3, column=1, sticky="w")
        ttk.Label(connection, text="宽").grid(row=3, column=2, padx=(12, 4))
        ttk.Spinbox(connection, from_=32, to=10000, textvariable=self.hivision_width_var, width=8).grid(row=3, column=3)
        ttk.Label(connection, text="高").grid(row=3, column=4, padx=(12, 4))
        ttk.Spinbox(connection, from_=32, to=10000, textvariable=self.hivision_height_var, width=8).grid(row=3, column=5)
        ttk.Label(connection, text="DPI").grid(row=3, column=6, padx=(12, 4))
        ttk.Spinbox(connection, from_=36, to=2400, textvariable=self.hivision_dpi_var, width=8).grid(row=3, column=7)
        ttk.Checkbutton(connection, text="请求并使用高清结果", variable=self.hivision_hd_var).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(7, 2)
        )
        ttk.Checkbutton(connection, text="启用 Hivision 人脸对齐", variable=self.hivision_face_align_var).grid(
            row=4, column=3, columnspan=4, sticky="w", pady=(7, 2)
        )

        models = ttk.LabelFrame(hivision_tab, text="服务端模型", padding=10)
        models.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        models.columnconfigure(1, weight=1)
        ttk.Label(models, text="抠图模型").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Combobox(
            models,
            textvariable=self.hivision_matting_model_var,
            values=HIVISION_MATTING_MODELS,
            state="normal",
        ).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(models, text="人脸模型").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Combobox(
            models,
            textvariable=self.hivision_face_model_var,
            values=HIVISION_FACE_MODELS,
            state="normal",
        ).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(models, text="列表可直接选择，也允许输入服务器支持的自定义模型名。", foreground="#667085").grid(
            row=2, column=1, sticky="w", pady=(2, 0)
        )

        composition = ttk.LabelFrame(hivision_tab, text="构图参数", padding=10)
        composition.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        composition_rows = [
            ("面部占比", self.hivision_head_measure_ratio_var, 0.05, 0.80, 0.01),
            ("面部中心高度", self.hivision_head_height_ratio_var, 0.05, 0.95, 0.01),
            ("头顶留白最大值", self.hivision_top_distance_max_var, 0.0, 0.80, 0.01),
            ("头顶留白最小值", self.hivision_top_distance_min_var, 0.0, 0.80, 0.01),
        ]
        for index, (label, variable, start, end, increment) in enumerate(composition_rows):
            column = (index % 2) * 2
            row = index // 2
            ttk.Label(composition, text=label).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=3)
            ttk.Spinbox(
                composition,
                from_=start,
                to=end,
                increment=increment,
                textvariable=variable,
                width=10,
            ).grid(row=row, column=column + 1, sticky="w", padx=(0, 20), pady=3)

        enhancement = ttk.LabelFrame(hivision_tab, text="图像增强强度", padding=10)
        enhancement.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        enhancement_rows = [
            ("亮度", self.hivision_brightness_var, -5, 25, 1),
            ("对比度", self.hivision_contrast_var, -10, 50, 1),
            ("锐化", self.hivision_sharpen_var, 0, 5, 1),
            ("饱和度", self.hivision_saturation_var, -10, 50, 1),
        ]
        for index, (label, variable, start, end, increment) in enumerate(enhancement_rows):
            column = (index % 3) * 2
            row = index // 3
            ttk.Label(enhancement, text=label).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=3)
            ttk.Spinbox(
                enhancement,
                from_=start,
                to=end,
                increment=increment,
                textvariable=variable,
                width=8,
            ).grid(row=row, column=column + 1, sticky="w", padx=(0, 20), pady=3)

        buttons = ttk.Frame(body)
        buttons.grid(row=1, column=0, sticky="e", pady=(12, 0))

        def restore_defaults() -> None:
            self.auto_orient_var.set(True)
            self.grayscale_var.set(True)
            self.face_var.set(True)
            self.glare_var.set(True)
            self.recapture_var.set(True)
            self.stop_on_reject_var.set(True)
            self.grayscale_threshold_var.set(0.85)
            self.grayscale_delta_var.set(10)
            self.face_confidence_var.set(0.75)
            self.orientation_min_confidence_var.set(0.85)
            self.orientation_confidence_margin_var.set(0.08)
            self.glare_threshold_var.set(0.08)
            self.glare_luma_var.set(245)
            self.recapture_threshold_var.set(0.72)

        def restore_hivision_defaults() -> None:
            self.hivision_timeout_var.set(120)
            self.hivision_width_var.set(295)
            self.hivision_height_var.set(413)
            self.hivision_dpi_var.set(300)
            self.hivision_matting_model_var.set(HIVISION_MATTING_MODELS[0])
            self.hivision_face_model_var.set(HIVISION_FACE_MODELS[0])
            self.hivision_hd_var.set(False)
            self.hivision_face_align_var.set(False)
            self.hivision_head_measure_ratio_var.set(0.20)
            self.hivision_head_height_ratio_var.set(0.45)
            self.hivision_top_distance_max_var.set(0.12)
            self.hivision_top_distance_min_var.set(0.10)
            self.hivision_brightness_var.set(0.0)
            self.hivision_contrast_var.set(0.0)
            self.hivision_sharpen_var.set(0.0)
            self.hivision_saturation_var.set(0.0)

        ttk.Button(buttons, text="恢复预检建议值", command=restore_defaults).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="恢复 Hivision 默认值", command=restore_hivision_defaults).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side="left")

    def test_hivision_connection(self) -> None:
        try:
            url = self.hivision_url_var.get().strip()
            timeout = int(self.hivision_timeout_var.get())
        except (TypeError, ValueError) as exc:
            self.messagebox.showerror("API 设置错误", str(exc))
            return
        self.hivision_test_status_var.set("正在读取 OpenAPI 并检查 /idphoto 参数…")
        try:
            self.hivision_test_button.configure(state="disabled")
        except Exception:
            pass

        def worker() -> None:
            try:
                result = test_hivision_api(url, timeout)
                self.events.put(("api_test_done", result))
            except Exception as exc:
                self.events.put(("api_test_error", str(exc)))

        threading.Thread(target=worker, name="test-hivision-api", daemon=True).start()

    def open_workbench(self) -> None:
        try:
            from photo_workbench import PhotoProcessingWorkbench

            PhotoProcessingWorkbench(
                self.root,
                self._pipeline_options_from_ui(),
                Path(self.output_var.get().strip() or application_directory() / "导出结果"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.messagebox.showerror("参数错误", str(exc))
        except Exception as exc:
            self.messagebox.showerror("无法打开处理工作台", str(exc))

    def _column_choice_to_index(self, choice: str) -> int:
        if " - " in choice:
            return resolve_column_spec(choice.split(" - ", 1)[0], self.header_values)
        return resolve_column_spec(choice, self.header_values)

    def _set_busy(self, busy: bool, text: str | None = None) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.inspect_button.configure(state=state)
        self.export_button.configure(state=state)
        self.process_button.configure(state=state)
        self.delivery_button.configure(state=state)
        self.delivery_lock_button.configure(state=state)
        can_interrupt = busy and self.current_operation in {"export", "process"}
        self.cancel_button.configure(state="normal" if can_interrupt else "disabled")
        if not busy:
            self.export_paused = False
            self.export_run_event.set()
            self.cancel_event.clear()
            self.pause_button.configure(text="暂停", state="disabled")
        if text:
            self.progress_text_var.set(text)

    def toggle_export_pause(self) -> None:
        if not self.busy:
            return
        if self.export_paused:
            self.export_paused = False
            self.export_run_event.set()
            self.pause_button.configure(text="暂停")
            self.progress_text_var.set("正在继续处理…")
            self._append_log("已继续：等待中的学生任务开始执行。")
        else:
            self.export_paused = True
            self.export_run_event.clear()
            self.pause_button.configure(text="继续")
            self.progress_text_var.set("已暂停；正在处理的任务会安全收尾")
            self._append_log("已暂停：不再启动新任务；正在执行的任务会安全完成。")

    def cancel_current_task(self) -> None:
        if not self.busy or self.current_operation not in {"export", "process"}:
            return
        confirmed = self.messagebox.askyesno(
            "中断当前任务？",
            "将停止启动新的学生任务；当前正在执行的少量任务会安全收尾。\n\n"
            "已完成项会保留，未完成项写入恢复清单，下次用同样配置运行会继续。",
            icon="warning",
        )
        if not confirmed:
            return
        self.cancel_event.set()
        self.export_run_event.set()
        self.export_paused = False
        self.pause_button.configure(text="暂停", state="disabled")
        self.cancel_button.configure(state="disabled")
        self.progress_text_var.set("正在安全中断；等待已启动任务收尾…")
        self._append_log("已请求中断：正在保存已完成结果和未完成恢复清单。")

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{timestamp}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def inspect_async(self) -> None:
        if self.busy:
            return
        path = self.xlsx_var.get().strip()
        if not path:
            self.messagebox.showwarning("缺少文件", "请先选择 Excel 文件。")
            return
        try:
            header_row = int(self.header_row_var.get())
        except (TypeError, ValueError):
            self.messagebox.showerror("设置错误", "表头行必须是整数。")
            return
        selected_sheet = self.sheet_var.get().strip()
        selected_id = self.id_col_var.get().strip()
        selected_image = self.image_col_var.get().strip()
        self.current_operation = "inspect"
        self._set_busy(True, "正在检查表格…")
        self.progress.configure(value=0)
        self._append_log(f"检查工作簿：{path}")

        def worker() -> None:
            try:
                with WorkbookReader(path) as reader:
                    sheet_names = [sheet.name for sheet in reader.sheets]
                    sheet = selected_sheet if selected_sheet in sheet_names else sheet_names[0]
                    headers = reader.headers(sheet, header_row)
                id_col, image_col = suggest_columns(headers)
                try:
                    if selected_id and selected_sheet == sheet:
                        id_col = resolve_column_spec(selected_id.split(" - ", 1)[0], headers)
                    if selected_image and selected_sheet == sheet:
                        image_col = resolve_column_spec(selected_image.split(" - ", 1)[0], headers)
                except ValueError:
                    pass
                report = inspect_selection(path, sheet, header_row, id_col, image_col)
                self.events.put(("inspection", (sheet_names, headers, id_col, image_col, report)))
            except Exception as exc:
                self.events.put(("error", ("检查失败", str(exc))))

        threading.Thread(target=worker, name="inspect-workbook", daemon=True).start()

    def _show_inspection(self, payload: Any) -> None:
        sheet_names, headers, id_col, image_col, report = payload
        self.sheet_names = sheet_names
        self.header_values = headers
        self.column_choices = [
            f"{column_label(index)} - {header or '(空表头)'}"
            for index, header in enumerate(headers)
        ]
        self.sheet_box.configure(values=sheet_names)
        self.sheet_var.set(report.sheet)
        self.id_box.configure(values=self.column_choices)
        self.image_box.configure(values=self.column_choices)
        self.id_col_var.set(self.column_choices[id_col])
        self.image_col_var.set(self.column_choices[image_col])
        summary = report.summary
        parts = [
            f"数据 {summary['data_rows']} 行",
            f"有效学号 {summary['valid_ids']} 个",
            f"空图片 {summary['missing_sources']} 个",
            f"重复学号 {summary['duplicate_count']} 个",
        ]
        if summary["expired_links"]:
            parts.append(f"已过期链接 {summary['expired_links']} 个")
        elif summary["expiring_links"]:
            parts.append(f"即将过期链接 {summary['expiring_links']} 个")
        self.summary_var.set(" · ".join(parts))
        for item in self.tree.get_children():
            self.tree.delete(item)
        duplicates = set(summary["duplicate_ids"])
        current = datetime.now(timezone.utc)
        for row in report.rows[:1000]:
            if not row.student_id:
                validity = "学号为空"
            elif row.student_id in duplicates:
                validity = "学号重复"
            elif row.source.kind == "missing":
                validity = "图片为空"
            elif row.source.kind == "unsupported":
                validity = "不支持的来源"
            elif row.source.expires_at:
                try:
                    validity = "链接已过期" if datetime.fromisoformat(row.source.expires_at) <= current else "链接有效"
                except ValueError:
                    validity = "待下载验证"
            else:
                validity = "可处理"
            self.tree.insert("", "end", values=(row.row_number, row.student_id, row.source.display, validity))
        self.progress.configure(value=100)
        self._set_busy(False, "检查完成")
        self._append_log(self.summary_var.get())
        if summary["expired_links"]:
            self._append_log("提示：签名照片链接已过期，请从采集系统重新导出最新 Excel 后立即运行。")

    def export_async(self) -> None:
        if self.busy:
            return
        try:
            path = Path(self.xlsx_var.get().strip())
            output = Path(self.output_var.get().strip())
            if not path.is_file():
                raise ValueError("Excel 文件不存在")
            if not self.header_values:
                raise ValueError("请先点击“检查表格”")
            id_col = self._column_choice_to_index(self.id_col_var.get())
            image_col = self._column_choice_to_index(self.image_col_var.get())
            if id_col == image_col:
                raise ValueError("学号列和图片列不能相同")
            options = ExportOptions(
                xlsx_path=path,
                output_dir=output,
                sheet_name=self.sheet_var.get(),
                header_row=int(self.header_row_var.get()),
                id_col=id_col,
                image_col=image_col,
                workers=int(self.workers_var.get()),
                force_refresh=bool(self.force_var.get()),
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.messagebox.showerror("设置错误", str(exc))
            return

        self.current_operation = "export"
        self.cancel_event.clear()
        self._set_busy(True, "准备导出原图…")
        self.export_paused = False
        self.export_run_event.set()
        self.pause_button.configure(text="暂停", state="normal")
        self.progress.configure(value=0)
        self._append_log(f"第一步：只增量导出原图到 {options.output_dir}")

        def progress(done: int, total: int, message: str) -> None:
            self.events.put(("progress", (done, total, message)))

        def worker() -> None:
            try:
                result = run_export(options, progress, self.export_run_event, self.cancel_event)
                self.events.put(("export_done", result))
            except Exception as exc:
                self.events.put(("error", ("导出失败", str(exc))))

        threading.Thread(target=worker, name="run-export", daemon=True).start()

    def process_async(self) -> None:
        if self.busy:
            return
        try:
            output = Path(self.output_var.get().strip())
            pipeline = self._pipeline_options_from_ui()
            options = ProcessingOptions(
                output_dir=output,
                pipeline=pipeline,
                workers=int(self.workers_var.get()),
                force_process=bool(self.force_process_var.get()),
                save_intermediate_steps=True,
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.messagebox.showerror("处理设置错误", str(exc))
            return

        self.current_operation = "process"
        self.cancel_event.clear()
        self._set_busy(True, "准备处理已导出原图…")
        self.export_paused = False
        self.export_run_event.set()
        self.pause_button.configure(text="暂停", state="normal")
        self.progress.configure(value=0)
        self._append_log(f"第二步：从 {options.output_dir / '原始图片'} 读取并处理图片")

        def progress(done: int, total: int, message: str) -> None:
            self.events.put(("progress", (done, total, message)))

        def worker() -> None:
            try:
                result = run_processing(options, progress, self.export_run_event, self.cancel_event)
                self.events.put(("export_done", result))
            except Exception as exc:
                self.events.put(("error", ("图片处理失败", str(exc))))

        threading.Thread(target=worker, name="run-processing", daemon=True).start()

    def _queue_web_reprocess(self, output_root: Path, student_ids: list[str]) -> tuple[bool, str]:
        with self.web_action_lock:
            if self.busy or self.web_action_pending:
                return False, "主程序当前有任务，请完成或中断后再提交"
            self.web_action_pending = True
        self.events.put(("web_reprocess", (output_root, student_ids)))
        return True, f"已提交 {len(student_ids)} 名学生；请回到主程序查看进度"

    def _start_selected_processing(self, payload: Any) -> None:
        output_root, student_ids = payload
        with self.web_action_lock:
            self.web_action_pending = False
        if self.busy:
            self._append_log("网页批量重处理未启动：主程序已有任务。")
            return
        try:
            settings = load_portable_settings(self.settings_path)
            pipeline = pipeline_options_from_settings(settings)
            if not (pipeline.quality_enabled or pipeline.background_mode != "none" or pipeline.crop_enabled):
                raise ValueError("当前保存配置未启用预检、换背景或最终裁切，无法重新处理")
            options = ProcessingOptions(
                output_dir=Path(output_root),
                pipeline=pipeline,
                workers=max(1, min(16, int(settings.get("workers", 6)))),
                force_process=True,
                save_intermediate_steps=True,
                selected_student_ids=list(student_ids),
            )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            self._append_log(f"网页批量重处理未启动：{exc}")
            self.messagebox.showerror("无法批量重处理", str(exc))
            return

        self.current_operation = "process"
        self.cancel_event.clear()
        self._set_busy(True, f"准备重新处理选中的 {len(student_ids)} 名学生…")
        self.export_paused = False
        self.export_run_event.set()
        self.pause_button.configure(text="暂停", state="normal")
        self.progress.configure(value=0)
        self._append_log(
            f"网页批量操作：按 {self.settings_path.name} 的已保存配置重新处理 {len(student_ids)} 名学生"
        )

        def progress(done: int, total: int, message: str) -> None:
            self.events.put(("progress", (done, total, message)))

        def worker() -> None:
            try:
                result = run_processing(options, progress, self.export_run_event, self.cancel_event)
                self.events.put(("export_done", result))
            except Exception as exc:
                self.events.put(("error", ("批量重新处理失败", str(exc))))

        threading.Thread(target=worker, name="run-selected-processing", daemon=True).start()

    def _open_gallery_path(self, path: Path) -> None:
        try:
            url = self.gallery_server.register(path)
            os.startfile(url)  # type: ignore[attr-defined]
        except (OSError, RuntimeError) as exc:
            self._append_log(f"本地批量操作服务不可用，已用只读方式打开报告：{exc}")
            os.startfile(path)  # type: ignore[attr-defined]

    def _show_export_done(self, result: Any) -> None:
        summary = result.summary
        if getattr(result, "cancelled", False):
            title = "任务已安全中断"
            message = (
                f"批次 {result.batch_id} 已中断：已完成 {len([item for item in result.results if item.executed])}，"
                f"待继续 {summary.get('pending', 0)}。已完成结果和恢复清单均已保存。"
            )
        elif getattr(result, "operation", "export") == "process":
            title = "图片处理完成"
            message = (
                f"处理批次 {result.batch_id} 完成：本次处理 {summary['reprocessed']}，"
                f"参数未变 {summary['unchanged']}，需重传 {summary.get('quality_rejected', 0)}，"
                f"归档跳过 {summary.get('archived_skipped', 0)}，"
                f"已交付跳过 {summary.get('delivered_skipped', 0)}，"
                f"警告 {summary.get('processing_warnings', 0)}，失败 {summary['failed']}。"
            )
        else:
            title = "原图导出完成"
            message = (
                f"导出批次 {result.batch_id} 完成：新增 {summary['new']}，更新 {summary['updated']}，"
                f"修复 {summary['repair']}，未变化 {summary['unchanged']}，失败 {summary['failed']}。"
            )
        if not getattr(result, "cancelled", False):
            self.progress.configure(value=100)
        self._set_busy(False, title)
        self._append_log(message)
        if summary.get("processing_warnings"):
            self._append_log(f"其中 {summary['processing_warnings']} 条有图片处理警告，请查看本次合集。")
        if self.open_gallery_var.get():
            try:
                self._open_gallery_path(result.gallery_path)
            except OSError as exc:
                self._append_log(f"无法自动打开合集：{exc}")
        self.messagebox.showinfo(title, message)

    def export_review_delivery(self) -> None:
        """Portable users run the same exporter without a separate Python install."""
        if self.busy:
            return
        try:
            source = resolve_source(self.output_var.get().strip())
            choice = choose_mode(self.root)
            if choice is None:
                return
            destination = self.filedialog.askdirectory(parent=self.root, title="选择交付父目录：新增时选择上次的目录",
                initialdir=str(source.parent))
            if not destination:
                return
            previous = None
            if choice == "incremental":
                try:
                    previous = find_previous_file(Path(destination), allow_missing=bool(load_delivery_locks(source)["student_ids"]))
                except DeliveryError as exc:
                    self.messagebox.showinfo("选择历史累计学号", str(exc), parent=self.root)
                    previous = self.filedialog.askopenfilename(parent=self.root, title="选择审核通过学号 TXT",
                        filetypes=[("累计学号名单", "*.txt")])
                    if not previous:
                        return
        except (DeliveryError, ReviewError, OSError) as exc:
            self.messagebox.showerror("无法导出审核结果", str(exc), parent=self.root)
            return
        self.current_operation = "delivery"
        self._set_busy(True, "正在导出审核结果 / 综合名单…")
        self.progress.configure(value=0)
        self._append_log("审核结果交付：只读取源数据，不重新处理照片、不修改审核。")

        def worker():
            try:
                result = export_delivery(source, destination,
                    progress=lambda message: self.events.put(("delivery_progress", message)),
                    mode="incremental" if choice == "incremental" else "initial",
                    previous_file=previous, lists_only=choice == "lists")
                self.events.put(("delivery_done", result))
            except Exception as exc:
                self.events.put(("error", ("审核结果导出失败", str(exc))))
        threading.Thread(target=worker, name="review-delivery", daemon=True).start()

    def _show_delivery_done(self, result: dict) -> None:
        self._set_busy(False, "审核结果导出完成")
        self.progress.configure(value=100)
        message = (f"全年级 {result['roster_total']} 人；当前有效通过 {result['approved']} 人；"
                   f"已交付锁定 {result.get('delivered_locked', 0)} 人；"
                   f"剩余未通过 {result['not_approved']} 人。\n"
                   f"已处理但未通过 {result['processed_not_approved']} 人；本次导出照片 {result['exported']} 张。\n"
                   f"照片导出异常 {result['errors']} 项；名单查询提示 {result['query_warnings']} 项。\n\n"
                   + ("仅查询不生成累计交付名单，不影响下次新增。\n" if result['lists_only'] else
                      f"累计名单：{result['cumulative_list']}\n") + f"结果目录：{result['output_dir']}")
        self._append_log(message)
        if result["errors"] or result["query_warnings"]:
            message += "\n请检查导出异常.csv 和名单查询提示.csv。"
        if self.messagebox.askyesno("审核结果导出完成", message + "\n\n打开结果文件夹？",
                                   icon="warning" if result["errors"] or result["query_warnings"] else "info"):
            try:
                os.startfile(result["output_dir"])  # type: ignore[attr-defined]
            except OSError as exc:
                self.messagebox.showerror("无法打开结果目录", str(exc))

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "inspection":
                    self._show_inspection(payload)
                elif event == "progress":
                    done, total, message = payload
                    percent = 100 if total == 0 else min(100, done * 100 / total)
                    self.progress.configure(value=percent)
                    if self.cancel_event.is_set():
                        self.progress_text_var.set(f"正在安全中断｜{done}/{total} 已完成")
                    elif self.export_paused:
                        self.progress_text_var.set(f"已暂停｜{done}/{total} 已启动任务收尾中")
                    else:
                        self.progress_text_var.set(f"{done}/{total} {message}")
                    self._append_log(message)
                elif event == "export_done":
                    self._show_export_done(payload)
                elif event == "delivery_progress":
                    self.progress_text_var.set(payload)
                    self._append_log(payload)
                elif event == "delivery_done":
                    self._show_delivery_done(payload)
                elif event == "web_reprocess":
                    self._start_selected_processing(payload)
                elif event == "api_test_done":
                    self.hivision_test_status_var.set(payload["message"])
                    try:
                        self.hivision_test_button.configure(state="normal")
                    except Exception:
                        pass
                    self._append_log(f"Hivision API 测试：{payload['message']}")
                    title = "API 兼容" if payload.get("ok") else "API 参数不兼容"
                    details = (
                        f"{payload['message']}\n\n"
                        f"接口：{payload['endpoint']}\n"
                        f"服务：{payload['service_title']} {payload['service_version']}\n"
                        "本次测试只读取 OpenAPI，没有上传学生照片。"
                    )
                    if payload.get("ok"):
                        self.messagebox.showinfo(title, details)
                    else:
                        self.messagebox.showwarning(title, details)
                elif event == "api_test_error":
                    self.hivision_test_status_var.set(f"测试失败：{payload}")
                    try:
                        self.hivision_test_button.configure(state="normal")
                    except Exception:
                        pass
                    self._append_log(f"Hivision API 测试失败：{payload}")
                    self.messagebox.showerror("API 测试失败", payload)
                elif event == "error":
                    title, message = payload
                    self._set_busy(False, "发生错误")
                    self._append_log(f"{title}：{message}")
                    self.messagebox.showerror(title, message)
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._poll_events)

    def open_output(self) -> None:
        try:
            path = Path(self.output_var.get())
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            self.messagebox.showerror("无法打开目录", str(exc))

    def open_latest(self) -> None:
        batch_root = Path(self.output_var.get()) / "批次记录"
        candidates = list(batch_root.glob("*/index.html")) if batch_root.is_dir() else []
        if not candidates:
            self.messagebox.showwarning("没有批次", "尚未生成批次结果。")
            return
        path = max(candidates, key=lambda item: item.parent.name)
        try:
            self._open_gallery_path(path)
        except OSError as exc:
            self.messagebox.showerror("无法打开合集", str(exc))

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    _startup_trace("main_enter")
    parser = build_parser()
    args = parser.parse_args()
    if args.cli or args.inspect or args.review_export or args.import_delivered or args.unlock_delivered:
        return _cli_main(args)
    app = PhotoExporterApp(args.xlsx)
    _startup_trace("mainloop_enter")
    app.run()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        detail = traceback.format_exc()
        try:
            log_path = application_directory() / "启动错误.log"
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {message}\n{detail}\n")
        except OSError:
            pass
        try:
            from tkinter import messagebox
            messagebox.showerror("工具启动失败", f"{message}\n\n详细信息已写入“启动错误.log”。")
        except Exception:
            pass
        print(f"错误：{message}", file=sys.stderr)
        raise SystemExit(1)
