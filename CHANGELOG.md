# Changelog

## 0.1.0（2026-09-17）

首版本地候选（P0–P7 完成，待独立验收 A0 与实机 A1）。

### 独立功能（不依赖协作插件）

- 私聊双方手动偏好档案：喜欢/中立/不喜欢/明确禁止、主动/接受/双向、
  强度 1-5、内置与自定义标签、备注（仅数据）。
- `/xp`（`/偏好`）命令组：help/status/on/off/show/set/remove/clear，
  管理员 admin switch/stats/template。
- 管理员总开关默认关闭；用户需本人 `/xp on`；群聊仅通用引导。
- 身份四元隔离（平台实例、机器人、最终人格、真实 sender），
  persona 解析失败保守跳过。
- SQLite 持久化：事务、参数化、revision 乐观锁、epoch 失效语义；
  clear 一次性确认与如实删除边界说明。
- 请求注入：priority=20 钩子 + extra_user_content_parts +
  mark_as_temp；幂等、有预算、不覆盖其他内容；零额外模型调用。

### 已验证联动（需配套补丁 / 只读依赖）

- Relation Arc 只读快照（模式 ro 直读其 SQLite）：关系暂停/放缓
  压制偏好强度；缺失/异常保守不生效。
- Context Bridge 捕获前排除协议 `pref_profile_turn_exclusion_v1`
  （patches/0001，基线 d8a7147）：偏好启用私聊轮次的输入/回复/工具
  轨迹不进入跨会话共享账本。

### 保守降级

- uctx 在场但未打补丁 → 私人偏好注入禁用（命令层说明）。
- Relation Arc 缺失/表异常/无该身份数据 → 关系限制不生效，独立偏好
  可用。

### 边界

- 已送模型内容不可撤回；宿主聊天/其他插件共享库/备份不由本插件清除。
- v1 不自动学习、不训练模型；性别不参与任何决策。
