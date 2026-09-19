# HANDOFF — astrbot_plugin_preference_profile v0.1.12（十三轮收尾候选）

交接日期：2026-09-19（十三轮收尾轮）。交付状态：**Codex 第十二轮确认
M1/M2/M3 通过、七 T5b 消失后，N1（重建入口保护）/N2（清单哈希门）/
N3（映射寿命管理）收尾完成，双版单一入口全清单 11 项 ALL_OK；
待 Codex 复验；A0 未通过**。未宣称实机、云端或部署完成。宿主补丁
diff/机器可读哈希清单/单一入口重建脚本见偏好管理-宿主来源映射原型-20260919/。

## 0-13. 十三轮收尾摘要（9517bd4 版本提交 → 0.1.12 文档定稿）

| 项 | 修复 | 旧组合真实行为失败对照 | 新组合（v3+0.1.12）双版 |
| -- | -- | -- | -- |
| N1 重建入口保护来源 | rebuild_and_test.py v3：work 输出必须尚不存在且与全部来源无重叠（等于来源/是来源祖先/位于来源内部/普通已有目录均拒绝），拒绝路径零删除；先完整校验全部输入后才创建输出 | v2 入口对 work 路径 rmtree（rebuild_and_test.py:56-70）：误传来源目录会清空来源 | gate 负例 8/8 拒绝；已存在目录 canary 前后保持 |
| N2 固定版本并校验交付 | 机器可读清单 combo_manifest.json（宿主原始/补丁/应用后逐文件哈希、插件提交与树哈希、协作固定提交 relation 913ca59 / uctx d8a7147+0001 应用后与补丁哈希）；重建一律 git archive 固定提交导出；探针输出逐字段解析；summary.json 记录退出码/场景数/缺陷数/导入路径/组合逐文件哈希 | v2 入口（rebuild_and_test.py:31-46/73-102）不校验哈希、协作基线依赖当前检出（实测漂移至 21eef11） | 双版重建含哈希门全过；错哈希/错 tag/缺失协作提交负例全拒绝 |
| N3 映射寿命管理 | 宿主补丁 v3：映射条目改 `(源对象, weakref.ref(运行时实例))`（runtime 侧弱引用不延长寿命）+ `_extra_runtime_channel` 能力标记；插件 callable 探测兼容解引用、identity 分支按源归属过滤置空、`release()`/`_blank_record` 终态清空请求映射；「-1000 必在所有合法钩子之前」表述收窄 | mapping_handoff 探针：DONE/ERROR 终态后 2 个运行时实例仍被映射强引用存活（移除映射后归零）；取消路径存在宿主任务引用不归因映射 | n3_lifetime_check 五场景终态后 registry=0、映射条目=0、弱引用死亡；mapping_handoff 三终态对照归零 |

验证（双版单一入口 `rebuild_and_test.py` ALL_OK，11 项 0 缺陷）：
boundary(8)/token(12)/corrected(6)/remaining(6)/composition(9)/terminal(8)/
ownership(4)/lifecycle(17)/identity_map(4)/evidence(4)/m_rework(4)。
**原生宿主对照（如实单列）**：原生+0.1.12 下 16 脚本 264 PASS 保持；
token_repro 12 场景 **7 缺陷复现**（clear/admin_off 组，旧组合失败）→
补丁宿主新组合 0 缺陷（新组合通过）；m_rework PASS 2/FAIL 2（M1/M3
受阻如实）；prototype_boundary 4 缺陷（原生失败集合与补丁宿主不同）。
复现：`python rebuild_and_test.py --venv <venv> --plugin <repo> --work
<不存在的新目录> --tag <428|426>`；负例门加 `--gates`。

## 0-11. 十一轮定向修复摘要（6d73bb5 → 22aac1a + 文档，历史保留）

| 项 | 修复 | 旧组合（6d73bb5+v1 补丁）真实行为失败对照 | 新组合（v2+0.1.11）双版 |
| -- | -- | -- | -- |
| M1 通道锁定 | 归属通道在 on_agent_begin 一次性判定：请求上映射（`_extra_runtime_pairs`，dataclass 私有内存属性）含本插件源对象即锁 identity；此后他人移除/置空原块也**不回退令牌匹配** | ownership_remove_own/blank_own+clear：他人 140 字副本被令牌回退误删（foreign_ok=False） | 八场景 0 defect；m_rework·M1 通过 |
| M2 快照语义 | 宿主补丁 v2 恢复 extras 序列化→重建原生语义；改为 model_validate 后在请求私有内存属性建立 源对象→最终运行时实例 映射（不进序列化/Provider 参数/历史） | media_window_mutate_foreign：Provider 收到 FOREIGN_CHANGED_DURING_MEDIA（快照被改） | m_rework·M2 通过；默认关闭零注入零记录 |
| M3 组装等待中停用 | `_blank_record` 对源对象置空并标记 `_source_invalidated`；宿主映射绑定处检查标记补偿置空最终实例——失效先发生、实例后建立的交接补全，不依赖 DB/handler 分发 | media_window_turn_off_plugin：停用后旧偏好仍送达（own_sent=True） | m_rework·M3 通过（原生宿主该分支仍受阻，如实保留） |

全量（隔离副本 v2+0.1.11，双版）：boundary 八场景 0 defect；token_repro
12 项 0 defect（预算/对照/normal_terminal 保持）；历史 50 场景 0 复现；
identity_map 探针 4 场景 ALL_PASS；evidence_quality 0 defect 全 assistant；
m_rework 4/4。**原生宿主对照**（如实保留）：16 脚本双版 264 PASS；
token_repro 7 受阻；prototype_boundary 4 缺陷（原生失败集合与补丁宿主不同）；
m_rework M1/M3 受阻失败。单一入口重建：`python rebuild_and_test.py --venv
<venv> --plugin <repo> --work <new dir> --tag <428|426>`（双版 ALL_OK 已验证）。

## 0-10. 十轮原型摘要（8a8bdbf → dabaa61，历史保留）

| 项 | 内容 | 结果 |
| -- | -- | -- |
| 宿主最小补丁 | 隔离宿主源码副本（4.28.0/4.26.0 拷贝，HOST/astrbot 遮蔽）：①ProviderRequest.assemble_context 重构出 assemble_context_with_extra_pairs——组装构造处显式建立 (源 ContentPart, 序列化块) 配对；②Runner._finalize_extra_pairs 在 Message.model_validate 前把块替换回源实例（ContentPart 校验器保留传入实例→运行时对象与源对象同一）。+54/−9，两版同构 | 补丁 diff：偏好管理-宿主来源映射原型-20260919/host_patch_{428,426}.diff |
| 插件适配（0.1.10） | _blank_runtime_parts：先收集运行时 temp 文本块；身份命中（块 is 源对象）→ 仅按对象身份失效并**停用令牌回退**（finalize 后复制副本携带当前令牌，令牌路径会误删——原型实测发现）；身份未命中（原生宿主）→ 回退令牌子串匹配，行为与 0.1.9 一致 | 原生宿主 16 脚本双版各 264 PASS 保持；token_repro 7 受阻如实保持 |
| 原型验证（隔离宿主） | token_repro 12 项、历史 50 场景、直通事实探针（identity_passthrough_probe：F1 身份直通、F2 晚复制不继承+副本保留、F3 载荷等价与正常终态）、evidence_quality 终态、x_rework 8 断言 | token_repro 双版 **0 defect**（七误删全部消失，预算/对照/normal_terminal 保持）；历史 50 双版 0 复现；探针双版 ALL_PASS；evidence_quality 0 defect 全 assistant；x_rework 双版 8/8（X5 观察的 assemble_context+裸 validate 层事实不受 Runner 补丁影响，与直通探针互补） |

精确基线：宿主 AstrBot 4.28.0 / 4.26.0（共享 venv 内安装版拷贝，补丁前
哈希记录于 diff 的 --- 行）；插件基线 8a8bdbf/0.1.9；协作组合 Relation
Arc 913ca59、Context Bridge d8a7147+0001（隔离副本）。剩余限制见
偏好管理-宿主来源映射原型-20260919/prototype_report.md。

## 0-9. 九轮证据收尾摘要（47bc1c9 → 154e8da/8a8bdbf，历史保留）

| 项 | 处置 | 证据 |
| -- | -- | -- |
| E1 预算回归假 PASS | 修：BudgetProvider 返回父类真实 LLMResponse；run_turn 断言 DONE+role=assistant+「synthetic final reply」；X1/X2/X4 增加正常完成记录释放断言 | 修复前 evidence_quality_probe 双版各 4 用例 AgentState.ERROR/role=err；修复后双版 8/8 PASS 且探针 0 defect、终态全 assistant |
| E2 token_repro 完整执行 | 适配版（Codex 第九轮版 shim 复用）双版完整 12 项：允许无注入记录、Provider 侧核预算、140 对照强制完整主体+标识、要求正常终态 | 双版各 12 行：T5b 七项 defect_reproduced=true（受阻如实保留）、预算两例与三对照通过（normal_terminal=true）；旧 abdba20 预算对照 140>126 复现 |
| 文档与统计 | ACCEPTANCE 头部当前实数（16 脚本每版 264）并单列「通过/未修受阻/接口事实」；源码「不可伪造/他人不含本轮令牌」收窄；ADR 收窄「构造性不可实现」为「当前原生转换路径与既定受支持接口下缺少已验证关联」，owner_key 降级为仍需论证 | docs/ACCEPTANCE.md、pref_profile/injection.py、docs/ADR.md |
| 宿主来源映射设计说明 | 新增 docs/HOST_INTERFACE_PROPOSAL.md（评审稿）：源对象→实际运行时对象映射的建立时点/轮次范围/晚复制不继承/异常取消停用释放/非 LLM 零干预/临时不入历史/多步与双版本边界；owner_key 全对象复制继承陷阱；离线最小验证 instance_retention_probe（实例传入被宿主保留、dict 重建为新实例、注册表不受影响，双版成立） | docs/HOST_INTERFACE_PROPOSAL.md + instance_retention_probe.json（双版） |

全量：16 脚本双 venv 各 264 PASS；历史 50 场景×双版通过；token_repro
12 项双版 7 受阻 + 5 过。T5b 七场景不通过删除/跳过改绿。

## 0-8. 八轮返工摘要（abdba20 → 508b84b/47bc1c9，历史保留）

第八轮复核（f77d345 → abdba20 之后的 token_repro）判两项 P2：

| 项 | 处置 | 证据 |
| -- | -- | -- |
| T7 总字符预算 | 已修：注入前以「max_inject_chars − 14 字标识」渲染，不足丢尾部条目、放不下不注入；合同「单轮注入总字符上限」语义不变 | token_repro budget_exact_body 双版 0 复现；x_rework·X1（旧 abdba20 行为性失败 140>126，新候选双版通过）、X2-X4（对照/小预算/正常注入） |
| T5b 轮换后副本误删 | **受阻**：finalize（-1000，相对靠后但非链末尾）之后，合法请求钩子（-2000）与 AgentBegin 任意优先级钩子可复制本插件全文构造同文 temp 副本；转换链双版本实测证明 extra part 经 model_dump_for_context→Message.model_validate 重建后对象身份丢失、TextPart 模型字段仅 type/text、私有属性不入 dump（仅宿主特判 _no_save）、同文复制副本与本尊可观测不可区分——内容级/属性级判据均被合法复制继承，现有宿主 Part 接口无法承载「复制不继承所有权」的关联；任何内容标识、更多轮换点或更晚写入都会被更晚的合法复制穿透 | token_repro 7 场景双版保持（受阻项如实保留）；conversion_chain_probe 双版本 + x_rework·X5 转换链事实；最小宿主支持方案见 ADR 八轮小节 |

全量：16 脚本双 venv 各 264 PASS；composition 9/terminal 8/ownership 4/
lifecycle 17×双版 0 复现；token_repro 12 场景双版仅 T5b 七项保持。

## 0a. 七轮返工摘要（edc5a75 → 3aacc15，历史保留）

Codex 七轮判 T5b（可靠归属）：六轮位置映射在九场景钩子组合下退化
为「每条消息最后一块」（extra parts 总是先 append，part_index 恒等于
extra_count-1），后续钩子追加/更早独立消息/运行时追加均失配。

| 项 | 根因 | 修复 | 证据 |
| -- | -- | -- | -- |
| T5b | 位置映射退化（九场景缺陷） | 每轮唯一令牌「〔偏好标识<hex>〕」嵌入注入文本尾部；运行时清理只按本轮令牌子串+_no_save 匹配；finalize（-1000，相对靠后但非链末尾）按对象身份轮换存活块令牌并同步登记——更早同文副本（持旧令牌）失效清理时不被误删；失效分支仍按对象身份移除 | composition 九场景×双版本 0 复现；同一适配探针历史基线重核：a2378c6 每版 1 缺陷、f77d345 每版 6 缺陷、abdba20 为 0；terminal 8/ownership 4/lifecycle 17×双版本全绿；15 回归脚本×双 venv 全绿（v/w 夹具按「去令牌主体一致」等价关系适配） |

探针适配等价关系见 ADR「七轮返工修订」；冻结原版探针只读保留于
偏好管理-独立复核-f77d345-20260918/，适配副本在
偏好管理-七轮复验-f77d345/（HOST 为 .tmp_t7verify/new 与 new426，
旧候选对照 HOST 为 .tmp_t7verify/old）。

## 0. 六轮返工摘要（a2378c6 → edc5a75，历史保留）

Codex 六轮判 T5b/T6a/T6b：

| 项 | 根因 | 修复 | 证据 |
| -- | -- | -- | -- |
| T5b | 全文+_no_save+数量仍非归属：其他插件原生 mark_as_temp 同文块被误删、本插件真块漏清 | 位置映射：assemble_context 按序追加 extra parts，重建后 base=len(content)-extra_count，块位于 content[base+登记索引]；全文+_no_save+界内三重校验，失配 fail-safe | terminal T5b_early/late 双版 0 复现（对象身份+Provider 边界）；w_rework W1 |
| T6a | on_decorating_result 无条件 release：多步 Agent 中间文字装饰时 runner 未 done 即释放，后续失效无目标 | 装饰仅回收 dead/未挂接记录 | terminal T6a 双版 0 复现（真实工具+装饰+第二次调用）；w_rework W2 |
| T6b | stop 中止与 asyncio 取消无 AgentDone/decorating，记录滞留 | attach_runtime 注册执行轮次 task 的 done 回调（完成/取消/err 均触发）+ event 弱引用自动回收 | terminal T6b 双版 0 复现；w_rework W3/W4 |

## 0b. 五轮返工摘要（a06a5e4 → a2378c6，历史保留）

Codex 五轮确认推式失效有效（四轮 17 场景保持全绿）；判 T5a/T5b/T6：

| 项 | 根因 | 修复 | 证据 |
| -- | -- | -- | -- |
| T5a | finalize 正常/异常分支仍调 is_own_part 前缀匹配，推式清理后误删同标题其他插件块 | 两分支全改 TurnRecord.parts 对象身份；删除 is_own_part；拿不到凭证宁可不清理 | ownership T5_finalize 双版本 0 复现；v_rework V1 |
| T5b | 全文相等+数量上限仍非归属：early 清掉同文用户原文；late 数量耗尽在用户原文、真 temp 块漏发 | 运行时身份=「登记全文 + _no_save 临时标记 + 数量上限」（宿主序列化链重建后 temp 标记保留）；单一实现三入口共用 | ownership early/late_exact 双版本 0 复现（Provider 边界 _no_save 块判定）；v_rework V2 early+late |
| T6 | 完成轮次记录不释放：5 轮 DONE 后注册表 1..5、弱引用全存活 | on_agent_done + on_decorating_result 终态释放全部强引用；finalize 释放 dead 未挂接记录 | ownership retention 0 复现；v_rework V3/V4（失败 decorating 兜底） |

## 0b. 四轮返工摘要（c77dd0d → a06a5e4，历史保留）

Codex 四轮确认 T1/T3/T4 关闭、接受旧 T2 夹具说明；判 T2a/T2b/T5：

| 项 | 根因 | 修复 | 证据 |
| -- | -- | -- | -- |
| T2a | -1000 钩子非链末尾保证：AgentBegin 后仍有压缩 await 与 -2000 合法钩子等待，期间失效后首调泄漏 | 推式失效：PrefStore 写入回调 + ObservableConfig 写回调 → TurnRegistry 在等待窗口立即按全文凭证置空运行时消息 | lifecycle_repro 新副本双版本 17 场景全绿（compression_clear/admin_off/off、late_begin_hook_clear 0 复现）；u_rework U2/U3 |
| T2b | 真实 turn_off_plugin 后 terminate 关 DB，清理钩子读关闭连接异常被吞，原块继续发送 | terminate 先 purge_all 再关库；校验异常 fail-closed；_still_valid 纳入 star_map.activated | u_rework U4（disable）/U5（真实 turn_off_plugin） |
| T5 | is_own_part 按可见前缀匹配，误删同标题不同尾文的用户引用与其他插件块 | 注入时登记完整文本+对象身份凭证；运行时只置空全文完全相等且不超登记数量的块；组装前按对象身份移除 | lifecycle prefix_collision 0 复现；u_rework U1 |

## 0b. 三轮返工摘要（8de2365 → c77dd0d，历史保留）

Codex 三轮确认前两轮 13 个反例已修，判 T1–T4：

| 项 | 根因 | 修复 | 证据 |
| -- | -- | -- | -- |
| T1 | ExpirableTextPart 子类经 ContentPart.__init_subclass__ 覆盖宿主全局 text 注册，导入即污染普通文本 | 删除子类；前缀识别 is_own_part + 钩子时机清理 | serialization_repro 新副本双版本 3 探针 0 复现；t_rework T1a-T1f |
| T2 | reset 后、首次 Provider 调用前无失效校验 | on_agent_begin(priority=-1000) 对 run_context.messages 终检置空 | t_rework T2 clear/admin_off（真实 OnAgentBegin 等待，首调零泄漏、原输入/哨兵保留）；探针替身差异见 ACCEPTANCE 边界 |
| T3 | 损坏 state_json 解析失败被 pass 当 normal | 解析失败/非 dict/非法枚举/scope 类型错 → 降级 | serialization_repro 0 复现；t_rework T3b-T3g |
| T4 | ACCEPTANCE.md 4.8MB/3585 次重复插入 | 从 0491cc9 干净基线重建，单份矩阵+边界 | t_rework T4a（尺寸<100KB、重复≤1） |

## 0b. 二轮返工摘要（0491cc9 → 8de2365，历史保留）

Codex 二轮确认原六反例修复并接受 R4 旧替身说明（corrected_repro 全绿）。
本轮修复 7 个 remaining 场景：

| 项 | 根因 | 修复 | 证据 |
| -- | -- | -- | -- |
| R4-1 | 4.26 生产注入未传 provider_settings | injector 构造接入 provider_settings_getter（按 UMO） | r3·Q1（4.26+4.28 均注入 1 块）；Codex remaining 双版本 0 复现 |
| R4-2 | 命令只取全局配置 | _provider_settings(event) 按 umo 取会话作用域 | r3·Q2（scoped persona_B 命令=B） |
| R4-3 | 会话读取失败落默认人格 | LookupError → 拒绝私人档案操作 | r3·Q3（identity=None） |
| R5-1 | admin off 未复查 | _still_valid（admin+enabled+epoch）追加前复查 | r3·Q6 |
| R5-2 | append≠发送仍不可失效 | ExpirableTextPart 发送序列化时刻校验 + finalize(-1000) 收尾移除 | r3·Q7（clear 后假模型 0 泄漏、其他插件块保留） |
| R3-1 | 双 scope 合并混入未启用范围 | config.json 单选生效 scope | r3·Q4/Q4b（真实 _scope 对照） |
| R3-2 | 未知 schema 判可用 | PRAGMA user_version∈{12} + config_version≤7 | r3·Q5/Q5b（999 与缺 config 均降级） |

真实集成证据（MIS-152）：p7_lifecycle_check 用真实
PluginManager.load(specified_dir_name) 从临时 data/plugins 完整加载
（metadata/AstrBotConfig schema/实例化/initialize/12 handler 注册），
真实 CommandFilter 参数匹配 + call_handler 完整分发 /xp show，真实
stop_event 取消中止钩子链，真实 registry activated 停用过滤，
terminate 后零注入。数据目录经 patch 隔离（真实加载路径默认写
cwd 相对 data/，生产语义；测试不污染仓库）。

注：corrected_repro 的 R3 场景无 config.json（真实部署
PluginConfigManager.load_or_create 恒写）——按二轮合同"配置无法
确认时降级"为不可用是预期保守行为；等价带 config 场景见 r3·Q4c。

## 0b. 一轮返工摘要（0c32cee → 0491cc9，历史保留）

| 项 | 根因 | 修复 | 旧失败/新通过证据 |
| -- | -- | -- | -- |
| R1 | `bool(getattr(event,"is_private_chat",False))` 取绑定方法真假值恒真；测试以 @property 伪造接口 | identity.host_is_private_chat 统一判定（callable 调用、异常拒绝）；fakes 删 property | Codex 六反例 old=缺陷 true → new 双版本 false；r_rework RR1a-e |
| R2 | 生产 `BridgeGuard()` 空参恒 no_bridge | 正式构造传真实 star_map；探测实时化（装卸/停用/activated=False）；标志写失败保守禁注入 | 同上 R2 行；r_rework RR2a-f |
| R3 | 快照只收 state_json 键名，漏读 interaction_safety 值 | base=state_json.interaction_safety 值，与 timed 按 effective_interaction_safety 秩合并取高；纯 SELECT 过期判定；双 scope 取严 | 同上 R3 行；r_rework RR3a-d（真实 RelationStore API） |
| R4 | 命令解析 conversation=None；4.26 缺 provider_settings | 命令读当前选中会话（get_curr_conversation_id→get_conversation，与请求 _get_session_conv 同源）；resolve 按签名适配 provider_settings | r_rework RR4a/RR4b（真实解析算法+受控会话）；见下"R4 复现脚本替身差异" |
| R5 | epoch 读后未再校验 | append 前重校验 enabled/epoch；append 即提交（不可撤回如实声明） | 同上 R5 行；r_rework RR5a/b（受控 await 边界） |
| R6 | 方向集合化丢序 | 有序九组合真值表 | 同上 R6 行；r_rework RR6a/b |

**R4 复现脚本替身差异（向 Codex 说明）**：independent_repro.py 的
create_plugin 以 SimpleNamespace 提供 context 且未包含
conversation_manager（真实宿主 Context 必有该属性），其
get_config 亦无 provider_settings。在此替身下命令人格无会话信息可读、
只能走宿主默认链（4.28→配置默认；4.26→无默认则 None），与手工注入
persona_B 的 req 不一致是信息集差异而非解析缺陷——命令侧不存在获取
persona_B 的通道，该判定在正确实现下不可能翻绿。复验请使用提供
conversation_manager 的替身（等价于 r_rework_check 的
ControlledConversationManager），真实宿主中该属性由 Context 注入
（astrbot/core/star/context.py:157）。

### 返工验证

- 本仓库 tests/r_rework_check.py：双 venv 各 25/25（真实事件/
  CommandFilter/call_handler/正式构造/star_map 装卸/真实 RelationStore/
  真实人格算法/受控调度）。
- Codex independent_repro.py 于新候选独立副本（.tmp_rework_verify/new，
  已验证后清理）：4.28 与 4.26 上 R1/R2/R3/R5/R6 均
  defect_reproduced=false；R4 true 属上述替身差异。旧候选副本
  （old=0c32cee）六项均 true（复核结论可复现）。
- 全量回归：双 venv 各 8 脚本 + import 194 项断言 0 FAIL 0 跳过
  （p6 已改为正式构造，不再 __new__ 旁路）。


## 1. 最终状态

- 仓库：`astrbot_plugin_preference_profile`，分支 `main`，独立 Git
  （无远端、未 push）。阶段提交：P0 ccd9265 → P1 c5b8786 → P2 e8051d6
  → P3 492358e → P4 18afbcc → P5 26429a0 → P6 fd1cc10 → P7（最终提交
  见 `git log -1`）。作者身份沿用本机既有插件提交身份
  Ewnscat-ya（仓库级配置）。
- 工作区：交付时 `git status --short` 应仅剩本 HANDOFF 相关最终改动
  之外的零星状态；以实查为准。
- 安装包：`dist/astrbot_plugin_preference_profile-0.1.0.zip` +
  `dist/SHA256SUMS.txt`（打包清单见 §4）。
- 协作补丁：`patches/0001-uctx-turn-exclusion-protocol.patch`
  （基线 d8a7147）+ `patches/SHA256SUMS.txt` + `patches/README.md`
  （应用/回滚/回归证据）。

## 2. 验证证据（复现入口）

双宿主 venv（4.28.0 = `D:/第三方插件完善/.venv`、4.26.0 =
`D:/第三方插件完善/.venv426`）各跑 8 个脚本、165 项断言、0 FAIL 0 跳过：

```
cd astrbot_plugin_preference_profile
<venv>/Scripts/python.exe tests/p0_host_contract_check.py     # 12
<venv>/Scripts/python.exe tests/p1_store_check.py             # 33
<venv>/Scripts/python.exe tests/p2_policy_check.py            # 28
<venv>/Scripts/python.exe tests/p3_commands_check.py          # 25
<venv>/Scripts/python.exe tests/p3_import_check.py            # 4（cwd=父目录）
<venv>/Scripts/python.exe tests/p4_injection_check.py         # 21
<venv>/Scripts/python.exe tests/p5_integration_check.py       # 23（需 .tmp_uctx_isolation 隔离副本，见 §5）
<venv>/Scripts/python.exe tests/p6_acceptance_check.py        # 19
```

V01–V20 逐项映射：docs/ACCEPTANCE.md（V19/V20 证据即本 HANDOFF §3/§4）。
V21/V22 归 MIS-155 实机，未实测不报 PASS。

## 3. 泄漏检查（V19）

- Git 跟踪清单：`git ls-files` 全量核对，仅源码/文档/测试/补丁；
  无数据库、日志、凭据、虚拟环境、宿主源码、真实账号/聊天/配置。
- 测试全部使用合成数据与假端（FakeProvider 不出网，
  api_base=127.0.0.1:0）；文档仅含脱敏路径与合成示例。
- ZIP 解包清单核对：见 §4，与 Git 跟踪一致（另含打包清单文件）。

## 4. 打包（V20）

- ZIP 顶层目录 `astrbot_plugin_preference_profile/`，内容=源码
  （main.py、pref_profile/、metadata.yaml、_conf_schema.json、
  requirements.txt）+ 文档（README/CHANGELOG/HANDOFF/docs/）+
  tests/ + patches/。不含 .git、data、__pycache__、.mimosa、dist。
- SHA-256 记录于 dist/SHA256SUMS.txt（包外清单，避免包内自引用失真）；
  补丁哈希于 patches/SHA256SUMS.txt。ZIP 于文档定稿后重打包，
  内容与最终提交一致。
- 干净安装验证：数据目录删除后全新初始化（p6·G5）；真实宿主导入
  （p3_import_check 双 venv）。

## 5. 隔离副本（补丁工作区，可重建）

`.tmp_uctx_isolation/astrbot_plugin_user_context_bridge` =
`git clone` 本机 uctx 仓库（基线 d8a7147）+ 应用 patches/0001。
原目录全程只读未动。p5 测试依赖该副本；重建方式见 patches/README.md。

## 6. 已知限制与外部事项（如实）

- 脱网环境未端到端执行宿主 CommandFilter 分发（需完整
  PipelineContext）；已覆盖真实注册+真实绑定层方法（V09 部分）、
  实机命令体验归 V22。
- V21 模型遵从度未实测。
- uctx 基线执行期间从 8477eba → d8a7147（该项目自身验收返工）；
  补丁基于后者。若 uctx 再前进，需按 patches/README 重新对基。
- Mimosa 工作区级门禁多次拦截源于**其他既有插件**（astrbot-custom-plugins
  下 r18_filter/status_panel/qq_memory 的路径穿越等）真实发现——
  不在本轮只读授权内，留用户决策；本仓库聚焦深度扫描 findings=0
  （不据此宣称工作区安全）。
- 许可未指定（发布前决定）。

## 7. 后续

- A0/MIS-154：Codex 独立验收（复跑 §2、核对 §1/§4 一致性、
  独立检查身份/权限/冲突/暂停/隔离/删除/降级）。
- A1/MIS-155：用户实机 V21/V22。
- F1/F2：Pages、确认式学习（Backlog）。

## 8. 滚动断点（历史）

- 2026-09-17 P0 完成 → P1 → P2 → P3 → P4 → P5 → P6 → P7，
  各阶段详情见 docs/STATUS.md 阶段记录表与 Linear 评论。
