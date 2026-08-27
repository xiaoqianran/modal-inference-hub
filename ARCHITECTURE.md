# 架构

## 1. 选择

这是 Experiment-oriented Modular Monolith。代码沿变化轴组织，每个垂直切片包含
自己的状态转换、持久化和 HTTP 动作；没有 controller/service/repository 横向目录。

~~~text
┌──────────────────── modal-inference-hub ────────────────────┐
│                                                            │
│  App.tsx                    人工实验 UI                     │
│     │                                                      │
│     ▼                                                      │
│  app.py                     组合根 / HTTP 外壳              │
│     │                                                      │
│     ├─ experiments.py       Prompt → 候选 → 人选 → 3D       │
│     ├─ direct_images.py     用户图片 → 3D                   │
│     ├─ batches.py           有界调度，只引用子 Run           │
│     ├─ deployments.py       Provider 部署运行投影            │
│     └─ sidecars.py          深接口：HTTP/认证/Artifact       │
│                                                            │
└────────────────────────────────────────────────────────────┘
                  │                         │
                  ▼                         ▼
          modal-2D-client             modal-3D-client
          execution owner             execution owner
~~~

## 2. Functional Core / Imperative Shell

~~~python
# 纯函数：相同输入永远得到相同输出，不读 DB、不发网络请求
selected = select_candidate(experiment, candidate_id, timestamp)

# 指令式外壳：先保存 intent，再执行可能失败的副作用
store.save(plan_asset3d(...))
png = image_sidecar.artifact(image_job_id)
job = asset3d_sidecar.submit_asset3d(png, ...)
~~~

状态不是多个表之间的隐式协调结果。一个 Experiment document 是聚合边界；SQLite 使用
乐观版本号阻止静默覆盖。

Batch document 是另一个聚合边界，但只保存成员关系、调度状态与目标引用，不嵌入
Experiment、DirectImage Job 或 Artifact。用户上传图片进入内容寻址 InputStore；它属于
Caller input，不冒充 Provider Artifact。

## 3. 状态与副作用流程

~~~text
Create Experiment
      │
      ├─[先持久化 planned candidates]
      │
      ├─ submit 2D job（确定性 ID，可恢复）
      │       └─ 只记录 ExecutionProjection + ArtifactDescriptor
      │
      ▼
Human Selection                  # 人的语义判断属于 Hub
      │
      ├─[先持久化 3D intent]
      │
      ├─ GET selected PNG         # 不进入 Hub Artifact cache
      ├─ POST modal-3D-client
      │
      ▼
GLB Artifact                     # 仍不是 AgentScape Asset / World
~~~

uncertain 是一等状态。Hub 丢失 submit 响应时使用同一稳定 job ID 查询 Sidecar，不把
网络超时错误地折叠为 failed，也不发明全局 Job ID。

批处理流程：

~~~text
Prompt Batch                         Image Batch
    │                                    │
    ├─ item → Experiment ref             ├─ SHA-256 Input ref
    ├─ item → Experiment ref             ├─ item → DirectImage Run ref
    └─ 到候选完成后 awaiting_review       └─ item → DirectImage Run ref

BatchService 使用单一 Provider semaphore 顺序提交；不会用无界 Promise.all 把付费任务
同时推向 GPU。每个子目标 ID 由 batch + ordinal 确定，崩溃恢复不会换 Job ID。
~~~

Provider 部署流程：

~~~text
用户确认 + 临时凭据
        │
        ▼
Hub Deployment projection
        │ 只调用 provider-owned deployer
        ├──────────────► modal-2D/deployment.py ─► deploy ─► verify capabilities
        └──────────────► modal-3D/deployment.py
                              ├─ rembg
                              ├─ workers + register
                              ├─ gateway
                              └─ capabilities + adapter revision gate
~~~

## 4. 信息隐藏与“不复制 Schema”

- Hub 不 import modal，也不调用 Modal Function。
- Hub 不拥有 Modal CLI argv 或 3D Worker 清单；它只定位并启动 Provider 自有 deployer。
- Hub 不定义 Provider capability/model/options 的镜像 Pydantic 模型。
- /api/providers 只转发 Provider model descriptors；实验保存 provider-namespaced ID。
- 跨仓只读取 status/result.artifact/result.conditioning 这一最小稳定投影。
- Sidecar URL、会话 Header、JSON 与 multipart 细节被 SidecarClient 隐藏。
- 前端只识别两种最小 Token 导入格式，不实现或执行 Modal CLI parser。
- Modal token 只在连接/部署请求中瞬时使用；部署时通过官方环境变量进入子进程，Hub 不落盘。

## 5. Single-file First 与 Extract by Pressure

当前达到压力阈值的抽取点：

1. sidecars.py：2D/3D 共享网络、认证、错误语义，重复会造成协议漂移。
2. GlbViewer.tsx：Three.js 的 WebGL 生命周期和包体压力已经与普通 UI 不同；采用动态
   import，避免首屏加载 3D runtime。
3. GLB 下载：达到内存压力边界后改为 1 MiB 分块透传；Hub 不缓存完整 GLB。
4. direct_images.py：用户输入持久化、完整性与直接 3D Run 有独立生命周期。
5. batches.py：成员/背压/恢复是独立变化轴；它引用子 Run，不复制其状态所有权。
6. deployments.py：长时间 subprocess、凭据安全和部署恢复与生成实验生命周期不同。

新的实验功能应先进入一个高内聚文件。只有出现多人冲突、明显性能边界或深接口可以隐藏
大量知识时才拆分；不会按名词预建微服务。

## 6. State Ownership

| 事实 | Owner |
|---|---|
| prompt、候选集合、人工选择、实验历史 | Hub |
| Batch membership、背压策略、用户上传输入 | Hub |
| Provider 部署清单、Modal CLI argv、健康门 | modal-2D / modal-3D |
| 本地部署运行投影 | Hub |
| Provider execution / retry / remote call | 对应 Sidecar / Provider |
| Artifact content identity | Producer |
| Input conditioning | modal-3D |
| Asset admission / World truth | AgentScape |
