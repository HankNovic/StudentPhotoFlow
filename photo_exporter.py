from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xlsx_photo_core import (
    APP_VERSION,
    ExportOptions,
    WorkbookError,
    WorkbookReader,
    column_label,
    inspect_selection,
    resolve_column_spec,
    run_export,
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


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Excel 学生照片增量导出工具")
    parser.add_argument("xlsx", nargs="?", help="Excel 文件路径；不加其他参数时会打开图形界面")
    parser.add_argument("--cli", action="store_true", help="使用命令行模式")
    parser.add_argument("--inspect", action="store_true", help="仅检查工作簿，不导出")
    parser.add_argument("--output", help="输出目录")
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
        default="ai",
        help="背景处理：none/quick/ai/hivision，默认 ai",
    )
    parser.add_argument("--background-color", default="#438EDB", help="背景色，例如 #438EDB")
    parser.add_argument("--crop", action="store_true", help="启用最终成片裁切")
    parser.add_argument("--crop-width", type=int, default=295, help="最终图片宽度，默认 295")
    parser.add_argument("--crop-height", type=int, default=413, help="最终图片高度，默认 413")
    parser.add_argument("--hivision-url", default="http://127.0.0.1:8080", help="可选 Hivision API 地址")
    parser.add_argument("--workers", type=int, default=6, help="并发数 1-16，默认 6")
    parser.add_argument("--force-refresh", action="store_true", help="重新下载已存在照片")
    return parser


def _cli_main(args: argparse.Namespace) -> int:
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
        face_detection=args.face_detection,
        quality_enabled=args.quality_check,
        auto_orient=not args.no_auto_orient,
        background_mode=args.background_mode,
        background_color=args.background_color,
        crop_enabled=args.crop,
        crop_width=args.crop_width,
        crop_height=args.crop_height,
        hivision_url=args.hivision_url,
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
        try:
            import tkinter as tk
            from tkinter import colorchooser, filedialog, messagebox, ttk
        except ImportError as exc:
            raise RuntimeError("当前 Python 没有 Tkinter，请安装带 Tcl/Tk 的 Windows Python") from exc

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.colorchooser = colorchooser
        self.root = tk.Tk()
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
        self.background_mode_var = tk.StringVar(value="AI 智能抠图换背景")
        self.color_preset_var = tk.StringVar(value="标准蓝 #438EDB")
        self.custom_color_var = tk.StringVar(value="#438EDB")
        self.hivision_url_var = tk.StringVar(value="http://127.0.0.1:8080")
        self.hivision_timeout_var = tk.IntVar(value=120)
        self.hivision_height_var = tk.IntVar(value=413)
        self.hivision_width_var = tk.IntVar(value=295)
        self.hivision_dpi_var = tk.IntVar(value=300)
        self.crop_enabled_var = tk.BooleanVar(value=False)
        self.crop_width_var = tk.IntVar(value=295)
        self.crop_height_var = tk.IntVar(value=413)
        self.workers_var = tk.IntVar(value=6)
        self.force_var = tk.BooleanVar(value=False)
        self.open_gallery_var = tk.BooleanVar(value=True)
        self.summary_var = tk.StringVar(value="请选择 Excel 文件后点击“检查表格”。")
        self.progress_text_var = tk.StringVar(value="就绪")

        self._build_ui()
        self.root.after(120, self._poll_events)
        if initial_xlsx:
            self.root.after(250, self.inspect_async)

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

        options = ttk.LabelFrame(settings, text="可选图片处理", padding=(10, 7))
        options.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(10, 2))
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
        advanced.grid(row=5, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        ttk.Label(advanced, text="并发数").pack(side="left")
        ttk.Spinbox(advanced, from_=1, to=16, textvariable=self.workers_var, width=5).pack(side="left", padx=(6, 18))
        ttk.Checkbutton(advanced, text="强制重新下载已有照片", variable=self.force_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(advanced, text="完成后打开本次合集", variable=self.open_gallery_var).pack(side="left")

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
        self.export_button = ttk.Button(footer, text="开始增量导出", command=self.export_async)
        self.export_button.grid(row=0, column=4, padx=(12, 0))

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
        from photo_pipeline import PipelineOptions

        return PipelineOptions(
            quality_enabled=bool(self.quality_var.get()),
            auto_orient=bool(self.auto_orient_var.get()),
            check_grayscale=bool(self.grayscale_var.get()),
            check_face=bool(self.face_var.get()),
            check_glare=bool(self.glare_var.get()),
            check_recapture=bool(self.recapture_var.get()),
            grayscale_ratio_threshold=float(self.grayscale_threshold_var.get()),
            grayscale_delta_limit=int(self.grayscale_delta_var.get()),
            face_confidence_threshold=float(self.face_confidence_var.get()),
            orientation_min_confidence=float(self.orientation_min_confidence_var.get()),
            orientation_confidence_margin=float(self.orientation_confidence_margin_var.get()),
            glare_ratio_threshold=float(self.glare_threshold_var.get()),
            glare_luma_threshold=int(self.glare_luma_var.get()),
            recapture_score_threshold=float(self.recapture_threshold_var.get()),
            stop_on_reject=bool(self.stop_on_reject_var.get()),
            background_mode=BACKGROUND_MODES[self.background_mode_var.get()],
            background_color=self._selected_color(),
            hivision_url=self.hivision_url_var.get().strip(),
            hivision_timeout=int(self.hivision_timeout_var.get()),
            hivision_height=int(self.hivision_height_var.get()),
            hivision_width=int(self.hivision_width_var.get()),
            hivision_dpi=int(self.hivision_dpi_var.get()),
            crop_enabled=bool(self.crop_enabled_var.get()),
            crop_width=int(self.crop_width_var.get()),
            crop_height=int(self.crop_height_var.get()),
        )

    def open_quality_settings(self) -> None:
        ttk = self.ttk
        window = self.tk.Toplevel(self.root)
        window.title("内置预检与 Hivision 参数")
        window.transient(self.root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=14)
        body.grid(sticky="nsew")

        steps = ttk.LabelFrame(body, text="参与处理的步骤", padding=10)
        steps.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(steps, text="自动判断并安全修正 90°/180°/270°方向", variable=self.auto_orient_var).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="彩色/黑白检查", variable=self.grayscale_var).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="人脸数量检查", variable=self.face_var).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="反光与过曝检查", variable=self.glare_var).grid(row=3, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="二次拍摄检查", variable=self.recapture_var).grid(row=4, column=0, sticky="w", pady=2)
        ttk.Checkbutton(steps, text="预检不合格时停止后续换背景并列入重传名单", variable=self.stop_on_reject_var).grid(row=5, column=0, sticky="w", pady=(6, 2))

        thresholds = ttk.LabelFrame(body, text="判定阈值（可调参后重新输出）", padding=10)
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

        hivision = ttk.LabelFrame(body, text="Hivision API（仅选择该模式时使用，不打包服务）", padding=10)
        hivision.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(hivision, text="API 地址").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(hivision, textvariable=self.hivision_url_var, width=42).grid(row=0, column=1, columnspan=5, sticky="ew", pady=3)
        ttk.Label(hivision, text="超时秒数").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Spinbox(hivision, from_=5, to=600, textvariable=self.hivision_timeout_var, width=8).grid(row=1, column=1, sticky="w")
        ttk.Label(hivision, text="宽").grid(row=1, column=2, padx=(12, 4))
        ttk.Spinbox(hivision, from_=100, to=3000, textvariable=self.hivision_width_var, width=7).grid(row=1, column=3)
        ttk.Label(hivision, text="高").grid(row=1, column=4, padx=(12, 4))
        ttk.Spinbox(hivision, from_=100, to=4000, textvariable=self.hivision_height_var, width=7).grid(row=1, column=5)
        ttk.Label(hivision, text="DPI").grid(row=1, column=6, padx=(12, 4))
        ttk.Spinbox(hivision, from_=72, to=1200, textvariable=self.hivision_dpi_var, width=7).grid(row=1, column=7)

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky="e", pady=(12, 0))

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

        ttk.Button(buttons, text="恢复建议值", command=restore_defaults).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side="left")

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
        if text:
            self.progress_text_var.set(text)

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
                face_detection=False,
                quality_enabled=bool(self.quality_var.get()),
                auto_orient=bool(self.auto_orient_var.get()),
                check_grayscale=bool(self.grayscale_var.get()),
                check_face=bool(self.face_var.get()),
                check_glare=bool(self.glare_var.get()),
                check_recapture=bool(self.recapture_var.get()),
                grayscale_ratio_threshold=float(self.grayscale_threshold_var.get()),
                grayscale_delta_limit=int(self.grayscale_delta_var.get()),
                face_confidence_threshold=float(self.face_confidence_var.get()),
                orientation_min_confidence=float(self.orientation_min_confidence_var.get()),
                orientation_confidence_margin=float(self.orientation_confidence_margin_var.get()),
                glare_ratio_threshold=float(self.glare_threshold_var.get()),
                glare_luma_threshold=int(self.glare_luma_var.get()),
                recapture_score_threshold=float(self.recapture_threshold_var.get()),
                stop_on_reject=bool(self.stop_on_reject_var.get()),
                background_mode=BACKGROUND_MODES[self.background_mode_var.get()],
                background_color=self._selected_color(),
                hivision_url=self.hivision_url_var.get().strip(),
                hivision_timeout=int(self.hivision_timeout_var.get()),
                hivision_height=int(self.hivision_height_var.get()),
                hivision_width=int(self.hivision_width_var.get()),
                hivision_dpi=int(self.hivision_dpi_var.get()),
                crop_enabled=bool(self.crop_enabled_var.get()),
                crop_width=int(self.crop_width_var.get()),
                crop_height=int(self.crop_height_var.get()),
                workers=int(self.workers_var.get()),
                force_refresh=bool(self.force_var.get()),
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.messagebox.showerror("设置错误", str(exc))
            return

        self._set_busy(True, "准备导出…")
        self.progress.configure(value=0)
        self._append_log(f"开始增量导出到：{options.output_dir}")

        def progress(done: int, total: int, message: str) -> None:
            self.events.put(("progress", (done, total, message)))

        def worker() -> None:
            try:
                result = run_export(options, progress)
                self.events.put(("export_done", result))
            except Exception as exc:
                self.events.put(("error", ("导出失败", str(exc))))

        threading.Thread(target=worker, name="run-export", daemon=True).start()

    def _show_export_done(self, result: Any) -> None:
        summary = result.summary
        message = (
            f"批次 {result.batch_id} 完成：新增 {summary['new']}，更新 {summary['updated']}，"
            f"重新处理 {summary['reprocessed']}，未变化 {summary['unchanged']}，"
            f"需重传 {summary.get('quality_rejected', 0)}，失败 {summary['failed']}。"
        )
        self.progress.configure(value=100)
        self._set_busy(False, "导出完成")
        self._append_log(message)
        if summary.get("processing_warnings"):
            self._append_log(f"其中 {summary['processing_warnings']} 条有图片处理警告，请查看本次合集。")
        if self.open_gallery_var.get():
            try:
                os.startfile(result.gallery_path)  # type: ignore[attr-defined]
            except OSError as exc:
                self._append_log(f"无法自动打开合集：{exc}")
        self.messagebox.showinfo("导出完成", message)

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
                    self.progress_text_var.set(f"{done}/{total} {message}")
                    self._append_log(message)
                elif event == "export_done":
                    self._show_export_done(payload)
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
        path = Path(self.output_var.get()) / "查看最新批次.html"
        if not path.is_file():
            self.messagebox.showwarning("没有批次", "尚未生成批次结果。")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            self.messagebox.showerror("无法打开合集", str(exc))

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.cli or args.inspect:
        return _cli_main(args)
    app = PhotoExporterApp(args.xlsx)
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
