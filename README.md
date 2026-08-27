# Modal Inference Hub

面向人的实验工作台：组合 modal-2D-client 与 modal-3D-client，但不拥有 Provider
执行、模型生命周期或 Modal CLI Schema。

当前支持：

- 粘贴 `modal token set --token-id ... --token-secret ...` 并安全识别凭据；只解析，不执行。
- 从 UI 调用 2D/3D 仓库自有部署器，并用 capabilities 验证部署结果。
- 单 Prompt 实验、批量 Prompt 候选生成、批量 PNG/JPEG/WebP 直接转 3D。
- 确定性 Sidecar Job ID、uncertain 恢复、内容寻址输入和 GLB 流式下载。

~~~text
用户文本
   │
   ▼
实验 exp_xxx（Hub 真值）
   │
   ├─ N 个确定性 job ref ──────► modal-2D-client ──► modal-2D
   │                                  │
   │                           PNG Artifact descriptor
   │                                  │
   ├─ 人工选择 candidate ◄────────────┘
   │
   └─ 选中 PNG 原样转交 ───────► modal-3D-client ──► modal-3D
                                      │
                                GLB Artifact descriptor
~~~

## 启动

先独立启动两个参考 Sidecar；Hub 不替它们重做 CLI：

~~~powershell
$env:MODAL_2D_PORT = "3212"
uv run --project ..\modal-2D-client python -m modal_2d_client.server

$env:MODAL_3D_CLIENT_PORT = "3213"
uv run --project ..\modal-3D-client python -m modal_3d_client.server
~~~

Sidecar 的 Modal 认证仍由 Sidecar 自己完成。然后启动 Hub：

~~~powershell
uv sync --group dev
npm install
npm run desktop:dev
~~~

纯 Web 开发时可单独运行 Python Hub，并设置 VITE_HUB_URL。Sidecar 地址可通过
MODAL_2D_CLIENT_URL、MODAL_3D_CLIENT_URL 覆盖；会话 token 分别通过
MODAL_2D_CLIENT_TOKEN、MODAL_3D_CLIENT_TOKEN 提供。

## 凭据与自动部署

连接界面接受完整命令或一对凭据：

~~~text
modal token set --token-id <TOKEN_ID> --token-secret <TOKEN_SECRET>
~~~

输入不会作为 shell 命令执行。连接成功或失败后 UI 都会清空输入；Hub 不把凭据写入
SQLite、Experiment、Batch 或 Deployment document。

点击 Provider 旁的“部署”后，Hub 读取 Provider 自己导出的计划并要求再次确认。默认从
相邻的 `../modal-2D`、`../modal-3D` 启动部署器，也可覆盖：

~~~powershell
$env:MODAL_2D_PROVIDER_REPO = "D:\path\to\modal-2D"
$env:MODAL_3D_PROVIDER_REPO = "D:\path\to\modal-3D"
# 或一次设置五仓工作区根目录：
$env:MODAL_HUB_WORKSPACE = "D:\path\to\workspace"
~~~

部署需要本机 `uv`。Hub 只保存脱敏后的部署阶段；2D/3D 的 App 列表、CLI 参数、worker
注册与 adapter revision 校验全部留在 Provider 仓库。

## 批量任务

~~~text
多行 Prompt ─► Batch ─► N 个 Experiment ─► awaiting_review ─► 人工选择

多张图片 ─► 内容寻址 InputStore ─► Batch ─► N 个 DirectImage Run ─► GLB
~~~

- Prompt 输入一行一项，单批最多 50 项。
- 图片支持 PNG/JPEG/WebP，单文件最多 25 MiB，单批最多 50 项。
- 重复 Prompt 与相同内容摘要的图片会保持顺序去重，避免重复付费提交。
- Provider 提交使用有界顺序调度；Batch 只保存目标引用，不复制 Job/Artifact document。
- 批次和 Experiment 都显示在左侧历史中，进入候选选择后可以回到原批次。

## 验证

~~~powershell
uv run --group dev ruff check hub agent tests
uv run --group dev pytest -q
npm run build
npm test
cargo test --manifest-path src-tauri/Cargo.toml
~~~

旧 modal-3D-client 数据目录会原地保留并优先复用。Hub 新数据分别写入
`experiments.sqlite3`、`direct-images.sqlite3`、`batches.sqlite3`、
`deployments.sqlite3` 与内容寻址 `inputs/`；不会改写旧 projects.sqlite3 或 Artifact 历史。
