# 架构说明

`modal-3D-client` 是面向用户的客户端，云端模型代码继续保存在 `modal-3D` 仓库中。

```text
React / TypeScript
        │
      Tauri 2
        │
  本地 Python 代理
     ├─ 硬件检测
     ├─ Modal Session / Artifact Volume
     ├─ Cloud SAM / Generation Jobs
     ├─ 可选的本地 SAM 3.1（后续）
     └─ 云端 3D 工作节点
```

## 架构边界

- 用户界面只在输入和连接期间接触凭据，不会持久化或重新读取已保存的密钥。
- Tauri 负责桌面应用生命周期和 Windows 凭据管理器存储。
- Python 本地代理负责 Modal Session、Artifact、Cloud SAM 与长期 Generation Job 编排；Job 元数据持久化到 SQLite，可跨 Agent 重启恢复 Modal FunctionCall。
- 本地代理仅监听随机的 `127.0.0.1` 端口；Tauri 会为每次启动提供独立会话令牌。
- 已保存的凭据直接恢复到本地代理，不会回传到 React 界面。
- 本地 SAM 3.1 是可选功能，云端回退使用相同的界面协议。
- `modal-3D` 是云端工作节点及其 API 协议的唯一事实来源。
- 大型模型不会打包进安装程序。

## 首批里程碑

1. ✅ Tauri 使用随机端口和独立会话令牌启动、停止本地代理 sidecar。
2. ✅ 通过本地代理连接 Modal 令牌；凭据仅在当前代理会话的内存中保存。
3. ✅ 使用 Windows 凭据管理器持久化 Modal 凭据，并直接恢复到本地代理。
4. ✅ 打通 Cloud SAM → 候选对象 → Canonical RGBA → 3D Generation → GLB 的真实工作流。
5. ✅ 四个云端模型进入统一 registry / recommended profile，并加入按需加载的 Three.js GLB Viewer。
6. 增加 `SAM：自动 / 本地 / 云端` 模式与更完整的能力检测。
7. ✅ Generation Job 使用 SQLite 持久化并支持 Agent 重启后恢复。
8. 增加项目工作区、Local / Auto SAM 与后续 Web Runtime。
