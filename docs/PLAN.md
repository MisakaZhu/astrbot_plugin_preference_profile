# PLAN — astrbot_plugin_preference_profile

目标：独立 AstrBot 插件，私聊双方手动偏好档案（喜欢程度/禁忌/方向/强度/节奏）、
本人权限与关闭删除、Relation Arc 只读联动、偏好私聊不进入跨会话共享。
完整行为合同见 Linear 主计划（实施与验收计划 v1）与本仓库 docs/ADR.md。

## 阶段（对齐 Linear MIS-146..153）

| 阶段 | Linear | 内容 | 状态 |
| --- | --- | --- | --- |
| P0 | MIS-146 | 冻结合同、初始化 Git、真实宿主验证关键路径 | 完成（本文件记录时） |
| P1 | MIS-147 | 身份、双向档案模型与 SQLite 持久化 | 待做 |
| P2 | MIS-148 | 偏好/禁忌/方向/强度决策引擎 | 待做 |
| P3 | MIS-149 | 私聊命令、权限、关闭/删除生命周期 | 待做 |
| P4 | MIS-150 | 真实请求钩子接入、临时提示、重试去重 | 待做 |
| P5 | MIS-151 | Relation Arc 只读联动 + Context Bridge 排除补丁 | 待做 |
| P6 | MIS-152 | V01–V20 全矩阵、双版本宿主、组合回归 | 待做 |
| P7 | MIS-153 | 打包 ZIP/SHA-256、补丁、HANDOFF 交接 | 待做 |

A0（MIS-154 Codex 独立验收）、A1（MIS-155 实机）不在本轮自动执行范围。

## 模块规划

```
astrbot_plugin_preference_profile/
├── main.py                    # Star 入口：注册钩子与命令组，生命周期
├── metadata.yaml / _conf_schema.json
├── pref_profile/
│   ├── __init__.py
│   ├── identity.py            # 四元身份、persona_scope 解析（ADR-002）
│   ├── store.py               # SQLite：entries/user_state/meta（ADR-006）
│   ├── policy.py              # 决策引擎（ADR-007）
│   ├── prompt_builder.py      # 受限指导文本生成与预算
│   ├── commands.py            # /xp 命令组实现
│   ├── bridge_guard.py        # uctx 排除协议协商（ADR-004）
│   └── relation_snapshot.py   # Relation Arc 只读快照（ADR-005）
├── tests/                     # 各阶段真实宿主检查脚本（脱网、合成数据）
└── docs/                      # PLAN/STATUS/ADR/ACCEPTANCE/COMPATIBILITY
```

## 本地执行约束（脱敏后仍适用的部分）

- 只读参考：astrbot_plugin_relation_arc（HEAD 913ca59）、
  astrbot_plugin_user_context_bridge（HEAD 8477eba），执行前已重查一致。
- 共享 venv（4.28.0 / 4.26.0）只读使用，不安装新依赖。
- 协作改动一律在隔离副本验证，以补丁交付；不部署云端、不 push、不真机调用。
