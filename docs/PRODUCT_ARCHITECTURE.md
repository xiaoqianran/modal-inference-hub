# 产品流程

~~~text
┌────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐
│ 写入 Prompt │ -> │ 并行 2D 候选  │ -> │ 人工比较/选择 │ -> │ 生成 3D   │
└────────────┘    └──────────────┘    └──────────────┘    └───────────┘
                         │                    │                  │
                   Sidecar jobs         Human evidence      GLB Artifact
~~~

UI 不显示 Modal CLI 参数表，也不维护上游 Schema 副本。可用模型和 profile 来自 Sidecar
运行时发现；实验只保存当次实际选择，以便复现与比较。

成功层级保持分离：

~~~text
Provider execution succeeded
            ≠ Artifact structurally valid
            ≠ AgentScape Asset admitted
            ≠ World ready
~~~

当前产品终点是“可下载、由 Sidecar 验证过的 GLB Artifact”。未来发布到 AgentScape 必须
作为新的垂直切片加入，不能把 Provider success 直接改名为 ready Asset。
