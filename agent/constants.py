"""客户端与云端 `modal-3D` 之间的协议常量（单一事实来源）。

这些字符串与 `/workspace/wk/modal-3D` 仓库中的同名常量一一对应，
任何一端改动都必须同步到另一端，否则会静默断连。对应关系：

- GATEWAY_APP        ←→ modal_3d/gateway.py        : APP_NAME
- SAM_APP            ←→ modal_3d/sam3_1.py         : APP_NAME
- ARTIFACTS_VOLUME   ←→ modal_3d/gateway.py        : artifacts (modal.Volume.from_name)
- CONTRACT           ←→ modal_3d/capabilities.py   : CONTRACT
- GATEWAY_SUBMIT     ←→ capabilities_document()    : generation.submit_function
- GATEWAY_PIPELINE   ←→ capabilities_document()    : generation.pipeline_function
- JOB_TRANSPORT      ←→ capabilities_document()    : generation.job_transport
- SOURCE_MAX_BYTES   ←→ capabilities_document()    : sam.cloud.input.max_bytes
- SOURCE_MAX_PIXELS  ←→ capabilities_document()    : sam.cloud.input.max_pixels
- SOURCE_MIME_TYPES  ←→ capabilities_document()    : sam.cloud.input.mime
"""

from __future__ import annotations

# 云端 gateway App 名（客户端由此定位并调用 submit / capabilities）
GATEWAY_APP = "modal-3d-gateway"

# 云端 SAM 3.1 预处理 App 名（客户端由此定位并调用 segment / refine / materialize）
SAM_APP = "modal-3d-sam31"

# 共享产物 Volume：canonical RGBA 与 GLB 都存放在这里
ARTIFACTS_VOLUME = "modal-3d-artifacts"

# 能力文档契约版本：客户端只接受此版本的 gateway
CONTRACT = "modal-3d.capabilities.v1"

# gateway 上用于提交 3D 生成的函数名（异步，返回可恢复的 modal.FunctionCall）
GATEWAY_SUBMIT = "submit"

# gateway 上「原图一步到位」的流水线函数名（客户端当前未使用，保留以供对齐）
GATEWAY_PIPELINE = "generate_from_raw"

# 任务传输方式：客户端据此确认 gateway 返回的是可恢复的 FunctionCall
JOB_TRANSPORT = "modal.FunctionCall"

# 源图片（source-image）契约的安全上限：云端 capability 必须与这些边界一致，
# 否则 models._validate_document 会判定为 IncompatibleCapability。
# 单位：字节。
SOURCE_MAX_BYTES = 20 * 1024 * 1024
# 单位：像素（宽 × 高）。
SOURCE_MAX_PIXELS = 40_000_000
# 允许的源图片 MIME 类型（顺序即客户端 image_input 的探测顺序）。
SOURCE_MIME_TYPES = ("image/png", "image/jpeg", "image/webp")
