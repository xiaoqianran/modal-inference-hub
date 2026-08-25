# Modal 3D Client 架构

## 当前系统

```text
React / TypeScript UI
        │
        ▼
Tauri / Rust Desktop Host
        │  启动本地 Agent、凭据、文件保存、资源目录
        ▼
Python Local Agent (FastAPI, 127.0.0.1 随机端口 + session token)
        │
        ├── Project Workspace / SQLite
        ├── rembg / BiRefNet 本地预处理
        │      ├── GPU 优先（ONNX Runtime CUDA）
        │      └── CUDA 失败自动回退 CPU
        ├── 8-connected Alpha components
        ├── Canonical 1024×1024 RGBA
        ├── Job / SQLite / 重启恢复
        ├── Artifact 校验与本地缓存
        │
        └──────────────► modal-3D / Modal cloud
                           ├── capability registry
                           ├── FunctionCall generation
                           └── GLB artifact
```

## 主工作流

```text
Source Image
   ↓
Project.create
   ↓
Local rembg (GPU preferred, CPU fallback)
   ↓
Alpha Matte
   ↓
8-connected component analysis
   ↓
Local component selection / box selection / undo-redo
   ↓
Canonical RGBA 1024×1024
   ↓
SHA256 + byte count
   ↓
Upload Canonical to Modal Volume
   ↓
Modal FunctionCall
   ↓
Persistent Job polling / recovery
   ↓
GLB validation + content-addressed cache
   ↓
Three.js viewer / native export
```

## 组件职责

### React / TypeScript

- 展示项目、预处理、组件选择、生成进度和 GLB Viewer。
- 通过 `src/agent.ts` 访问本地 Agent HTTP API。
- 不直接调用 Modal，也不持久化 Modal 密钥。

### Tauri / Rust

- 管理桌面应用和 Python Agent 生命周期。
- 从应用资源目录启动 PyInstaller onedir Agent。
- 将 Modal 凭据保存在 Windows Credential Manager，并直接恢复给 Agent。
- 提供原生导出、应用数据目录和诊断能力。

### Python Local Agent

- 持有 Project / Job 状态和 SQLite 持久化。
- 下载并校验 `birefnet-general-lite`。
- 执行本地 rembg、组件分析和 Canonical 生成。
- 维护 Canonical 本地 SHA256 与远端路径的内容绑定。
- 调用 Modal generation，并在本地持久化 FunctionCall ID。
- 对远端 GLB 做格式、大小和 SHA256 校验后进入本地缓存。

### modal-3D 云端

- 是云端 worker 与 capability registry 的事实来源。
- 输入是客户端已经准备好的 Canonical RGBA。
- 不负责 rembg、SAM、主体选择、裁剪或 Canonical 生成。

## 状态与恢复边界

```text
Project
  ├── source descriptor
  ├── canonical descriptor + remote SHA256 binding
  ├── model/profile
  ├── job_id
  └── artifact descriptor

Job
  ├── remote_call_id
  ├── status
  ├── retry/error metadata
  └── artifact_remote_path
```

生成提交采用补偿式一致性：远端调用已创建但 Job 落库失败时尽力取消远端调用；Job 已落库但 Project 绑定失败时持久化取消意图。Job 对 `NotFound` 使用连续确认后再判定 `expired`，避免短暂远端查询异常造成误判。

## 安全边界

- Agent 只监听 `127.0.0.1` 随机端口。
- 每次 Tauri 启动 Agent 都生成独立 session token。
- UI 不读取已保存的 Modal 密钥。
- Canonical 和 GLB 都使用 SHA256/bytes 做内容校验。
- Agent PyInstaller 采用 onedir，避免 onefile 每次启动解压大型 CUDA runtime。

## 已退役实现

旧的 SAM 3.1 cloud/local preprocessing 实现仅保存在 `archive/sam3_1/` 作为历史参考。它不被当前 React、Tauri、Python Agent、CI 打包或运行时导入。当前前景分离统一使用本地 rembg / BiRefNet。
