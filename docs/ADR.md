# ADR — astrbot_plugin_preference_profile

决策记录按阶段追加；已冻结条目变更须新增条目说明替代关系，不原地改写。

## ADR-001 范围与角色冻结（P0）

- 首版仅私聊使用；管理员总开关默认关闭，用户还需本人 `/xp on` 明确开启。
- 两个主体：Bot 人格模板（`owner_kind='bot'`）与用户本人档案（`owner_kind='user'`）。
- 性别仅可选属性字段，不参与方向/强度推断，不作为任何规则的输入。
- 普通用户只能读写自己的档案；Bot 模板与总开关要求宿主管理员权限
  （`@filter.permission_type(PermissionType.ADMIN)`）。
- 不提供批量导出他人档案接口。

## ADR-002 身份模型（P0 冻结）

用户身份四元组与 Context Bridge 的 SharedIdentity 同构，便于跨插件 scope 对齐：

```
user_identity    = (platform_id, self_id, persona_scope, sender_id)
bot_identity     = (platform_id, self_id, persona_scope)
identity_key     = 四元/三元以 \x1f 连接（persona_scope 取宿主最终人格）
```

- `persona_scope` 通过与宿主 `_ensure_persona_and_skills` 同参调用
  `persona_manager.resolve_selected_persona(umo, conversation_persona_id, platform_name)` 获得；
  两版宿主（4.26.0/4.28.0）签名一致（P0-F4 实测）。
- 解析抛异常（宿主故障）→ 本轮跳过注入与隔离标记，不落任何默认人格，
  记原因码 `persona_unresolved`。解析成功（含 "default"）→ 使用返回值。
- `sender_id` 只取 `event.get_sender_id()`，绝不取昵称、消息文本或群历史。

## ADR-003 注入机制（P0 冻结）

- 唯一注入通道：`on_llm_request` 钩子（`priority=20`，先于 Context Bridge 默认
  priority=0 的捕获钩子，P0-F2/F3 实测排序与 extra 传递），
  `req.extra_user_content_parts.append(TextPart(...).mark_as_temp())`。
- 不改 `system_prompt`、不覆盖 `contexts`/`func_tool`/其他插件 parts、
  不改 `unified_msg_origin` 与回复路由。
- 幂等：以 `event_key = f"{umo}#{message_id}"` 记录本轮已注入标记（event extra），
  同一 event 重复进入钩子不重复追加（宿主重试不双注）。
- 预算：单轮注入条目数上限 6、总字符上限 600，超出按优先级截断并记原因码。
- 普通轮次不增加模型调用；v1 无自动学习。

## ADR-004 Context Bridge 捕获前排除协议（P0 冻结，P5 实现补丁）

- 协议名：`pref_profile_turn_exclusion_v1`。
- 偏好插件侧：priority=20 钩子判定"本轮为已启用偏好功能的私聊轮次"时，
  `event.set_extra("uctx_bridge_turn_excluded", {"protocol": "pref_profile_turn_exclusion_v1"})`，
  随后注入偏好指导。
- Context Bridge 侧（隔离副本补丁）：`handle_llm_request` 在 scope 判定前检查上述
  extra 标志；命中即 `return False`（不 `begin_turn`、不替换 `contexts`、
  不置 `req.conversation=None`）。后续 agent_done / decorating_result 因无
  PendingTurn 自然短路，输入/回复/工具轨迹全程不入共享账本。
- 排除后的轮次走宿主原生行为（宿主自身会话历史按宿主规则保存，符合合同）。
- 运行时协商：偏好插件探测 uctx 是否支持本协议（检查其模块导出的
  `UCTX_EXCLUDE_PROTOCOL` 常量）；uctx 在场但不支持 → 该用户本轮不注入私人偏好
  （V16 保守降级，原因码 `uctx_protocol_missing`），并在命令层向用户说明；
  uctx 不在场 → 无需隔离，正常注入。
- 补丁以独立 git 补丁文件交付，标注精确基线 SHA 与哈希；原目录不直接修改。

## ADR-005 Relation Arc 只读快照（P0 冻结，P5 实现）

- 只读打开 Relation Arc 的 SQLite（`mode=ro` URI）；探测 `accounts` /
  `timed_safety` 表与插件版本，任一不符 → 关系限制不生效（原因码
  `relation_unavailable`），独立偏好功能不受影响。
- 身份映射：arc_identity = `f"{platform_id}:{sender_id}"`（对齐其
  `RelationArc._identity`）；scope 映射：同时探测 `session(umo)` 与 `global("")`
  两个 scope 的账户行，按其 `_effective_state` 近似语义合并 timed_safety 与 paused。
- 快照字段：关系状态字典、互动节奏（normal/slow_down/pause_intimacy 中最高有效级）、
  paused、schema 特征、读取时间。
- 绝不写其库、不从中文提示反推状态；`pause_intimacy` 有效时压制亲密偏好
  注入（更严格限制优先），普通偏好不受关系缺失影响。

## ADR-006 存储与生命周期（P0 冻结）

SQLite 单文件于插件数据目录（`StarTools.get_data_dir`），WAL 模式：

```sql
CREATE TABLE entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_kind TEXT NOT NULL CHECK(owner_kind IN ('user','bot')),
  identity_key TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('like','neutral','dislike','forbidden')),
  direction TEXT NOT NULL DEFAULT 'both' CHECK(direction IN ('active','receptive','both')),
  intensity INTEGER NOT NULL DEFAULT 3 CHECK(intensity BETWEEN 1 AND 5),
  note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL CHECK(source IN ('self_declared','admin_template')),
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at REAL NOT NULL,
  UNIQUE(owner_kind, identity_key, tag_id)
);
CREATE TABLE user_state (
  identity_key TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  epoch INTEGER NOT NULL DEFAULT 1,
  updated_at REAL NOT NULL
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

- "未设置" = 无条目，与显式 `neutral` 严格区分。
- 并发：写入事务内 `revision+1` 且 `WHERE revision=?`，冲突返回明确错误码。
- `off`/`clear` 递增 `user_state.epoch`；在途请求携带的 epoch 快照不匹配则不注入
  （慢请求不复活）。`clear` 另删该身份全部 entries 并清缓存，一次性确认令牌
  5 分钟有效。
- 删除边界如实告知：已送模型内容不可撤回；宿主既有聊天、其他插件共享库、
  备份不由本插件清除，仅指向各自的既有清理途径。

## ADR-007 决策顺序（P0 冻结，P2 实现引擎）

每轮按序短路，输出受限结构（原因码 + 最多 6 条指导文本），不输出自由规则：

1. 门禁：总开关、私聊范围、本人 enabled（epoch 校验）、档案非空；
2. 限制：双方 `forbidden` 命中 → 本轮不注入偏好指导（仅注入"存在禁忌需回避"
   的必要提示）；Relation Arc `pause_intimacy`/`slow_down` 生效时压制强度；
3. 指导：双方条目按方向匹配（active/receptive/both）、状态（like 优先）与
   强度上限（取双方更保守值）生成少量中性措辞指导文本；
4. 冲突取更严格；未设置 ≠ 同意；喜欢不覆盖对方禁止；偏好不建立关系、不加分、
   不改内容规则；与当前普通话题无关时不强行引入（由注入文本措辞约束：
   指导仅"调整表达方式"，不"发起话题"）。

## ADR-008 命令面冻结（P0 冻结，P3 按宿主解析微调语法细节）

`/xp` 主命令组 + `/偏好` 中文别名组，同一实现；私聊可用全部子命令，
群聊仅 `help` 返回通用引导（不含任何标签/状态/值）：

```
/xp help | status | on | off
/xp show [页码]
/xp set <标签> [状态] [方向] [强度] [备注]
    状态: like|neutral|dislike|forbid  方向: active|receptive|both  强度: 1-5
/xp remove <标签>
/xp clear            # 一次性确认
/xp admin ...        # 管理员：总开关 / Bot 模板维护
```

- 命令处理结果不进入模型请求（普通命令轮次无 LLM 调用），
  也不得进入共享历史（命令轮次本身通常无 LLM 请求，天然满足；管理命令
  绝不通过 LLM 响应）。
- 标签集合：内置枚举（如 `话题偏好`、`称呼`、`玩笑`、`亲密度` 等中性维度）
  + 自定义短标签（限 2-16 字符、数量上限 20/人），仅作数据。


## 返工修订（Codex 复核 0c32cee 后，R1–R6）

- **ADR-001/008 修订（R1）**：所有入口的私聊判定统一为
  `identity.host_is_private_chat`——真实宿主 `is_private_chat()` 是方法，
  直接 `bool(getattr(...))` 取绑定方法真假值恒真。异常时拒绝私人操作。
  测试替身不得以 `@property` 伪造接口形状（已删除）。
- **ADR-004 修订（R2）**：生产构造必须以真实宿主注册表
  `astrbot.core.star.star.star_map` 构造 BridgeGuard；探测实时化
  （无缓存，装卸/停用后正确；`activated=False` 视为不在场）；
  `protocol_ok` 下排除标志写入失败 → 本轮保守禁注入。
- **ADR-005 修订（R3）**：快照读取 `state_json.interaction_safety` 的
  实际值（管理员 `set_interaction_safety_admin` 写入处），与 timed_safety
  按 `effective_interaction_safety` 同一秩合并（取更高等级）；过期判定
  用纯 SELECT（不调用带 DELETE 副作用的 `active_timed_safety`）；
  global/session 双 scope 各自计算后取全局更严格值。
- **ADR-002 修订（R4）**：命令人格经
  `conversation_manager.get_curr_conversation_id → get_conversation`
  读取当前选中会话 persona_id（与请求轮次 `_get_session_conv` 同源）；
  `resolve_persona_scope` 按宿主签名适配 4.26 的 `provider_settings`
  （自 `context.get_config()["provider_settings"]` 取，不写死默认人格）。
- **ADR-006/003 修订（R5）**：注入块 append 前重新校验本人 enabled 与
  epoch（异步边界后失效）；append 即视为提交，此后不可撤回（如实边界）。
- **ADR-007 修订（R6）**：方向措辞改为有序真值表（user_dir, bot_dir）
  九组合唯一，互换双方方向产生不同指导。


## 二轮返工修订（Codex 复核 0491cc9 后，R3/R4/R5 剩余分支）

- **ADR-002 再修订（R4）**：请求侧（injection）与命令侧同一套人格
  解析——provider_settings 按 `Context.get_config(umo=当前事件)` 取
  **会话作用域**配置（真实宿主 umo 参数决定作用域）；会话读取失败
  （get_conversation 抛错）返回 LookupError → 拒绝私人档案操作，
  与"成功读取且未指定人格（走宿主默认链）"严格区分。
- **ADR-003/006 再修订（R5）**：失效校验统一为 `_still_valid`
  （管理员总开关 + 本人 enabled + epoch）。三道防线：追加前复查；
  finalize_request（priority=-1000，钩子链末尾）按对象身份移除失效块；
  ExpirableTextPart 在宿主发送序列化时刻（两版
  ProviderRequest.assemble_context → model_dump_for_context）动态校验，
  失效序列化为空文本块——不依赖任何后续钩子被执行。已真正发出的
  请求不可撤回（不重定义"提交"）。
- **ADR-005 再修订（R3）**：读取真实生效 scope——解析其
  config.json（config_version ≤ 7 校验 + is_global_relation 单选
  session/global），未启用范围的旧记录不混入；PRAGMA user_version
  必须为已知支持版本（12）；配置无法确认 / 未知 schema / 读取异常
  一律保守不可用。


## 三轮返工修订（Codex 复核 8de2365 后，T1–T4）

- **T1**：禁止定义 TextPart/ContentPart 子类——宿主
  `ContentPart.__init_subclass__` 把子类写入全局类型注册表，仅导入
  即把 text 类型从 TextPart 替换，普通文本被误判失效清空。失效块
  识别改为固定前缀（HEADER 至首个「】」）；清理只发生在钩子时机。
- **T2**：on_agent_begin(priority=-1000) 在 Runner reset 后、首次
  Provider 调用前对 run_context.messages 中已固化的本插件块终检，
  失效置空文本。OnAgentBegin 返回后到首次 Provider 调用之间无插件
  钩子点（接口缺口）。
- **T3**：state_json 解析失败/非 dict/interaction_safety 非法枚举/
  is_global_relation 非布尔 → 快照一律降级不可用。
- **T4**：验收文档单一事实来源；ACCEPTANCE 从干净基线重建并附
  t_rework·T4a 防重复断言。


## 四轮返工修订（Codex 复核 c77dd0d 后，T2a/T2b/T5）

- **T2a 推式失效**：钩子优先级只是相对排序，不是"链末尾"保证（宿主
  AgentBegin 后仍有 ContextManager/LLMSummaryCompressor 真实 await 窗口
  与更低优先级合法后续钩子）。失效改为由动作本身触发：PrefStore 的
  clear/off 写入与 ObservableConfig（dict 子类，写透传宿主配置对象并
  触发回调）同步驱动 TurnRegistry 在等待窗口中立即置空运行时消息。
- **T2b 停用/卸载**：terminate 先 purge_all（关 DB 前）；清理钩子读
  已关闭 DB 异常按 fail-closed（校验失败=不可信=置空）；_still_valid
  纳入 star_map.activated。
- **T5 精确归属**：注入块以完整文本+对象身份登记为凭证；运行时清理
  只置空与全文完全相等且不超过登记数量的块；组装前按对象身份移除。
  禁止按可见前缀/通用类型批量清理。


## 五轮返工修订（Codex 复核 a06a5e4 后，T5a/T5b/T6）

- **T5a 统一归属**：移除 is_own_part 前缀函数；finalize 正常/异常
  分支一律按 TurnRecord.parts 对象身份移除，拿不到凭证时宁可不清理
  也不误删（异常兜底不再"按前缀清所有"）。
- **T5b 运行时身份凭证**：组装后清理统一为「登记全文 + _no_save
  临时标记 + 数量上限」——mark_as_temp 经宿主 model_dump_for_context
  → Message.model_validate 重建后保留在新 part 上；用户原文与其他
  插件普通块即使文本完全相同也保留。单一实现 _blank_runtime_parts
  为推式清理 / AgentBegin 终检 / 异常兜底三入口共用。
- **T6 注册表终态**：on_agent_done（真实完成/失败/中止均触发）与
  on_decorating_result（兜底）按 event 释放全部强引用；finalize 对
  dead 且未挂接运行时的记录同样释放；release 显式清空引用链。
  在途轮次的推式失效能力不受影响。


## 六轮返工修订（Codex 复核 a2378c6 后，T5b/T6a/T6b）

- **T5b 位置映射归属**：组装后归属改为位置映射——宿主
  assemble_context 按序追加每个 extra part，重建后 user content 的
  base=len(content)-extra_count，本插件块位于 content[base+登记索引]；
  定位后全文+_no_save+界内三重校验，失配 fail-safe 跳过。全文+公共
  _no_save+数量上限不再是归属判定（其他插件可同文同 temp）。
- **T6a 装饰条件回收**：on_decorating_result 仅回收 dead 或未挂接
  运行时的记录；多步 Agent 的中间回复（工具调用伴随文字，runner 未
  done）不释放，第二次模型调用仍可被失效。
- **T6b task-done 回调**：attach_runtime 时注册执行本轮宿主 asyncio
  task 的 add_done_callback（完成/取消/err 均触发，弱引用取回 event
  释放）——覆盖无 AgentDone/decorating 的取消与 err 终态；registry
  为 dict[id(event)]+弱引用回调，event 死亡自动清理。


## 七轮返工修订（Codex 复核 f77d345 后，T5b 可靠归属）

- **T5b 每轮唯一令牌**：六轮位置映射在九场景钩子组合下退化为
  「每条消息最后一块」（extra parts 总是先 append，part_index 恒等于
  extra_count-1）：后续钩子追加、更早独立消息、运行时追加均失配。
  七轮改为内容级归属——注入时在文本尾部嵌入每轮随机令牌
  「〔偏好标识<hex>〕」（约 14 字符、模型可见、跨宿主重建链
  model_dump_for_context → Message.model_validate 与多步 Agent 组装
  原样保留）；运行时清理只置空「含本轮令牌且带 _no_save 临时标记」
  的块，同文同 temp 的他人块（不含本轮令牌）保留。
- **finalize 令牌轮换**：注入后的合法请求钩子（priority 介于 20 与
  -1000）可能复制含旧令牌的本插件全文。finalize（-1000，相对靠后但
  非链末尾、
  Runner 组装前）按对象身份把存活块令牌轮换为新值并同步 TurnRecord
  ——此后失效清理只按新令牌匹配，更早副本（持旧令牌）不被误删；
  失效分支仍按对象身份移除，不依赖令牌。
- **registry 实现**：TurnRecord 槽位 tokens 替代 part_index/extra_count；
  registry 保持 dict[id(event)] 骨架，注册时挂 event 弱引用回调，
  event 死亡自动移除条目（无插件侧无界保留）。
- **探针适配等价关系**：composition/terminal/ownership/v/w 中「与
  注入时全文全等」的本插件块识别放宽为「去尾部令牌后主体一致/以
  主体为前缀且非全等」，归属判定（对象身份、_no_save、Provider
  边界、foreign 保留）与场景构造不变；冻结原版探针只读保留于
  偏好管理-独立复核-f77d345-20260918/，适配副本在
  偏好管理-七轮复验-f77d345/。


## 八轮返工修订（Codex 复核 abdba20 后，T7 预算 + T5b 受阻裁定）

- **T7 总字符预算（已修）**：max_inject_chars 按合同是「单轮注入总
  字符上限」，最终表示中的全部模型可见字符（含 14 字归属标识）都计
  入预算——注入前以「上限−len(token)」作为渲染预算，预算不足按既有
  规则丢尾部条目，连头部都放不下则不注入；不截断固定边界约束，不改
  配置语义。
- **T5b 剩余分支（九轮裁定：接口依赖受阻）**：-1000 只是相对排序；
  finalize 之后合法请求钩子（-2000）与 AgentBegin 任意优先级钩子可
  复制本插件全文构造同文 temp 副本。转换链双版本实测（宿主源码
  provider/entities.py assemble_context → model_dump_for_context →
  tool_loop_agent_runner reset 处 Message.model_validate）：①对象身份
  不跨重建；②TextPart 模型字段仅 type/text；③私有属性不入 dump
  （仅宿主特判 _no_save 经 dump/重建保留）；④同文复制副本与本尊在
  (type,text,_no_save) 全部可观测字段上不可区分。**收窄表述**：在本
  项目保持原生 TextPart、使用现有受支持的请求/Agent 钩子、不改变宿主
  转换行为的约束下，现有方案缺少经过验证的稳定来源关联——这不是
  「所有实现均数学上不可实现」的完备证明，也不是「任何位置比较都被
  合同禁止」（合同禁止的是仅凭可变位置猜测来源）；宿主在实际构造时
  记录源对象与输出对象的配对是可评估的解决路线。保持七场景未修，
  不引入合同禁止的启发式（更多轮换点/更晚写入标识同样会被更晚的
  合法复制穿透）。
- **宿主路线（九轮修订，评审稿而非已实施方案）**：优先评估**宿主
  内部的源对象 → 实际运行时对象映射**（建立时点、轮次范围、晚复制
  不继承、异常/取消/停用释放、非 LLM/默认关闭零干预、临时不入历史、
  双版本/多步边界的完整设计见 docs/HOST_INTERFACE_PROPOSAL.md）。
  原八轮提出的「加 owner_key 元数据字段即可」不成立：全对象/完整
  字典复制也可能复制该字段，未定义创建/复制/重建语义的普通字段不能
  自动满足「复制不继承所有权」；若走字段路线，须由宿主提供受控的
  新建/复制语义并证明同值伪装不会使清理扩散。本轮未修改宿主、未把
  未实现方案写成已修。


## 十轮原型修订（隔离宿主副本来源映射验证，2026-09-19）

- **宿主最小补丁（原型，隔离副本）**：`ProviderRequest` 组装构造处
  显式建立 (源 ContentPart, 序列化块) 配对
  （`assemble_context_with_extra_pairs`，原 `assemble_context` 签名
  委托不变）；Runner `_finalize_extra_pairs` 在 `Message.model_validate`
  前把配对块替换回源实例——校验器保留传入实例，**运行时对象与源对象
  同一**。补丁 +54/−9（entities.py、tool_loop_agent_runner.py），
  4.28.0/4.26.0 两版同构适用。
- **插件身份优先失效（0.1.10）**：`_blank_runtime_parts` 检测到任一
  运行时块与源对象同一（宿主直通）即仅按对象身份失效并**停用令牌
  回退**——finalize 后被复制的同文副本携带当前令牌，令牌路径会误删
  （原型实测发现）；原生宿主身份不命中回退令牌，行为与 0.1.9 一致。
- **验证结果（隔离宿主副本）**：token_repro 12 项双版 0 defect（七误删
  全部消失，预算/对照/正常终态保持）；历史 50 场景双版 0 复现（含多步、
  压缩、取消、停用、prefix_collision、retention）；直通事实探针（身份
  直通/晚复制不继承+副本保留/载荷等价）双版全过；evidence_quality 双版
  0 defect 全 assistant；x_rework 双版 8/8（X5 观察的
  assemble_context+裸 validate 层事实不受 Runner 补丁影响）。原生宿主
  16 脚本双版 264 保持、token_repro 7 受阻如实保持。
- **剩余限制**：原型仅验证 4.28.0/4.26.0 现有组装路径，未覆盖宿主其他
  `Message` 构造点；多步 Agent 第二次调用复用同一 run_context（T6a
  场景已覆盖），宿主若在轮次内重建消息需更新配对；配对的弱引用生命
  周期与异常路径仍需宿主侧正式评审；未修改已安装宿主，不构成部署。

## 十一轮至十三轮收尾修订（M1/M2/M3 与 N1/N2/N3，2026-09-19）

- **M1 通道锁定（v2 补丁，0.1.11）**：归属通道在 on_agent_begin（-1000）
  一次性判定——请求映射含本插件源对象即锁 identity 通道，此后不回退
  令牌匹配（修复他人同文副本被令牌回退误删）。
- **M2 快照语义（v2 补丁）**：extras 恢复序列化→重建原生语义；改为
  `Message.model_validate` 后在请求私有内存属性建立 源对象→最终运行时
  实例 映射（不进序列化/Provider 参数/历史），源对象后续修改不影响
  本轮输入。
- **M3 失效交接（v2/v3 补丁）**：`_blank_record` 源对象置空 +
  `_source_invalidated` 标记；映射绑定处补偿置空最终实例（组装等待中
  clear/正式停用均生效）；v3 下同时经请求映射置空运行时实例并锁定
  identity 通道（弱引用表示下仅源置空无效）。
- **N3 映射寿命管理（v3 补丁，0.1.12）**：映射条目改
  `(源对象, weakref.ref(运行时实例))`——runtime 侧弱引用不延长最终实例
  寿命（宿主自身释放规则不变），src 侧强引用与请求持有的 extra parts
  同寿命；设 `_extra_runtime_channel="identity"` 能力标记。插件终态
  `release()` 清空请求映射条目，`_blank_record` 按源归属过滤置空运行时
  实例；`n3_lifetime_check.py` 五场景（DONE/真实 ERROR/真实 Task 取消/
  正式停用/默认关闭）终态后 registry=0、映射条目=0、弱引用死亡。
  取消路径存在宿主任务自身引用（不归因映射），探针如实区分。
- **N1/N2 重建入口（交付脚本，非运行时）**：v2 入口对 work 任意 rmtree、
  哈希不校验、协作基线依赖当前检出——v3 入口 work 须尚不存在且与来源
  无重叠（拒绝零删除、canary 保持）、先校验后创建、一律 git archive
  固定提交（relation 913ca59 / uctx d8a7147+0001）导出并以
  combo_manifest.json 逐文件哈希门校验，探针输出逐字段解析。
- **验证**：双版单一入口全清单 11 项 ALL_OK 0 缺陷；gate 负例 8/8
  拒绝；原生宿主 16 脚本 264 保持、token_repro 7 缺陷复现与
  m_rework M1/M3 受阻如实单列（旧组合失败/新组合通过对照）。
  A0 未通过，待 Codex 复验与宿主侧接入决策。
