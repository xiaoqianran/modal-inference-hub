# modal-3D 三维创作客户端

面向 Windows 的本地/云端混合 `modal-3D` 客户端。

## 技术栈

- Tauri 2
- React + TypeScript
- Python 3.12 + FastAPI 本地代理
- 使用 `uv` 管理 Python 环境
- 最新版 `modal[api-proxy-support]`

云端模型工作节点保存在公开的 `modal-3D` 仓库中。本地 SAM 3.1 是可选功能，不会随基础客户端安装。Modal 凭据通过本机代理验证；Windows 版本可将凭据保存到 Windows 凭据管理器，并直接恢复到本地代理，不会重新加载到 React 界面。


## 当前云端 MVP

桌面端已经打通第一条真实纵向链路：

```text
图片 → Cloud SAM 3.1 → 候选对象 → Canonical RGBA
     → FastSAM3D++ → 异步 Job → GLB → 客户端下载
```

React 只调用本地 Agent 的产品 API；Agent 使用当前用户的 `modal.Client` 访问私有 Modal RPC 和共享 `modal-3d-artifacts` Volume。Cloud SAM 产出的 canonical RGBA 直接以 Volume path 交给 3D Worker，不会下载到本地再上传。

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
