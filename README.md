# modal-3D 三维创作客户端

面向 Windows 的本地/云端混合 `modal-3D` 客户端。

## 技术栈

- Tauri 2
- React + TypeScript
- Python 3.12 + FastAPI 本地代理
- 使用 `uv` 管理 Python 环境
- 最新版 `modal[api-proxy-support]`

云端模型工作节点保存在公开的 `modal-3D` 仓库中。本地 SAM 3.1 是可选功能，不会随基础客户端安装。Modal 凭据通过本机代理验证；Windows 版本可将凭据保存到 Windows 凭据管理器，并直接恢复到本地代理，不会重新加载到 React 界面。

当前客户端只接受新版 `modal-3D.capabilities.v1` gateway：`capabilities` 动态发现模型，`submit` 返回可恢复的 `modal.FunctionCall` 任务。客户端会校验 gateway、worker 输入、GLB artifact 与任务 ID 协议；旧版 `submit_job/result_job` 接口不再保留。


## 当前云端 MVP

桌面端已经打通第一条真实纵向链路：

```text
图片 → Cloud SAM 3.1 → 候选对象 → Canonical RGBA
     → 选择 3D 模型 → 异步 Job → GLB → Three.js 预览 / 下载
```

React 只调用本地 Agent 的产品 API；Agent 使用当前用户的 `modal.Client` 访问私有 Modal RPC 和共享 `modal-3d-artifacts` Volume。Cloud SAM 产出的 canonical RGBA 直接以 Volume path 交给 3D Worker，不会下载到本地再上传。

当前模型 registry 已包含 FastSAM3D++、Hermite-TRELLIS2++、Hunyuan2.1++ 和 Pixal3D。模型列表不在客户端硬编码；每个模型只暴露云端 capability 中声明的 profile，模型特有参数由 Agent 校验和展开，React 不直接拼 Modal options。

Generation Job 会写入客户端 app-data 目录中的 `jobs.sqlite3`。Agent 重启后会恢复本地 Job ID 与 Modal `FunctionCall` ID 的映射，并继续轮询尚未结束的远程任务；最近成功结果也可重新加载到 Viewer。

客户端同时维护本地 Project Workspace：选中源图片时会在 app-data 中创建 Project 并保存 source image；`concept / SAM scene / canonical path / model / profile / job / GLB` 随工作流逐步写入 `projects.sqlite3`。重启后恢复的是完整作品上下文，而不是孤立 Job。

Project 可从最近项目列表删除；生成中的 Project 必须先取消任务。删除只清理本地 source 与 Workspace 记录，不自动删除共享 Modal Volume 中的 canonical/GLB artifact。

GLB 导出使用桌面原生保存链路：Agent 从 Modal Volume 流式写入 app-data export 缓存并验证 `glTF Binary v2`，Tauri 再通过系统保存对话框将已验证文件复制到用户选择的位置。大 GLB 不通过 React/Tauri IPC 传输。

SAM 使用 Provider 模式：`Auto / Cloud / Local`。Cloud SAM 已真实启用；Windows x86_64 + NVIDIA 机器可从客户端后台安装独立 Local SAM runtime。bootstrap 来自固定 GitHub Release，Torch/cu128 使用精确 hash-pinned Windows wheels，3.5 GB checkpoint 使用当前 Modal 凭据从 `modal-3d-sam31-weights` Volume 流式同步并校验 SHA256。Auto 在有效 Local 安装上优先尝试 Local，Local 启动失败时才回落 Cloud。Local runtime 可随时卸载以释放 runtime/checkpoint 空间，同时保留 scene/selection 数据供以后重装后继续使用；版本升级复用同一安装入口，在 staging 中验证完成后再原子切换 runtime，失败不会先破坏旧安装。每个 Project 都持久化实际使用的 `sam_provider`，确保后续 materialize 与 segmentation 来源一致。

Local SAM 默认安装到客户端数据目录下的 `local-sam`（已打包 Windows 版本通常是 `%APPDATA%\com.modal3d.client\local-sam`；状态栏会显示实际绝对路径）。SAM 状态栏会显示 checkpoint 下载百分比、实时 MiB/s 和预计剩余时间；点击“迁移目录”可选择其他磁盘，客户端会在停止 runtime 后安全复制完整目录，确认成功后才切换配置。

Cloud SAM 还支持交互式 Refine：候选不理想时可在原图拖多个正框（保留）和负框（排除），提交后生成新的 selection；确认 candidate 后再 materialize canonical RGBA。

## Windows 开发

首次使用前请安装：

- Node.js 24 或更高版本
- 带有 MSVC 工具链的 Rust stable
- `uv`
- Microsoft Edge WebView2 Runtime（当前 Windows 10/11 通常已经内置）

在 PowerShell 中启动完整桌面客户端：

```powershell
npm install
npm run agent:sync
npm run desktop:dev
```

首次启动会将 Python 本地代理打包为 Tauri sidecar，并自动进行健康检查。后续仅在代理源码、依赖或构建脚本变化时重新打包。客户端打开后会自动启动并探测本地代理。

生成简体中文 Windows 安装包：

```powershell
npm run desktop:build
```

安装包已包含 Python 本地代理，最终用户无需另外安装 Python 或 `uv`。

## 单独开发前端

```powershell
npm install
npm run dev
```

## 单独开发本地代理

```powershell
uv sync --upgrade-package modal
uv run uvicorn agent.main:app --host 127.0.0.1 --port 8765
```

端口 `8765` 仅用于手动开发。由 Tauri 管理的代理会使用随机回环端口和每次启动生成的临时会话令牌。

Modal 版本要求有意不固定；`npm run agent:sync` 会更新锁文件中的 Modal 版本，桌面端构建随后会严格使用该锁文件。

诊断 sidecar 启动问题时，可以通过 `MODAL_3D_AGENT_EXECUTABLE` 指定其他代理可执行文件。代理启动失败时，界面会显示捕获到的 Python 日志。

架构边界和后续里程碑请参阅 [ARCHITECTURE.md](ARCHITECTURE.md)。
