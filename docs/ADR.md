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
