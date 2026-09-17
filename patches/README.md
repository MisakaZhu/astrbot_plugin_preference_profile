# 协作补丁

## 0001-uctx-turn-exclusion-protocol.patch

- 目标插件：astrbot_plugin_user_context_bridge
- **精确基线**：d8a7147e2a43c37b83781b92774afae2a254be60（0.2.0 返工候选；
  规划时基线 8477eba 已被该项目独立验收返工推进，补丁以执行时最新
  HEAD 为基线制作）
- 改动范围（仅 15 行新增，无删改）：
  - `main.py`：模块级常量 `UCTX_EXCLUDE_PROTOCOL` /
    `UCTX_EXCLUDE_EXTRA_KEY`（供 astrbot_plugin_preference_profile 的
    BridgeGuard 通过 star_map 探测协议可用性）
  - `uctx_bridge/bridge.py`：`handle_llm_request` 开头检查 event extra
    排除标志，命中即 `return False`（不 begin_turn、不改 contexts、
    不置 conversation=None；后续 agent_done / decorating_result 因无
    PendingTurn 自然短路）——偏好私聊轮次的输入/回复/工具轨迹全程
    不进入跨会话共享账本
- 应用：
  ```bash
  cd astrbot_plugin_user_context_bridge
  git apply --check ../astrbot_plugin_preference_profile/patches/0001-uctx-turn-exclusion-protocol.patch
  git apply ../astrbot_plugin_preference_profile/patches/0001-uctx-turn-exclusion-protocol.patch
  ```
- 回滚：`git checkout -- main.py uctx_bridge/bridge.py` 或 `git apply -R <补丁>`
- 回归证据：tests/p5_integration_check.py（本仓库）双宿主 venv 各 23/23，
  覆盖：偏好启用私聊轮次不接管/不入账本；普通轮次正常接管与提交；
  私聊→群聊隔离；off 后恢复共享；重试不双注；未打补丁时保守禁用注入
  （V16）。隔离验证于 `.tmp_uctx_isolation`（git clone 副本，原目录未动）。
- 补丁 SHA-256：见 SHA256SUMS.txt
