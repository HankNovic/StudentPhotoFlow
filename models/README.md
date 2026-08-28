# Bundled models

The portable build bundles these models so end users do not need a network connection.

| File | Upstream | License | SHA-256 |
|---|---|---|---|
| `u2netp/u2netp.onnx` | [rembg model release](https://github.com/danielgatis/rembg/releases/tag/v0.0.0) / [U-2-Net](https://github.com/xuebinqin/U-2-Net) | See upstream notices | `309C8469258DDA742793DCE0EBEA8E6DD393174F89934733ECC8B14C76F4DDD8` |
| `yunet/face_detection_yunet_2023mar.onnx` | [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | MIT | `8F2383E4DD3CFBB4553EA8718107FC0423210DC964F9F4280604804ED2552FA4` |

HivisionIDPhotos is not bundled. StudentPhotoFlow only calls a user-configured Hivision HTTP API when that optional processing mode is selected.
