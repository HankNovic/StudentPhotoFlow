from __future__ import annotations

import io
import json
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from photo_pipeline import (
    HIVISION_FACE_MODELS,
    HIVISION_MATTING_MODELS,
    PipelineOptions,
    PipelineResult,
    run_pipeline,
)


BACKGROUND_LABELS = {
    "不处理": "none",
    "快速纯色背景替换": "quick",
    "AI 智能抠图换背景": "ai",
    "Hivision API（可选）": "hivision",
}
BACKGROUND_VALUES = {value: key for key, value in BACKGROUND_LABELS.items()}


class PhotoProcessingWorkbench:
    def __init__(self, parent, initial: PipelineOptions, output_dir: Path):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.output_dir = Path(output_dir)
        self.window = tk.Toplevel(parent)
        self.window.title("StudentPhotoFlow｜可视化处理工作台")
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        window_width = min(1280, max(960, screen_width - 80))
        window_height = min(820, max(620, screen_height - 100))
        window_x = max(0, (screen_width - window_width) // 2)
        window_y = max(0, (screen_height - window_height) // 2)
        self.window.geometry(f"{window_width}x{window_height}+{window_x}+{window_y}")
        self.window.minsize(min(960, window_width), min(620, window_height))
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        self.input_var = tk.StringVar()
        self.quality_var = tk.BooleanVar(value=initial.quality_enabled)
        self.auto_orient_var = tk.BooleanVar(value=initial.auto_orient)
        self.grayscale_var = tk.BooleanVar(value=initial.check_grayscale)
        self.face_var = tk.BooleanVar(value=initial.check_face)
        self.glare_var = tk.BooleanVar(value=initial.check_glare)
        self.recapture_var = tk.BooleanVar(value=initial.check_recapture)
        self.stop_on_reject_var = tk.BooleanVar(value=initial.stop_on_reject)
        self.gray_threshold_var = tk.DoubleVar(value=initial.grayscale_ratio_threshold)
        self.gray_delta_var = tk.IntVar(value=initial.grayscale_delta_limit)
        self.face_confidence_var = tk.DoubleVar(value=initial.face_confidence_threshold)
        self.orientation_min_confidence_var = tk.DoubleVar(value=initial.orientation_min_confidence)
        self.orientation_confidence_margin_var = tk.DoubleVar(value=initial.orientation_confidence_margin)
        self.glare_threshold_var = tk.DoubleVar(value=initial.glare_ratio_threshold)
        self.glare_luma_var = tk.IntVar(value=initial.glare_luma_threshold)
        self.recapture_threshold_var = tk.DoubleVar(value=initial.recapture_score_threshold)
        self.background_var = tk.StringVar(value=BACKGROUND_VALUES.get(initial.background_mode, "不处理"))
        self.color_var = tk.StringVar(value=initial.background_color)
        self.hivision_url_var = tk.StringVar(value=initial.hivision_url)
        self.hivision_timeout_var = tk.IntVar(value=initial.hivision_timeout)
        self.hivision_width_var = tk.IntVar(value=initial.hivision_width)
        self.hivision_height_var = tk.IntVar(value=initial.hivision_height)
        self.hivision_dpi_var = tk.IntVar(value=initial.hivision_dpi)
        self.hivision_matting_model_var = tk.StringVar(value=initial.hivision_matting_model)
        self.hivision_face_model_var = tk.StringVar(value=initial.hivision_face_model)
        self.hivision_hd_var = tk.BooleanVar(value=initial.hivision_hd)
        self.hivision_face_align_var = tk.BooleanVar(value=initial.hivision_face_align)
        self.hivision_head_measure_ratio_var = tk.DoubleVar(value=initial.hivision_head_measure_ratio)
        self.hivision_head_height_ratio_var = tk.DoubleVar(value=initial.hivision_head_height_ratio)
        self.hivision_top_distance_max_var = tk.DoubleVar(value=initial.hivision_top_distance_max)
        self.hivision_top_distance_min_var = tk.DoubleVar(value=initial.hivision_top_distance_min)
        self.hivision_brightness_var = tk.DoubleVar(value=initial.hivision_brightness_strength)
        self.hivision_contrast_var = tk.DoubleVar(value=initial.hivision_contrast_strength)
        self.hivision_sharpen_var = tk.DoubleVar(value=initial.hivision_sharpen_strength)
        self.hivision_saturation_var = tk.DoubleVar(value=initial.hivision_saturation_strength)
        self.crop_enabled_var = tk.BooleanVar(value=initial.crop_enabled)
        self.crop_width_var = tk.IntVar(value=initial.crop_width)
        self.crop_height_var = tk.IntVar(value=initial.crop_height)
        self.status_var = tk.StringVar(value="选择一张照片后，点击“运行全部步骤”。")
        self._busy = False
        self._photos: list[Any] = []
        self._last_result: PipelineResult | None = None
        self._last_input: Path | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        ttk = self.ttk
        source = ttk.Frame(self.window, padding=(12, 12, 12, 8))
        source.grid(row=0, column=0, sticky="ew")
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="测试照片").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(source, textvariable=self.input_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(source, text="选择…", command=self.choose_image).grid(row=0, column=2, padx=(8, 0))

        workspace = ttk.Frame(self.window)
        workspace.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(0, weight=1)

        controls_host = ttk.Frame(workspace, width=330)
        controls_host.columnconfigure(0, weight=1)
        controls_host.rowconfigure(0, weight=1)
        controls_host.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        controls_host.grid_propagate(False)
        controls_canvas = self.tk.Canvas(
            controls_host,
            highlightthickness=0,
            borderwidth=0,
            width=310,
        )
        controls_scroll = ttk.Scrollbar(
            controls_host,
            orient="vertical",
            command=controls_canvas.yview,
        )
        controls_canvas.configure(yscrollcommand=controls_scroll.set)
        controls_canvas.grid(row=0, column=0, sticky="nsew")
        controls_scroll.grid(row=0, column=1, sticky="ns")
        controls = ttk.Frame(controls_canvas, padding=(0, 0, 10, 0))
        controls_window = controls_canvas.create_window((0, 0), window=controls, anchor="nw")

        def update_scroll_region(_event=None) -> None:
            controls_canvas.configure(scrollregion=controls_canvas.bbox("all"))

        def fit_controls_width(event) -> None:
            controls_canvas.itemconfigure(controls_window, width=max(1, event.width))

        controls.bind("<Configure>", update_scroll_region)
        controls_canvas.bind("<Configure>", fit_controls_width)
        quality = ttk.LabelFrame(controls, text="内置预检步骤", padding=9)
        quality.pack(fill="x")
        ttk.Checkbutton(quality, text="启用 Pillow + OpenCV + YuNet", variable=self.quality_var).pack(anchor="w")
        for text, variable in [
            ("安全自动方向修正", self.auto_orient_var),
            ("彩色/黑白检查", self.grayscale_var),
            ("人脸数量检查", self.face_var),
            ("反光与过曝检查", self.glare_var),
            ("二次拍摄检查", self.recapture_var),
            ("不合格时停止后续处理", self.stop_on_reject_var),
        ]:
            ttk.Checkbutton(quality, text=text, variable=variable).pack(anchor="w", padx=(16, 0), pady=1)

        thresholds = ttk.LabelFrame(controls, text="可调阈值", padding=9)
        thresholds.pack(fill="x", pady=(9, 0))
        threshold_rows = [
            ("灰度得分", self.gray_threshold_var, 0.0, 1.0, 0.01),
            ("通道差", self.gray_delta_var, 0, 60, 1),
            ("人脸置信度", self.face_confidence_var, 0.05, 0.99, 0.01),
            ("旋转最低置信度", self.orientation_min_confidence_var, 0.05, 0.99, 0.01),
            ("旋转领先分数", self.orientation_confidence_margin_var, 0.0, 0.5, 0.01),
            ("反光面积", self.glare_threshold_var, 0.0, 1.0, 0.01),
            ("反光亮度", self.glare_luma_var, 1, 255, 1),
            ("翻拍得分", self.recapture_threshold_var, 0.0, 1.0, 0.01),
        ]
        for row, (label, variable, start, end, increment) in enumerate(threshold_rows):
            ttk.Label(thresholds, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Spinbox(
                thresholds,
                from_=start,
                to=end,
                increment=increment,
                textvariable=variable,
                width=9,
            ).grid(row=row, column=1, sticky="e", padx=(8, 0), pady=2)

        processing = ttk.LabelFrame(controls, text="输出处理", padding=9)
        processing.pack(fill="x", pady=(9, 0))
        ttk.Label(processing, text="处理模式").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(
            processing,
            textvariable=self.background_var,
            values=list(BACKGROUND_LABELS),
            state="readonly",
            width=22,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        ttk.Label(processing, text="背景色").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(processing, textvariable=self.color_var, width=12).grid(row=2, column=1, sticky="e", pady=2)
        ttk.Checkbutton(
            processing,
            text="最终成片裁切",
            variable=self.crop_enabled_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 2))
        crop_values = ttk.Frame(processing)
        crop_values.grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Label(crop_values, text="宽").pack(side="left")
        ttk.Entry(crop_values, textvariable=self.crop_width_var, width=7).pack(side="left", padx=(4, 8))
        ttk.Label(crop_values, text="× 高").pack(side="left")
        ttk.Entry(crop_values, textvariable=self.crop_height_var, width=7).pack(side="left", padx=(4, 0))

        hivision = ttk.LabelFrame(controls, text="Hivision API 参数", padding=9)
        hivision.pack(fill="x", pady=(9, 0))
        ttk.Label(hivision, text="地址").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Entry(hivision, textvariable=self.hivision_url_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 5))
        ttk.Label(hivision, text="超时/宽/高/DPI").grid(row=2, column=0, columnspan=2, sticky="w")
        hivision_values = ttk.Frame(hivision)
        hivision_values.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 5))
        for variable in [
            self.hivision_timeout_var,
            self.hivision_width_var,
            self.hivision_height_var,
            self.hivision_dpi_var,
        ]:
            ttk.Entry(hivision_values, textvariable=variable, width=6).pack(side="left", padx=(0, 4))
        ttk.Label(hivision, text="抠图模型").grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Combobox(
            hivision,
            textvariable=self.hivision_matting_model_var,
            values=HIVISION_MATTING_MODELS,
            state="normal",
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 5))
        ttk.Label(hivision, text="人脸模型").grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Combobox(
            hivision,
            textvariable=self.hivision_face_model_var,
            values=HIVISION_FACE_MODELS,
            state="normal",
        ).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(2, 5))
        ttk.Checkbutton(hivision, text="使用高清返回", variable=self.hivision_hd_var).grid(row=8, column=0, sticky="w")
        ttk.Checkbutton(hivision, text="人脸对齐", variable=self.hivision_face_align_var).grid(row=8, column=1, sticky="w")

        hivision_rows = [
            ("面部占比", self.hivision_head_measure_ratio_var, 0.05, 0.80, 0.01),
            ("中心高度", self.hivision_head_height_ratio_var, 0.05, 0.95, 0.01),
            ("留白最大", self.hivision_top_distance_max_var, 0.0, 0.80, 0.01),
            ("留白最小", self.hivision_top_distance_min_var, 0.0, 0.80, 0.01),
            ("亮度", self.hivision_brightness_var, -5, 25, 1),
            ("对比度", self.hivision_contrast_var, -10, 50, 1),
            ("锐化", self.hivision_sharpen_var, 0, 5, 1),
            ("饱和度", self.hivision_saturation_var, -10, 50, 1),
        ]
        for row, (label, variable, start, end, increment) in enumerate(hivision_rows, start=9):
            ttk.Label(hivision, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Spinbox(
                hivision,
                from_=start,
                to=end,
                increment=increment,
                textvariable=variable,
                width=9,
            ).grid(row=row, column=1, sticky="e", pady=2)

        self.run_button = ttk.Button(controls, text="运行全部步骤／按参数重跑", command=self.run_async)
        self.run_button.pack(fill="x", pady=(12, 0))
        self.save_button = ttk.Button(controls, text="另存最终结果…", command=self.save_output, state="disabled")
        self.save_button.pack(fill="x", pady=(7, 0))

        def scroll_controls(event) -> str:
            delta = int(-event.delta / 120) if event.delta else 0
            controls_canvas.yview_scroll(delta or (1 if event.delta < 0 else -1), "units")
            return "break"

        def bind_controls_mousewheel(widget) -> None:
            widget.bind("<MouseWheel>", scroll_controls)
            for child in widget.winfo_children():
                bind_controls_mousewheel(child)

        bind_controls_mousewheel(controls_canvas)
        bind_controls_mousewheel(controls)

        preview = ttk.Frame(workspace)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        preview.grid(row=0, column=1, sticky="nsew")
        self.notebook = ttk.Notebook(preview)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        placeholder = ttk.Frame(self.notebook, padding=30)
        ttk.Label(placeholder, text="运行后将在这里按顺序显示每一步的图片、状态和计算指标。", anchor="center").pack(expand=True)
        self.notebook.add(placeholder, text="等待处理")

        footer = ttk.Frame(self.window, padding=(12, 4, 12, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

    def choose_image(self) -> None:
        path = self.filedialog.askopenfilename(
            parent=self.window,
            title="选择要预览处理的照片",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.webp *.bmp"), ("所有文件", "*.*")],
        )
        if path:
            self.input_var.set(path)

    def _options(self) -> PipelineOptions:
        return PipelineOptions(
            quality_enabled=bool(self.quality_var.get()),
            auto_orient=bool(self.auto_orient_var.get()),
            check_grayscale=bool(self.grayscale_var.get()),
            check_face=bool(self.face_var.get()),
            check_glare=bool(self.glare_var.get()),
            check_recapture=bool(self.recapture_var.get()),
            grayscale_ratio_threshold=float(self.gray_threshold_var.get()),
            grayscale_delta_limit=int(self.gray_delta_var.get()),
            face_confidence_threshold=float(self.face_confidence_var.get()),
            orientation_min_confidence=float(self.orientation_min_confidence_var.get()),
            orientation_confidence_margin=float(self.orientation_confidence_margin_var.get()),
            glare_ratio_threshold=float(self.glare_threshold_var.get()),
            glare_luma_threshold=int(self.glare_luma_var.get()),
            recapture_score_threshold=float(self.recapture_threshold_var.get()),
            stop_on_reject=bool(self.stop_on_reject_var.get()),
            background_mode=BACKGROUND_LABELS[self.background_var.get()],
            background_color=self.color_var.get().strip(),
            hivision_url=self.hivision_url_var.get().strip(),
            hivision_timeout=int(self.hivision_timeout_var.get()),
            hivision_width=int(self.hivision_width_var.get()),
            hivision_height=int(self.hivision_height_var.get()),
            hivision_dpi=int(self.hivision_dpi_var.get()),
            hivision_matting_model=self.hivision_matting_model_var.get().strip(),
            hivision_face_model=self.hivision_face_model_var.get().strip(),
            hivision_hd=bool(self.hivision_hd_var.get()),
            hivision_face_align=bool(self.hivision_face_align_var.get()),
            hivision_head_measure_ratio=float(self.hivision_head_measure_ratio_var.get()),
            hivision_head_height_ratio=float(self.hivision_head_height_ratio_var.get()),
            hivision_top_distance_max=float(self.hivision_top_distance_max_var.get()),
            hivision_top_distance_min=float(self.hivision_top_distance_min_var.get()),
            hivision_brightness_strength=float(self.hivision_brightness_var.get()),
            hivision_contrast_strength=float(self.hivision_contrast_var.get()),
            hivision_sharpen_strength=float(self.hivision_sharpen_var.get()),
            hivision_saturation_strength=float(self.hivision_saturation_var.get()),
            crop_enabled=bool(self.crop_enabled_var.get()),
            crop_width=int(self.crop_width_var.get()),
            crop_height=int(self.crop_height_var.get()),
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.run_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.save_button.configure(state="disabled")

    def run_async(self) -> None:
        if self._busy:
            return
        try:
            path = Path(self.input_var.get().strip())
            if not path.is_file():
                raise ValueError("请选择存在的图片文件")
            options = self._options()
            data = path.read_bytes()
        except (KeyError, OSError, TypeError, ValueError) as exc:
            self.messagebox.showerror("参数错误", str(exc), parent=self.window)
            return
        self._set_busy(True)
        self.status_var.set("正在按步骤处理；AI 模式第一次运行可能需要稍候……")

        def worker() -> None:
            try:
                result = run_pipeline(data, options)
                self.window.after(0, lambda: self._show_result(path, result))
            except Exception as exc:
                self.window.after(0, lambda: self._show_error(exc))

        threading.Thread(target=worker, name="photo-workbench", daemon=True).start()

    def _show_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self.status_var.set(f"处理失败：{exc}")
        self.messagebox.showerror("处理失败", str(exc), parent=self.window)

    def _show_result(self, path: Path, result: PipelineResult) -> None:
        try:
            from PIL import Image, ImageTk
        except ImportError as exc:
            self._show_error(exc)
            return
        self._last_result = result
        self._last_input = path
        self._photos.clear()
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        symbols = {"passed": "✓", "adjusted": "↻", "rejected": "✗", "disabled": "－", "skipped": "→"}
        for stage in result.stages:
            frame = self.ttk.Frame(self.notebook, padding=12)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)
            self.ttk.Label(frame, text=stage.detail, wraplength=820, justify="left").grid(row=0, column=0, sticky="ew", pady=(0, 8))
            image = Image.open(io.BytesIO(stage.image_bytes)).convert("RGB")
            image.thumbnail((850, 570), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self._photos.append(photo)
            self.ttk.Label(frame, image=photo, anchor="center").grid(row=1, column=0, sticky="nsew")
            metrics = json.dumps(stage.metrics, ensure_ascii=False, indent=2) if stage.metrics else "本步骤没有额外指标。"
            metrics_box = self.tk.Text(frame, height=5, wrap="word", font=("Consolas", 9))
            metrics_box.insert("1.0", metrics)
            metrics_box.configure(state="disabled")
            metrics_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
            title = f"{symbols.get(stage.status, '•')} {stage.label}"
            self.notebook.add(frame, text=title)
        reason_text = "；".join(reason["message"] for reason in result.reasons)
        if result.status == "rejected":
            self.status_var.set(f"预检不合格：{reason_text}")
        elif result.status == "warning":
            self.status_var.set(f"处理完成但有警告：{reason_text}")
        else:
            self.status_var.set(f"处理完成：共 {len(result.stages)} 个可视化步骤。可调整参数后再次运行。")
        self._set_busy(False)
        self.save_button.configure(state="normal" if result.output_bytes is not None else "disabled")

    def save_output(self) -> None:
        if not self._last_result or self._last_result.output_bytes is None or self._last_input is None:
            self.messagebox.showwarning("没有结果", "当前没有可保存的最终输出。", parent=self.window)
            return
        default_dir = self.output_dir / "手动处理结果"
        default_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.filedialog.asksaveasfilename(
            parent=self.window,
            title="保存重新处理结果",
            initialdir=default_dir,
            initialfile=f"{self._last_input.stem}_{timestamp}.jpg",
            defaultextension=".jpg",
            filetypes=[("JPEG 图片", "*.jpg")],
        )
        if not path:
            return
        destination = Path(path)
        try:
            destination.write_bytes(self._last_result.output_bytes)
            record = {
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "source_file": str(self._last_input),
                "output_file": str(destination),
                "status": self._last_result.status,
                "reasons": self._last_result.reasons,
                "metrics": self._last_result.metrics,
                "options": asdict(self._options()),
                "stages": [
                    {
                        "code": stage.code,
                        "label": stage.label,
                        "status": stage.status,
                        "detail": stage.detail,
                        "metrics": stage.metrics,
                    }
                    for stage in self._last_result.stages
                ],
            }
            destination.with_suffix(".json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self.messagebox.showerror("保存失败", str(exc), parent=self.window)
            return
        self.status_var.set(f"已保存：{destination}")
        self.messagebox.showinfo("保存完成", f"图片和参数记录已保存到：\n{destination.parent}", parent=self.window)
