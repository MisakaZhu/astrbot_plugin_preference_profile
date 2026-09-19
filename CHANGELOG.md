# Changelog

## 0.1.11（2026-09-19 十一轮定向修复候选）

修复 Codex 第十一轮复核 M1/M2/M3（宿主来源映射原型 v2）：

- M1：归属通道在 on_agent_begin 一次性锁定（映射含本插件源对象即
  identity），锁定后原块被他人移除/置空也不回退令牌匹配。
- M2：宿主补丁 v2 恢复 extras 快照重建语义；改为 model_validate 后在
  请求私有内存属性建立 源对象→最终运行时实例 映射（不进序列化/
  Provider 参数/历史）。
- M3：`_blank_record` 源对象置空+`_source_invalidated` 标记；宿主映射
  绑定处补偿置空最终实例（组装等待中 clear/正式停用均生效）。
- 新增 tests/m_rework_check.py（M1/M2/M3+对照）：旧组合（6d73bb5+v1
  补丁）真实行为失败（副本误删/快照改变/偏好送达），新组合双版 4/4。
- 验证：隔离副本 boundary 八场景/token_repro 12/历史 50/映射探针/
  evidence_quality 双版全过；原生宿主 16 脚本 264 保持、7 阻与
  m_rework M1/M3 阻如实保留。单一入口 rebuild_and_test.py 双版 ALL_OK。

## 0.1.10（2026-09-19 十轮宿主来源映射原型候选）

按第十轮裁定在隔离宿主源码副本验证「源对象→最终运行时对象」最小映射：

- 宿主最小补丁（原型 diff，未修改已安装宿主）：assemble_context 重构出
  assemble_context_with_extra_pairs（构造处显式建立 源对象→序列化块
  配对）；Runner _finalize_extra_pairs 在 model_validate 前把块替换回
  源实例（校验器保留分支生效→运行时对象与源对象同一）。
- 插件 _blank_runtime_parts：身份命中（宿主直通）→ 仅按对象身份失效并
  停用令牌回退；未命中（原生宿主）→ 回退令牌，行为与 0.1.9 一致。
- 验证：隔离宿主 token_repro 12 项双版 0 defect（七误删全部消失）、
  历史 50 场景双版 0 复现、直通事实探针双版全过；原生宿主 16 脚本双版
  264 保持、token_repro 7 受阻如实保持。宿主补丁 diff 与原型报告见
  偏好管理-宿主来源映射原型-20260919/。

## 0.1.9（2026-09-18 九轮证据收尾候选）

Codex 第九轮裁定 T7 通过、T5b 接口依赖受阻已接受（未修）。收尾：

- E1：x_rework BudgetProvider 改返回父类真实 LLMResponse（修复前四
  用例双版 AgentState.ERROR/role=err 假 PASS）；断言 role=assistant、
  预期回复、正常完成与记录释放。
- E2：token_repro 第九轮适配版双版完整 12 项（允许无注入记录、
  Provider 侧核预算、140 对照强制完整注入、正常模型终态）。
- 文档：ACCEPTANCE 统计 16 脚本每版 264 并分列「通过/未修受阻/
  接口事实」；源码与 ADR 过宽归属措辞收窄；新增
  docs/HOST_INTERFACE_PROPOSAL.md 与 instance_retention_probe。

## 0.1.8（2026-09-18 八轮返工候选）

处理 Codex 八轮复核（abdba20）两项 P2：

- T7 已修：max_inject_chars 按合同是单轮注入总字符上限，最终表示中
  的全部模型可见字符（含 14 字归属标识）计入预算——注入前以
  「上限−标识长」渲染，预算不足按既有规则丢尾部条目，放不下则不
  注入。旧候选上限=主体长时送达 140>126，新候选 ≤126。
- T5b 剩余七场景（finalize 后 -2000 请求钩子 / AgentBegin 各时机
  合法复制的同文 temp 副本被误删）**受阻**：转换链双版本实测证明
  extra part 经 model_dump_for_context→Message.model_validate 重建
  后对象身份丢失，TextPart 模型字段仅 type/text，私有属性不入
  dump（仅宿主特判 _no_save），同文复制副本与本尊在全部可观测字段
  上不可区分——内容级/属性级判据均被合法复制继承，现有宿主 Part
  接口无法承载「复制不继承所有权」的关联。保持未修，交受阻证据与
  最小宿主支持方案（见 ADR 八轮小节），不引入合同禁止的启发式。
- 新增 tests/x_rework_check.py（X1-X5+受阻声明；旧 abdba20 上 X1
  行为性失败 140>126，新候选双版 8/8）。16 脚本双 venv 各 264 PASS。
- 文档订正：composition 历史基线归属（a2378c6 每版 1 缺陷、f77d345
  每版 6、abdba20 为 0）；-1000 相对排序（非链末尾）措辞统一；
  ACCEPTANCE 标题/V11/p7·L4 重复拼接；STATUS 包路径与历史统计标注。

## 0.1.7（2026-09-18 七轮返工候选）

修复 Codex 七轮复核（f77d345）确认的 T5b 可靠归属：

- T5b：组装后归属改为每轮唯一令牌（「〔偏好标识<hex>〕」嵌入文本
  尾部），运行时清理只按本轮令牌子串+_no_save 匹配；弃用位置映射
  （part_index 恒等于 extra_count-1，九场景 6 缺陷复现）。
- finalize 令牌轮换：请求钩子链中相对靠后的 -1000（非链末尾）按
  对象身份轮换存活块令牌，更早产生的同文副本（持旧令牌）失效清理
  时不被误删。
- registry：TurnRecord tokens 槽位替代 part_index/extra_count；
  dict[id(event)]+event 弱引用回调自动移除死条目。
- 验证：composition 九场景×双版本全绿（适配探针在旧候选 a2378c6
  重现 6 缺陷，与 Codex 冻结报告一致）；terminal 8/ownership 4/
  lifecycle 17 场景×双版本全绿；全量 15 脚本双 venv 全绿（v/w 夹具
  按等价关系「去令牌主体一致」适配）。

## 0.1.6（2026-09-18 六轮返工候选）

修复 Codex 六轮复核（a2378c6）确认的 T5b/T6a/T6b：

- T5b：组装后归属改为位置映射（assemble_context 顺序索引+全文+
  _no_save 三重校验）；同文同 temp 的其他插件块不再被误删。
- T6a：装饰阶段仅回收已失效/未挂接记录，多步 Agent 中间回复后
  仍可失效，第二次模型调用无旧偏好。
- T6b：执行轮次 task 的 done 回调释放（取消/err 终态）+ event 弱
  引用自动回收（stop 中止）。
- 新增 tests/w_rework_check.py（真实工具/装饰/取消/同文 temp，
  9 断言双 venv 9/9，旧候选 6/9）；terminal_repro 双版 8 场景全绿；
  全量 15 脚本双 venv 各 256 PASS。

## 0.1.5（2026-09-18 五轮返工候选）

修复 Codex 五轮复核（a06a5e4）确认的 T5a/T5b/T6：

- T5a：finalize 正常/异常分支改按登记对象身份移除；删除前缀函数，
  全部清理入口统一归属规则。
- T5b：运行时清理统一「全文+_no_save 临时标记+数量上限」；同文
  用户输入与其他插件普通块保留，本插件 temp 块精确失效。
- T6：on_agent_done / on_decorating_result 终态释放注册表强引用；
  完成/失败轮次不再无限保留；在途推式失效不受影响。
- 新增 tests/v_rework_check.py（真实加载，10 断言双 venv 10/10，
  旧候选 2/10）；ownership_repro 双版本四反例 0 复现；lifecycle 17
  场景保持全绿；全量 14 脚本双 venv 各 247 PASS。

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
