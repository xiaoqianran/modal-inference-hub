from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

# The application supports CUDA and CPU only. Avoid collecting the TensorRT
# provider binary so PyInstaller does not chase unavailable TensorRT DLLs.
binaries = [
    item
    for item in collect_dynamic_libs("onnxruntime")
    if "tensorrt" not in Path(item[0]).name.lower()
]
