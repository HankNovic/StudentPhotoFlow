from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


PIPELINE_VERSION = 2


class PipelineError(RuntimeError):
    pass


@dataclass
class PipelineOptions:
    quality_enabled: bool = False
    auto_orient: bool = True
    check_grayscale: bool = True
    check_face: bool = True
    check_glare: bool = True
    check_recapture: bool = True
    grayscale_ratio_threshold: float = 0.85
    grayscale_delta_limit: int = 10
    face_confidence_threshold: float = 0.75
    glare_ratio_threshold: float = 0.08
    glare_luma_threshold: int = 245
    recapture_score_threshold: float = 0.72
    stop_on_reject: bool = True
    background_mode: str = "ai"  # none | quick | ai | hivision
    background_color: str = "#438EDB"
    hivision_url: str = "http://127.0.0.1:8080"
    hivision_timeout: int = 120
    hivision_height: int = 413
    hivision_width: int = 295
    hivision_dpi: int = 300
    hivision_matting_model: str = "modnet_photographic_portrait_matting"
    hivision_face_model: str = "mtcnn"


@dataclass
class PipelineStage:
    code: str
    label: str
    status: str
    detail: str
    image_bytes: bytes
    mime_type: str = "image/jpeg"
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    status: str
    stages: list[PipelineStage]
    output_bytes: bytes | None
    output_mime_type: str | None
    face_count: int | None
    rotation_ccw: int
    reasons: list[dict[str, str]]
    metrics: dict[str, Any]
    detector: str = "not_used"
    background_engine: str = "none"


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _yunet_model_path() -> Path | None:
    candidates = [
        _resource_root() / "models" / "yunet" / "face_detection_yunet_2023mar.onnx",
        Path(__file__).resolve().parent / "models" / "yunet" / "face_detection_yunet_2023mar.onnx",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def _yunet_model_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


def _load_haar_cascade(cv2):
    """Load OpenCV's fallback cascade without depending on a Unicode path."""
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(str(cascade_path))
    if not cascade.empty():
        return cascade

    # OpenCV's Windows filename bridge can reject paths containing Chinese
    # characters. Python can still read the file, and FileStorage can parse the
    # XML from memory, which keeps this fallback portable on those paths.
    try:
        cascade_xml = cascade_path.read_text(encoding="utf-8")
        storage = cv2.FileStorage(
            cascade_xml,
            cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY,
        )
        try:
            memory_cascade = cv2.CascadeClassifier()
            loaded = storage.isOpened() and memory_cascade.read(storage.getFirstTopLevelNode())
        finally:
            storage.release()
    except (OSError, UnicodeError, cv2.error) as exc:
        raise PipelineError(f"OpenCV 备用人脸模型加载失败：{exc}") from exc
    if not loaded or memory_cascade.empty():
        raise PipelineError("OpenCV 备用人脸模型加载失败")
    return memory_cascade


def _parse_color(color: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", color.strip())
    if not match:
        raise PipelineError(f"背景色格式无效：{color}")
    value = match.group(1)
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _load_image(data: bytes):
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise PipelineError("内置图片组件 Pillow 不可用，便携包可能不完整") from exc
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")
    except Exception as exc:
        raise PipelineError(f"图片解码失败：{exc}") from exc


def _encode_image(image, mime_type: str = "image/jpeg") -> bytes:
    stream = io.BytesIO()
    if mime_type == "image/png":
        image.save(stream, format="PNG", optimize=True)
    else:
        image.convert("RGB").save(stream, format="JPEG", quality=95, subsampling=0, optimize=True)
    return stream.getvalue()


def _analysis_image(image, maximum: int = 1600):
    if max(image.size) <= maximum:
        return image, 1.0
    ratio = maximum / max(image.size)
    resized = image.resize(
        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    )
    return resized, ratio


def _detect_faces(image, confidence: float) -> tuple[list[dict[str, Any]], str]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise PipelineError("内置 OpenCV 不可用，便携包可能不完整") from exc

    analysis, scale = _analysis_image(image)
    rgb = np.asarray(analysis.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    model_path = _yunet_model_path()
    if model_path is not None and hasattr(cv2, "FaceDetectorYN"):
        try:
            # Use the in-memory overload. OpenCV's Windows path loader can fail
            # when the portable folder contains Chinese characters.
            detector = cv2.FaceDetectorYN.create(
                "onnx",
                _yunet_model_bytes(str(model_path)),
                b"",
                (width, height),
                float(confidence),
                0.3,
                5000,
            )
            detector.setInputSize((width, height))
            _retval, raw_faces = detector.detect(bgr)
            faces: list[dict[str, Any]] = []
            if raw_faces is not None:
                for raw in raw_faces:
                    x, y, w, h = (float(value) / scale for value in raw[:4])
                    landmarks = [
                        (float(raw[index]) / scale, float(raw[index + 1]) / scale)
                        for index in range(4, 14, 2)
                    ]
                    faces.append({
                        "box": [round(x), round(y), round(w), round(h)],
                        "landmarks": landmarks,
                        "score": float(raw[14]),
                    })
            return faces, "yunet"
        except Exception:
            # A damaged or incompatible ONNX file must not stop original export.
            pass

    cascade = _load_haar_cascade(cv2)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    minimum = max(30, min(gray.shape[:2]) // 12)
    raw_faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(minimum, minimum)
    )
    faces = []
    for x, y, w, h in raw_faces:
        faces.append({
            "box": [round(x / scale), round(y / scale), round(w / scale), round(h / scale)],
            "landmarks": [],
            "score": 1.0,
        })
    return faces, "haar_fallback"


def _face_rank(image, faces: list[dict[str, Any]]) -> tuple[float, ...]:
    if not faces:
        return 0.0, 0.0, 0.0, 0.0
    largest = max(face["box"][2] * face["box"][3] for face in faces)
    area_ratio = largest / max(1, image.width * image.height)
    confidence = max(float(face.get("score", 0.0)) for face in faces)
    return 1.0 if len(faces) == 1 else 0.5, 1.0, confidence, area_ratio


def _orient_image(image, options: PipelineOptions):
    angles = (0, 90, 180, 270) if options.auto_orient else (0,)
    candidates: list[tuple[tuple[float, ...], int, Any, list[dict[str, Any]], str]] = []
    for angle in angles:
        rotated = image if angle == 0 else image.rotate(angle, expand=True)
        faces, detector = _detect_faces(rotated, options.face_confidence_threshold)
        candidates.append((_face_rank(rotated, faces), angle, rotated, faces, detector))
    _rank, angle, oriented, faces, detector = max(candidates, key=lambda item: item[0])
    return oriented, angle, faces, detector


def _face_region(image, faces: list[dict[str, Any]]):
    if not faces:
        left = round(image.width * 0.2)
        top = round(image.height * 0.15)
        right = round(image.width * 0.8)
        bottom = round(image.height * 0.85)
        return left, top, right, bottom
    x, y, width, height = max(faces, key=lambda item: item["box"][2] * item["box"][3])["box"]
    padding_x = round(width * 0.12)
    padding_y = round(height * 0.12)
    return (
        max(0, x - padding_x),
        max(0, y - padding_y),
        min(image.width, x + width + padding_x),
        min(image.height, y + height + padding_y),
    )


def _grayscale_metrics(image, faces: list[dict[str, Any]], delta_limit: int) -> dict[str, float]:
    try:
        import numpy as np
    except ImportError as exc:
        raise PipelineError("内置 NumPy 不可用，便携包可能不完整") from exc
    analysis, _scale = _analysis_image(image, maximum=1000)
    array = np.asarray(analysis.convert("RGB"), dtype=np.int16)
    delta = array.max(axis=2) - array.min(axis=2)
    global_ratio = float((delta <= delta_limit).mean())
    saturation = delta / np.maximum(array.max(axis=2), 1)
    global_saturation = float(saturation.mean())

    if faces:
        scale_x = analysis.width / image.width
        scale_y = analysis.height / image.height
        left, top, right, bottom = _face_region(image, faces)
        roi = array[
            max(0, round(top * scale_y)):min(analysis.height, round(bottom * scale_y)),
            max(0, round(left * scale_x)):min(analysis.width, round(right * scale_x)),
        ]
        if roi.size:
            roi_delta = roi.max(axis=2) - roi.min(axis=2)
            face_ratio = float((roi_delta <= delta_limit).mean())
            face_saturation = float((roi_delta / np.maximum(roi.max(axis=2), 1)).mean())
        else:
            face_ratio, face_saturation = global_ratio, global_saturation
    else:
        face_ratio, face_saturation = global_ratio, global_saturation
    # The face region is more reliable than the whole image. A photographed
    # paper photo may be grayscale while the surrounding desk is colorful.
    score = max(global_ratio, face_ratio)
    return {
        "grayscale_score": round(float(score), 4),
        "global_grayscale_ratio": round(global_ratio, 4),
        "face_grayscale_ratio": round(face_ratio, 4),
        "mean_saturation": round(global_saturation, 4),
        "face_mean_saturation": round(face_saturation, 4),
    }


def _glare_metrics(image, faces: list[dict[str, Any]], luma_threshold: int):
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise PipelineError("内置 OpenCV/NumPy 不可用，便携包可能不完整") from exc
    array = np.asarray(image.convert("RGB"))
    hsv = cv2.cvtColor(array, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    left, top, right, bottom = _face_region(image, faces)
    roi_gray = gray[top:bottom, left:right]
    roi_sat = hsv[top:bottom, left:right, 1]
    if not roi_gray.size:
        return {"glare_score": 0.0, "highlight_ratio": 0.0, "largest_highlight_ratio": 0.0}, None
    mask = ((roi_gray >= luma_threshold) & (roi_sat <= 48)).astype(np.uint8)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    largest = 0
    if count > 1:
        largest = int(stats[1:, cv2.CC_STAT_AREA].max())
    area = int(mask.size)
    ratio = float(mask.mean())
    largest_ratio = largest / max(1, area)
    score = max(ratio, largest_ratio * 1.35)
    full_mask = np.zeros(gray.shape, dtype=np.uint8)
    full_mask[top:bottom, left:right] = mask
    return {
        "glare_score": round(min(1.0, score), 4),
        "highlight_ratio": round(ratio, 4),
        "largest_highlight_ratio": round(largest_ratio, 4),
    }, full_mask


def _recapture_metrics(image, faces: list[dict[str, Any]], color_metrics: dict[str, float] | None = None):
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise PipelineError("内置 OpenCV/NumPy 不可用，便携包可能不完整") from exc
    analysis, scale = _analysis_image(image, maximum=1200)
    array = np.asarray(analysis.convert("RGB"))
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 45, 135)
    contours, _hierarchy = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = float(analysis.width * analysis.height)
    best_score = 0.0
    best_quad = None
    best_area_ratio = 0.0
    margin = max(4, round(min(analysis.size) * 0.012))
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:40]:
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        area_ratio = cv2.contourArea(polygon) / max(1.0, image_area)
        if not 0.18 <= area_ratio <= 0.94:
            continue
        points = polygon.reshape(4, 2)
        touches_edge = any(
            x <= margin or y <= margin or x >= analysis.width - margin or y >= analysis.height - margin
            for x, y in points
        )
        if touches_edge:
            continue
        rectangle = cv2.minAreaRect(polygon)
        rect_area = max(1.0, rectangle[1][0] * rectangle[1][1])
        rectangularity = min(1.0, cv2.contourArea(polygon) / rect_area)
        area_component = min(1.0, max(0.0, (area_ratio - 0.18) / 0.42))
        score = 0.72 * rectangularity + 0.28 * area_component
        if score > best_score:
            best_score = score
            best_area_ratio = area_ratio
            best_quad = [(round(float(x) / scale), round(float(y) / scale)) for x, y in points]
    if color_metrics is None:
        color_metrics = _grayscale_metrics(image, faces, 10)
    face_gray = float(color_metrics.get("face_grayscale_ratio", 0.0))
    global_gray = float(color_metrics.get("global_grayscale_ratio", 0.0))
    color_gap = max(0.0, face_gray - global_gray)
    color_context_score = min(1.0, max(0.0, (color_gap - 0.12) / 0.35))
    color_context_score *= min(1.0, max(0.0, (face_gray - 0.65) / 0.25))
    best_score = max(best_score, color_context_score)
    return {
        "recapture_score": round(float(best_score), 4),
        "inner_rectangle_area_ratio": round(float(best_area_ratio), 4),
        "grayscale_context_gap": round(color_gap, 4),
    }, best_quad


def _annotate(image, faces=None, glare_mask=None, quad=None):
    try:
        import numpy as np
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise PipelineError("内置图片标注组件不可用") from exc
    annotated = image.convert("RGB").copy()
    if glare_mask is not None:
        base = np.asarray(annotated).copy()
        mask = glare_mask.astype(bool)
        if mask.shape == base.shape[:2]:
            base[mask] = (0.55 * base[mask] + 0.45 * np.array([255, 35, 35])).astype(np.uint8)
            annotated = Image.fromarray(base, mode="RGB")
    draw = ImageDraw.Draw(annotated)
    line = max(2, min(image.size) // 220)
    for face in faces or []:
        x, y, width, height = face["box"]
        draw.rectangle((x, y, x + width, y + height), outline=(24, 190, 94), width=line)
        for px, py in face.get("landmarks", []):
            radius = max(2, line)
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=(34, 112, 255))
    if quad:
        draw.line(list(quad) + [quad[0]], fill=(255, 145, 20), width=line)
    return annotated


def _quick_replace_background(image, color: str):
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError as exc:
        raise PipelineError("快速换背景需要 Pillow 和 NumPy") from exc
    array = np.asarray(image.convert("RGB")).copy()
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
        raise PipelineError("无法识别连通的纯色背景；可改用 AI 模式")
    radius = max(1.0, min(height, width) / 320.0)
    mask_image = Image.fromarray(connected.astype(np.uint8) * 255, mode="L")
    mask = np.asarray(mask_image.filter(ImageFilter.GaussianBlur(radius=radius)))
    alpha = mask.astype(np.float32)[..., None] / 255.0
    target = np.array(_parse_color(color), dtype=np.float32).reshape(1, 1, 3)
    output = array.astype(np.float32) * (1.0 - alpha) + target * alpha
    return Image.fromarray(np.clip(output, 0, 255).astype(np.uint8), mode="RGB")


_REMBG_LOCK = threading.Lock()
_REMBG_SESSION: Any = None


def _ai_replace_background(image, color: str):
    global _REMBG_SESSION
    # PyInstaller does not preserve source locators used by numba's on-disk
    # cache inside pymatting. The default rembg path does not need that JIT;
    # disabling it prevents a portable-only "no locator available" failure.
    os.environ.setdefault("NUMBA_DISABLE_JIT", "1")
    try:
        from PIL import Image
        from rembg import new_session, remove
    except ImportError as exc:
        raise PipelineError("内置 AI 模式不可用，便携包可能不完整") from exc
    resource_root = _resource_root()
    bundled_model = resource_root / "models" / "u2netp" / "u2netp.onnx"
    if bundled_model.is_file():
        os.environ.setdefault("U2NET_HOME", str(resource_root))
    with _REMBG_LOCK:
        if _REMBG_SESSION is None:
            _REMBG_SESSION = new_session("u2netp")
        session = _REMBG_SESSION
    cutout = remove(image.convert("RGBA"), session=session)
    if isinstance(cutout, bytes):
        cutout = Image.open(io.BytesIO(cutout)).convert("RGBA")
    else:
        cutout = cutout.convert("RGBA")
    background = Image.new("RGBA", cutout.size, _parse_color(color) + (255,))
    return Image.alpha_composite(background, cutout).convert("RGB")


def _multipart_payload(fields: dict[str, Any], file_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----StudentPhotoFlow{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="input_image"; filename="photo.jpg"\r\n',
        b"Content-Type: image/jpeg\r\n\r\n",
        file_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), boundary


def _hivision_replace_background(image, options: PipelineOptions):
    try:
        from PIL import Image
    except ImportError as exc:
        raise PipelineError("Pillow 不可用") from exc
    fields = {
        "height": options.hivision_height,
        "width": options.hivision_width,
        "human_matting_model": options.hivision_matting_model,
        "face_detect_model": options.hivision_face_model,
        "hd": "false",
        "dpi": options.hivision_dpi,
        "face_alignment": "true",
    }
    input_bytes = _encode_image(image, "image/jpeg")
    body, boundary = _multipart_payload(fields, input_bytes)
    endpoint = options.hivision_url.rstrip("/") + "/idphoto"
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "StudentPhotoFlow/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5, int(options.hivision_timeout))) as response:
            raw = response.read(50 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise PipelineError(f"Hivision API 返回 HTTP {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PipelineError(f"无法连接 Hivision API：{exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not payload.get("status"):
            raise ValueError(payload.get("error") or payload.get("message") or "status=false")
        encoded = payload["image_base64_standard"]
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        foreground = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
    except Exception as exc:
        raise PipelineError(f"Hivision API 响应无法解析：{exc}") from exc
    background = Image.new("RGBA", foreground.size, _parse_color(options.background_color) + (255,))
    return Image.alpha_composite(background, foreground).convert("RGB")


def validate_pipeline_options(options: PipelineOptions) -> None:
    if options.background_mode not in {"none", "quick", "ai", "hivision"}:
        raise PipelineError(f"未知处理模式：{options.background_mode}")
    _parse_color(options.background_color)
    options.grayscale_ratio_threshold = min(1.0, max(0.0, float(options.grayscale_ratio_threshold)))
    options.grayscale_delta_limit = min(60, max(0, int(options.grayscale_delta_limit)))
    options.face_confidence_threshold = min(0.99, max(0.05, float(options.face_confidence_threshold)))
    options.glare_ratio_threshold = min(1.0, max(0.0, float(options.glare_ratio_threshold)))
    options.glare_luma_threshold = min(255, max(1, int(options.glare_luma_threshold)))
    options.recapture_score_threshold = min(1.0, max(0.0, float(options.recapture_score_threshold)))
    if options.background_mode == "hivision" and not options.hivision_url.strip():
        raise PipelineError("选择 Hivision 时必须填写 API 地址")


def run_pipeline(data: bytes, options: PipelineOptions) -> PipelineResult:
    validate_pipeline_options(options)
    image = _load_image(data)
    stages = [PipelineStage(
        code="source",
        label="原图解码",
        status="passed",
        detail=f"已读取图片并应用 EXIF 方向，尺寸 {image.width}×{image.height}",
        image_bytes=_encode_image(image),
        metrics={"width": image.width, "height": image.height},
    )]
    reasons: list[dict[str, str]] = []
    metrics: dict[str, Any] = {"width": image.width, "height": image.height}
    face_count: int | None = None
    rotation = 0
    detector = "not_used"
    working = image

    if options.quality_enabled:
        working, rotation, faces, detector = _orient_image(image, options)
        face_count = len(faces)
        metrics.update({
            "rotation_ccw": rotation,
            "face_count": face_count,
            "face_detector": detector,
            "face_scores": [round(float(face.get("score", 0.0)), 4) for face in faces],
        })
        orientation_detail = "方向无需调整" if rotation == 0 else f"自动逆时针旋转 {rotation}°"
        stages.append(PipelineStage(
            code="orientation",
            label="方向与人脸定位",
            status="adjusted" if rotation else "passed",
            detail=f"{orientation_detail}；检测器 {detector}",
            image_bytes=_encode_image(_annotate(working, faces=faces)),
            metrics={"rotation_ccw": rotation, "face_count": face_count, "detector": detector},
        ))

        if options.check_grayscale:
            gray_metrics = _grayscale_metrics(working, faces, options.grayscale_delta_limit)
            metrics.update(gray_metrics)
            failed = gray_metrics["grayscale_score"] >= options.grayscale_ratio_threshold
            if failed:
                reasons.append({
                    "code": "MONOCHROME_OR_GRAYSCALE",
                    "message": "照片接近黑白或灰度图，请重新上传彩色证件照",
                })
            stages.append(PipelineStage(
                code="grayscale",
                label="彩色/黑白检查",
                status="rejected" if failed else "passed",
                detail=(
                    f"灰度得分 {gray_metrics['grayscale_score']:.3f}，"
                    f"阈值 {options.grayscale_ratio_threshold:.3f}"
                ),
                image_bytes=_encode_image(working),
                metrics=gray_metrics,
            ))
        else:
            stages.append(PipelineStage("grayscale", "彩色/黑白检查", "disabled", "用户已关闭此步骤", _encode_image(working)))

        if options.check_face:
            if face_count == 0:
                reasons.append({"code": "NO_FACE", "message": "未识别到清晰正面人脸，请重新上传"})
            elif face_count and face_count > 1:
                reasons.append({"code": "MULTIPLE_FACES", "message": f"检测到 {face_count} 张人脸，请仅上传本人证件照"})
            stages.append(PipelineStage(
                code="face",
                label="人脸有效性检查",
                status="passed" if face_count == 1 else "rejected",
                detail=f"检测到 {face_count} 张人脸；置信度阈值 {options.face_confidence_threshold:.2f}",
                image_bytes=_encode_image(_annotate(working, faces=faces)),
                metrics={"face_count": face_count, "detector": detector},
            ))
        else:
            stages.append(PipelineStage("face", "人脸有效性检查", "disabled", "用户已关闭此步骤", _encode_image(working)))

        if options.check_glare:
            glare_metrics, glare_mask = _glare_metrics(working, faces, options.glare_luma_threshold)
            metrics.update(glare_metrics)
            failed = glare_metrics["glare_score"] >= options.glare_ratio_threshold
            if failed:
                reasons.append({"code": "SEVERE_GLARE", "message": "脸部存在严重反光或过曝，请重新拍摄"})
            stages.append(PipelineStage(
                code="glare",
                label="反光与过曝检查",
                status="rejected" if failed else "passed",
                detail=f"反光得分 {glare_metrics['glare_score']:.3f}，阈值 {options.glare_ratio_threshold:.3f}",
                image_bytes=_encode_image(_annotate(working, faces=faces, glare_mask=glare_mask)),
                metrics=glare_metrics,
            ))
        else:
            stages.append(PipelineStage("glare", "反光与过曝检查", "disabled", "用户已关闭此步骤", _encode_image(working)))

        if options.check_recapture:
            recapture_metrics, quad = _recapture_metrics(
                working,
                faces,
                gray_metrics if options.check_grayscale else None,
            )
            metrics.update(recapture_metrics)
            failed = recapture_metrics["recapture_score"] >= options.recapture_score_threshold
            if failed:
                reasons.append({"code": "RECAPTURED_PHOTO", "message": "疑似翻拍纸质照片或屏幕，请上传原始电子照片"})
            stages.append(PipelineStage(
                code="recapture",
                label="二次拍摄检查",
                status="rejected" if failed else "passed",
                detail=f"翻拍得分 {recapture_metrics['recapture_score']:.3f}，阈值 {options.recapture_score_threshold:.3f}",
                image_bytes=_encode_image(_annotate(working, faces=faces, quad=quad)),
                metrics=recapture_metrics,
            ))
        else:
            stages.append(PipelineStage("recapture", "二次拍摄检查", "disabled", "用户已关闭此步骤", _encode_image(working)))
    else:
        stages.append(PipelineStage(
            code="quality_disabled",
            label="内置预检",
            status="disabled",
            detail="用户未启用 Pillow + OpenCV + YuNet 预检",
            image_bytes=_encode_image(working),
        ))

    if reasons and options.stop_on_reject:
        stages.append(PipelineStage(
            code="processing_stopped",
            label="处理终止",
            status="skipped",
            detail="预检不合格，未进入换背景步骤",
            image_bytes=_encode_image(working),
        ))
        return PipelineResult(
            status="rejected",
            stages=stages,
            output_bytes=None,
            output_mime_type=None,
            face_count=face_count,
            rotation_ccw=rotation,
            reasons=reasons,
            metrics=metrics,
            detector=detector,
            background_engine="none",
        )

    if options.background_mode == "none":
        stages.append(PipelineStage(
            code="background",
            label="换背景",
            status="disabled",
            detail="未选择换背景处理",
            image_bytes=_encode_image(working),
        ))
        output = _encode_image(working)
        engine = "none"
    else:
        try:
            if options.background_mode == "quick":
                final_image = _quick_replace_background(working, options.background_color)
                label, engine = "快速换背景", "quick"
            elif options.background_mode == "ai":
                final_image = _ai_replace_background(working, options.background_color)
                label, engine = "AI 智能抠图换背景", "rembg-u2netp"
            else:
                final_image = _hivision_replace_background(working, options)
                label, engine = "Hivision API 证件照处理", "hivision-api"
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(f"图片处理失败：{type(exc).__name__}: {exc}") from exc
        output = _encode_image(final_image)
        stages.append(PipelineStage(
            code="background",
            label=label,
            status="passed",
            detail=f"处理引擎 {engine}；背景色 {options.background_color.upper()}",
            image_bytes=output,
            metrics={"engine": engine, "background_color": options.background_color.upper()},
        ))

    return PipelineResult(
        status="warning" if reasons else "success",
        stages=stages,
        output_bytes=output,
        output_mime_type="image/jpeg",
        face_count=face_count,
        rotation_ccw=rotation,
        reasons=reasons,
        metrics=metrics,
        detector=detector,
        background_engine=engine,
    )


def save_pipeline_stages(result: PipelineResult, directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    for index, stage in enumerate(result.stages):
        suffix = ".png" if stage.mime_type == "image/png" else ".jpg"
        safe_code = re.sub(r"[^a-zA-Z0-9_-]+", "_", stage.code).strip("_") or "stage"
        path = directory / f"{index:02d}_{safe_code}{suffix}"
        path.write_bytes(stage.image_bytes)
        saved.append({
            "index": index,
            "code": stage.code,
            "label": stage.label,
            "status": stage.status,
            "detail": stage.detail,
            "file": str(path),
            "metrics": stage.metrics,
        })
    return saved
