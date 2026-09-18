# Changelog

## 0.1.4（2026-09-18 四轮返工候选）

修复 Codex 四轮复核（c77dd0d）确认的 T2a/T2b/T5：

- T2a：推式失效——存储写入回调 + ObservableConfig 配置写回调在
  内置压缩等待窗口与低优先级后续钩子等待中立即清理在途内容，
  不再依赖下一个钩子检查点。
- T2b：terminate 先清理再关库；校验异常 fail-closed；插件激活状态
  纳入失效判定（真实 turn_off_plugin 场景关闭）。
- T5：注入块以完整文本+对象身份为归属凭证，同标题不同尾文的
  用户引用与其他插件内容不再被误删。
- 新增 tests/u_rework_check.py（真实 PluginManager.load 完整生命周期，
  9 断言双 venv 9/9，旧候选 2/9）；lifecycle_repro 独立副本双版本
  17 场景全绿；全量 13 脚本双 venv 各 237 PASS。

## 0.1.3（2026-09-18 三轮返工候选）

修复 Codex 三轮复核（8de2365）确认的 T1–T4：

- T1：删除 ExpirableTextPart（曾覆盖宿主全局 text 类型注册，导入即
  污染普通文本序列化）；失效机制改为固定前缀识别 + 钩子时机清理。
- T2：新增 on_agent_begin(priority=-1000) 真实 Agent 钩子，覆盖
  reset 后、首次 Provider 调用前的失效窗口（clear/管理员关闭）。
- T3：损坏/非法 state_json、scope 配置类型错一律降级不可用。
- T4：ACCEPTANCE.md 从干净基线重建（原 4.8MB 重复插入已清除）。
- 新增 tests/t_rework_check.py（16 断言）；全量 12 脚本双 venv 各
  228 PASS。p7 边界措辞按实际覆盖范围修正。

## 0.1.2（2026-09-17 二轮返工候选）

修复 Codex 二轮复核（0491cc9）确认的 R3/R4/R5 剩余分支：

- R4：4.26 生产注入入口接入 provider_settings（按 UMO 会话作用域）；
  UMO 作用域配置同时约束管理命令；会话读取失败拒绝私人档案操作
  （不再落默认人格）。
- R5：管理员总开关/个人 off/clear/epoch 在追加前与追加后未发送时
  双重失效（收尾钩子 + ExpirableTextPart 发送序列化时刻校验）；
  其他插件块不受影响。
- R3：读取 Relation Arc 真实生效 scope（config.json 单选）；未知
  schema（PRAGMA user_version）与配置无法确认按合同降级。
- 新增真实加载生命周期证据（PluginManager.load 完整加载/注册/
  实例化、CommandFilter+call_handler 完整分发、真实 stop_event 取消、
  registry 停用过滤）；统计订正为 9 脚本 190/版（此前误报 194），
  现为 11 脚本 211/版。

## 0.1.1（2026-09-17 返工候选）

修复 Codex 独立复核（0c32cee）发现的 R1–R6：

- R1 群聊被当私聊（绑定方法真假值恒真）→ 统一真实方法判定。
- R2 正式入口共享守卫未接通（空参 BridgeGuard 恒 no_bridge）→
  接入真实 star_map、探测实时化、标志写入失败保守禁注入。
- R3 漏读管理员 interaction_safety → 对齐 effective_interaction_safety
  语义（base/timed 取高、纯只读、双 scope）。
- R4 命令与请求人格不一致 → 读取当前选中会话 + 4.26 provider_settings。
- R5 epoch 读后未校验 → 注入提交前重新校验，off/clear 使未提交快照失效。
- R6 方向无序 → 九组合有序真值表，互换产生不同指导。

新增 tests/r_rework_check.py（真实事件/CommandFilter/call_handler/
正式构造/装卸，双 venv 25/25）；Codex 六组反例在新副本上 R1/R2/R3/R5/R6
翻绿（双版本），R4 见 HANDOFF 替身差异说明。

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
