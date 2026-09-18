# ACCEPTANCE — 验收矩阵映射（三轮返工后 0.1.3）

- 运行环境：共享 venv（AstrBot 4.28.0 / 4.26.0，Python 3.12.10），脱网、
  合成数据、本地假模型；uctx 组合固定为 d8a7147 + patches/0001 隔离副本，
  Relation Arc 固定 913ca59（均未混入两仓库后续提交）。
- 复现命令（cwd=仓库根；p3_import_check 需 cwd=父目录，见文件头说明）：
  `<venv>/Scripts/python.exe tests/<脚本>.py`
- 证据对应提交：四轮返工最终提交见 git log -1（修改代码后本表须重跑）。
- 统计口径：当前 13 脚本每版 237 PASS
  （12+33+28+25+4+21+23+19+25+10+12+16+9）。旧口径订正：0.1.1 候选
  9 脚本每版 190（非 194——p3_import 的 4 项曾按两版本重复计数）；
  0.1.2 为 11 脚本 211；0.1.3 为 12 脚本 228。

| 编号 | 行为 | 证据（脚本·断言） | 4.28.0 | 4.26.0 |
| --- | --- | --- | --- | --- |
| V01 | 默认关闭不采集；不影响普通轮次 | p2·D01；p3·C03/C04；p4·H4a；t_rework·T1e（默认关闭零干预）；p6·G5 | PASS | PASS |
| V02 | 平台/机器人/人格/用户隔离 | r3·Q1/Q2/Q3（真实 Context.get_config 与人格算法：默认/UMO 作用域一致、读取失败拒绝）；r_rework·RR4a/b；p1·I1；p4·H4e | PASS | PASS |
| V03 | 群聊不读写/展示；伪造不越权 | r_rework·RR1a-RR1e（真实事件+CommandFilter+call_handler）；p3·C01/C02 | PASS | PASS |
| V04 | 模板与用户档案分离；权限边界 | p1·E3a-E3c；p3·C17-C24；p6·G3a/G3d | PASS | PASS |
| V05 | 状态/方向独立；非法输入被拒 | p1·E1/E2a-E2i；p2·D05-D12；r_rework·RR6b（九方向组合唯一） | PASS | PASS |
| V06 | 冲突/禁止/强度/关系暂停优先 | t_rework·T3a-T3f（真实配置管理器+存储：合法 pause/timed/恢复）；r3·Q4 系（真实生效 scope）；p2·D06-D12/R01-R04 | PASS | PASS |
| V07 | 持久化/事务/revision 冲突 | p1·E4/E5a-E5e/E6 | PASS | PASS |
| V08 | 关闭/删除失效（含未发送窗口） | u_rework·U2 压缩等待窗口三动作（真实 ContextManager/LLMSummaryCompressor 等待中 clear/admin_off/off → 推式清理，首调零泄漏）/U3（-2000 后续钩子）/U5（真实 turn_off_plugin 关 DB fail-closed）；t_rework·T2；r3·Q6/Q7；r_rework·RR5；p1·E6；p3·C05/C06/C13-C16；p4·H5 | PASS | PASS |
| V09 | 真实宿主命令；群聊仅引导；管理不进模型 | p7·L2（真实 PluginManager.load 加载后 CommandFilter 参数匹配 + call_handler 完整分发）；r_rework·RR1；p6·G1-G3 | PASS | PASS |
| V10 | 真实请求可见偏好；无关不强套；无额外调用 | p4·H7a/H7b（真实 Runner 一次调用含偏好块）、H8（无数据零干预） | PASS | PASS |
| V11 | 注入去重/上限/不覆盖他人提示；精确归属不误删 | u_rework·U1（同标题不同尾文的用户引用/其他插件引用保留、偏好块按全文凭证精确消失）；t_rework·T1a-T1d；p4·H2c/H3；p2·B01/B09 | PASS | PASS |
| V12 | 临时块不入持久化；回复/轨迹隔离 | p0·F5（真实 _save_to_history 跳过）；p4·H2b/H7c；p5·B1/B3（uctx 账本级隔离） | PASS | PASS |
| V13 | Relation Arc 各形态（含损坏状态降级）+ 原语义回归 | t_rework·T3b-T3e/T3g（非法 JSON/非 dict/非法枚举/类型错 → 降级）；r3·Q4-Q5b（真实 scope/user_version/缺 config）；r_rework·RR3；p5·RA（合成库对齐真实 config+user_version）；只读证明 | PASS | PASS |
| V14 | 偏好私聊不入共享库；群聊读不到 | r_rework·RR2a-RR2d（正式构造+真实 star_map）；p5·B1-B6 | PASS | PASS |
| V15 | 钩子顺序/重试/流式/失败/取消 | p7·L1d（20/-1000 双钩子）/L4（真实 stop_event 传播停止）；r3·Q7；r_rework·RR2（停用路径）；p4·H3、p5·B5（重试）；p6·S1/S2/S3（流式/失败/恢复） | PASS | PASS |
| V16 | 缺协议先禁用并解释 | r_rework·RR2e/RR2f；p4·H6c/H6d；p5·B6 | PASS | PASS |
| V17 | 重载/重启/停用/卸载后恢复（含在途清理） | u_rework·U4（activated=False 失效）/U5（真实 turn_off_plugin：terminate 先 purge_all 再关 DB，清理异常 fail-closed）；t_rework·T1f；p7·L3/L5；p6·G4；p1·E4 | PASS | PASS |
| V18 | 双版本真实包集成与干净装/卸 | p7·L1（真实 PluginManager.load 完整加载：metadata/config/实例化/13 handler 注册=10 命令+2 LLM 钩子+1 AgentBegin 钩子）；u_rework（真实加载下的完整生命周期）；p6·G5/G6a；p3_import_check | PASS | PASS |
| V19 | 交付物无真实数据泄漏 | 最终提交时 git ls-files 全量核对；ZIP 解包清单核对无 db/log/凭据/venv/宿主源码/.git；长数字均为代码常量/合成值 | PASS（打包时） | PASS（打包时） |
| V20 | SHA/补丁/证据一致性；文档可审阅 | ZIP/补丁 SHA-256 以包外 SHA256SUMS.txt 为准；包内容与最终提交一致；t_rework·T4a（ACCEPTANCE 无重复插入、尺寸正常）；全量回归在最终提交复跑 | PASS（打包时） | PASS（打包时） |

## 待实际验收（不计入自动化 PASS）

| 编号 | 行为 | 责任 |
| --- | --- | --- |
| V21 | 实际模型对方向/节奏差异的体现、冲突限制有效性 | MIS-155（用户实机 + Codex 复核） |
| V22 | 测试 QQ 私聊→群聊、多人/多人格隔离、停用恢复 | MIS-155（用户实机 + Codex 复核） |

## 已知边界与如实声明（单份，经 t_rework·T4a 防重复校验）

- 命令解析链：p7·L2 已覆盖真实加载后的 CommandFilter 参数匹配与
  call_handler 完整分发（私聊 /xp show 端到端）；persona/conversation/
  config 为受控合成注入，模型为本地假端。实机体验归 V22。
- R5 失效覆盖（四轮后为推式机制）：个人 off/clear、管理员总开关、
  epoch 变化、插件停用（activated=False / 真实 turn_off_plugin）由
  **失效动作本身**触发 TurnRegistry 推清理——存储写入回调与
  ObservableConfig 写回调在等待窗口（含内置 LLMSummaryCompressor
  的真实 await 与 -2000 合法后续钩子）中立即置空运行时消息；钩子
  检查点（追加前 / finalize -1000 / on_agent_begin -1000）作为补充
  防线，校验异常一律 fail-closed。**OnAgentBegin 返回后到首次
  Provider 调用之间不存在插件钩子点（接口缺口）**；已真正发出的
  请求不可撤回（未把 append/reset/AgentBegin 重定义为发送）。
- p7·L4 证明的是事件传播停止（stop_event 后续钩子不执行），不等同于
  对在途 Agent 协程的 asyncio 取消；p7·L5 证明 terminate 后新轮次
  零注入，不等于在途请求或全局状态的恢复验证。
- V13"原六维/绑定语义不变"由结构性只读（mode=ro + 纯 SELECT +
  p5·RA7 字节不变）证明；Relation Arc 自身功能回归以其项目测试为准。
- T2 复核脚本替身差异说明：Codex serialization_repro 的 after_reset
  场景设置 ev.plugins_name=[探针模块]（白名单排除本插件）且未按
  PluginManager.load 重绑 handler——宿主源码证据：①生产 plugins_name
  仅在管理员配置 plugin_set 时设置（waking_check/stage.py:164-169），
  排除本插件时注入主钩子同样被排除（不会产生旧块）；②真实加载把
  插件方法重绑为 functools.partial(raw, star_cls)（star_manager 实例
  化段），未绑定 handler 在生产不存在。等价真实加载状态的验证见
  t_rework·T2（白名单含插件名 + partial 绑定）。
- 0.1.2 的 ExpirableTextPart 曾覆盖宿主全局 text 类型注册（Codex T1），
  0.1.3 已删除该子类并改用前缀识别 + 钩子时机清理；t_rework·T1a-T1f
  证明导入/默认关闭/terminate 前后宿主普通文本、历史摘要、其他插件
  文本与请求组装保持一致。
