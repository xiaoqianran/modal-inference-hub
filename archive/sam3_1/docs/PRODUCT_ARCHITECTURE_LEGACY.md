# modal-3D Studio 产品与客户端架构

本文以 `modal-3D` 的 `modal-3D.capabilities.v1` 为唯一云端契约，描述 Windows 客户端的产品信息架构、状态边界、接口映射和 UX 验收标准。

## 1. 端到端架构

```text
┌──────────────────────────── Windows Desktop ────────────────────────────┐
│                                                                        │
│  ┌──────────── React / TypeScript ────────────┐                        │
│  │                                            │                        │
│  │  Workspace State       Runtime Controller │                        │
│  │  ├─ Project            ├─ Agent lifecycle │                        │
│  │  ├─ SAM selection      ├─ Modal session   │                        │
│  │  ├─ Canonical RGBA     ├─ Capabilities    │                        │
│  │  └─ Generation Job     └─ Local SAM task  │                        │
│  │          │                       │         │                        │
│  └──────────┴───────────┬───────────┴─────────┘                        │
│                         │ localhost + per-session token                │
│  ┌──────────────────────▼───────────────────────────────────────────┐  │
│  │ Python Agent                                              FastAPI│  │
│  │ ├─ Project Store (SQLite + source files)                         │  │
│  │ ├─ Job Store (SQLite + Modal FunctionCall ID)                    │  │
│  │ ├─ SAM Provider Router ────────┬──────── Cloud SAM               │  │
│  │ ├─ Artifact / Export           └──────── Local SAM child process │  │
│  │ └─ Modal Client + capability cache                               │  │
│  └───────────────────────────┬──────────────────────────────────────┘  │
│                              │ Modal authenticated RPC                 │
└──────────────────────────────┼─────────────────────────────────────────┘
                               │
             ┌─────────────────▼────────────────── Modal Cloud ─────────┐
             │                                                         │
             │  modal-3D-sam31          modal-3D-gateway               │
             │  ├─ segment              ├─ capabilities                │
             │  ├─ refine               ├─ submit                      │
             │  └─ materialize          └─ FunctionCall task           │
             │                                   │                     │
             │                        dynamic model registry            │
             │                         ├─ FastSAM3D++                   │
             │                         ├─ Hermite-TRELLIS2++            │
             │                         ├─ Hunyuan2.1++                  │
             │                         └─ Pixal3D / future workers      │
             └─────────────────────────────────────────────────────────┘
```

核心原则：React 不接触 Modal SDK、不保存密钥、不硬编码模型；Agent 不复制模型 registry；Worker 不感知 Local SAM。

## 2. 云端契约映射

```text
modal-3D.capabilities.v1
│
├─ generation
│  ├─ app: modal-3d-gateway
│  ├─ submit_function: submit
│  ├─ job_transport: modal.FunctionCall
│  └─ HTTP mirror: /tasks /pipelines /tasks/{id}
│
├─ models[]                         ← Agent /v1/models 动态投影
│  ├─ id / name / description
│  ├─ output: geometry | textured
│  ├─ profiles[]                    ← UI 只展示服务端声明的 profile
│  ├─ options schema                ← Agent 展开并校验，UI 不拼参数
│  └─ reference.warm_seconds
│
└─ sam.cloud
   ├─ segment
   ├─ refine
   └─ materialize                   ← canonical RGBA 统一入口
```

客户端 Agent 对 React 暴露的产品 API：

```text
System       GET  /health
             GET  /hardware
             GET  /v1/capabilities

Connection   GET  /modal/status
             POST /modal/connect
             DEL  /modal/connect

Settings     GET  /v1/settings/sam
             PUT  /v1/settings/sam

Local SAM    GET  /v1/local-sam/status
             POST /v1/local-sam/install
             DEL  /v1/local-sam/install
             POST /v1/local-sam/start
             DEL  /v1/local-sam/start
             PUT  /v1/local-sam/location

Projects     POST /v1/projects
             GET  /v1/projects[/{id}]
             DEL  /v1/projects/{id}
             POST /v1/projects/{id}/segment
             POST /v1/projects/{id}/refine
             POST /v1/projects/{id}/materialize
             POST /v1/projects/{id}/generation

Jobs         GET  /v1/jobs/{id}
             DEL  /v1/jobs/{id}

Artifacts    GET  /v1/assets
             POST /v1/exports
```

## 3. 状态所有权

```text
                   ┌──────── owns ────────┐
React Workspace ───┤ 当前交互、预览 URL   │
                   └─────────┬────────────┘
                             │ derived from
Agent Project DB ────────────┤ source → selection → canonical → job → GLB
                             │ references
Agent Job DB ────────────────┤ local job ID ↔ Modal FunctionCall ID
                             │ polls
Modal FunctionCall ──────────┤ running → result / failure / cancelled
                             │
Agent Settings JSON ─────────┤ sam_mode + local_sam_root
                             │
Windows Credential Manager ──┘ Modal token (never returned to React)
```

不能把这些状态压成一个 `busy`：作品处理、账户连接和 Local SAM 安装互不应锁死。运行时控制器因此只在“发起动作”的短窗口加锁；后台安装由独立轮询恢复。

## 4. 项目状态机

```text
[没有项目]
     │ import image
     ▼
  [draft] ── segment/refine ──► [segmented]
                                   │ choose candidate + materialize
                                   ▼
                                [ready]
                                   │ submit gateway task
                                   ▼
                              [generating/running]
                               │       │        │
                 disconnect ───┘       │        └── cancel_requested
                    ▼                   │
          [connection_required] ────────┘ reconnect + poll
                               │
                  ┌────────────┼────────────┐
                  ▼            ▼            ▼
             [succeeded]    [failed]   [cancelled/expired]
                  │
                  ▼
             native GLB export
```

UI 必须显示“下一步需要什么”，而不是只给一个灰按钮。工作台进度条和每个动作下方的 guidance 都由上述状态派生，不另存重复状态。

## 5. SAM Provider 与 Local Runtime

```text
SAM mode
│
├─ auto  ── Local installed + healthy? ── yes ─► Local
│                   │
│                   no / startup failed
│                   ▼
│              Modal connected? ───────────────► Cloud
│
├─ cloud ── Modal connected? ──────────────────► Cloud / explicit error
│
└─ local ── Local installed + healthy? ────────► Local / explicit error
```

模式是“用户偏好”，可保存但可能暂时不可用，所以模式选项不应因环境缺失而永久禁用；UI 在同一位置给出可用性和修复动作。

```text
Install request (short UI lock)
          │
          ▼
  bootstrap download ─► Torch/CUDA install ─► checkpoint sync ─► health
          │                    │                      │              │
          └──────── status.json + speed/bytes/ETA ───┴──────────────┘
                                   │
                      Runtime Controller polls independently
                                   │
                ┌──────────────────┴──────────────────┐
                ▼                                     ▼
          ready + Local usable                 error + retry guidance
```

关闭设置不终止安装；Cloud SAM 和作品浏览仍可继续。迁移、卸载、停止 Agent 只在安装线程活跃时禁用，以保护文件一致性。

## 6. 设置中心信息架构

```text
设置
├─ Modal 账户
│  ├─ 连接状态
│  ├─ Token 输入 / Windows 安全保存
│  ├─ 模型发现结果
│  └─ 断开 / 删除凭据
│
├─ SAM 推理
│  ├─ Auto / Cloud / Local 偏好
│  ├─ 当前实际 Provider
│  ├─ GPU / VRAM / 磁盘前置条件
│  ├─ 安装进度、速度、ETA
│  └─ 目录 / 验证 / 更新 / 卸载
│
└─ 高级与诊断
   ├─ Agent 启停
   ├─ 平台、内存、GPU、端口
   ├─ 数据路径
   └─ 状态刷新与恢复顺序
```

账号是用户任务，Agent 是实现细节。因此 Agent 控制放在“高级”，但依赖失败时在当前页面直接提供“启动服务”恢复入口，避免让用户猜应该去哪一页。

## 7. 用户痛点与约束

| 用户会难受的地方 | 原因 | 产品约束 |
|---|---|---|
| 一排灰按钮，不知道为什么 | 前置条件只写在 `disabled` | 每个不可执行动作必须同屏说明原因和修复入口 |
| 设置操作失败，却在背后的工作台提示 | 消息状态归属错误 | 设置动作只在设置中心反馈 |
| 下载几 GB 时整个设置锁死 | 单一全局 busy | 后台任务与短动作分离 |
| 连接成功后设置突然关闭 | 程序替用户决定流程 | 设置保持打开并明确确认成功 |
| Local 按钮因未安装而不能选择 | 把偏好与可用性混为一谈 | 偏好可保存，可用性单独展示 |
| 重启后不知道任务是否还在 | 只展示瞬时 React 状态 | Project/Job 由 Agent DB 恢复并继续轮询 |
| 模型列表失败就像账号也失败 | 多接口被 `Promise.all` 绑定 | Modal session、capability、model list 分级降级 |
| 英文/raw status 暴露给普通用户 | 后端状态直接渲染 | UI 映射为中文任务语言，诊断页保留技术信息 |

## 8. 可扩展边界

新增 3D Worker 只需注册 capability；客户端不改模型枚举。新增 SAM Provider 时扩展 Agent Router 和 capability，Workspace 仍消费统一 selection/canonical schema。新增设置项进入对应领域控制器，不把副作用重新塞回 `App.tsx`。

验收底线：所有可见控件要么可执行，要么在同屏解释原因；所有长任务可离开页面后继续；所有云端任务可在 Agent/客户端重启后恢复；任何凭据和本地会话令牌都不得写入日志或 React 持久化存储。
