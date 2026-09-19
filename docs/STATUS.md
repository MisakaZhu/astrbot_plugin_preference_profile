# STATUS — astrbot_plugin_preference_profile

最后更新：2026-09-19（十轮宿主来源映射原型候选 0.1.10）

## 当前状态：T5b 宿主来源映射最小原型已在隔离宿主副本验证通过（七误删双版 0 复现）；插件 0.1.10 增身份优先失效（原生宿主行为不变）；待 Codex 复验与宿主侧决策

十轮（8a8bdbf → dabaa61）：按第十轮裁定在**隔离宿主源码副本**（AstrBot
4.28.0/4.26.0 各一份拷贝，HOST/astrbot 遮蔽加载，已安装宿主与共享 venv
只读未动）实施最小宿主补丁——`ProviderRequest.assemble_context` 重构出
`assemble_context_with_extra_pairs`（组装构造处显式建立 源对象→序列化块
配对），Runner `_finalize_extra_pairs` 在 `Message.model_validate` 前把块
替换回源实例（校验器保留分支生效→运行时对象与源对象同一）。插件
`_blank_runtime_parts` 改为身份命中即停用令牌回退（仅按对象身份失效），
原生宿主身份不命中回退令牌（行为与 0.1.9 完全一致）。**原型验证**：
token_repro 12 项双版 0 defect（七误删全部消失，预算/对照/正常终态保持）；
历史 50 场景双版 0 复现（含多步/压缩/取消/停用）；直通事实探针双版全过
（身份直通、自身失效、晚复制副本保留、正常对照）。**原生宿主**：16 脚本
双版各 264 PASS 保持、token_repro 7 受阻如实保持（原型不掩盖原生受阻）。
宿主补丁 diff（+54/−9，entities.py + tool_loop_agent_runner.py）与原型
报告见 偏好管理-宿主来源映射原型-20260919/。本原型未修改已安装宿主、
未部署，A0 仍待 Codex 复验与宿主侧接入决策。

### 历史轮次摘要

九轮（47bc1c9 → 154e8da/8a8bdbf，0.1.9）：证据收尾通过——T7 独立复核
通过；T5b 接口依赖受阻已接受（未修）；E1 真实终态/E2 完整 12 项解决；
新增 HOST_INTERFACE_PROPOSAL 评审稿与 instance_retention_probe。

### 历史轮次摘要

八轮返工（abdba20 → 508b84b/47bc1c9，0.1.8）：T7 预算计入标识 +
x_rework 新回归；T5b 剩余分支交受阻证据。七轮（edc5a75 →
3aacc15/abdba20，0.1.7）：T5b 改每轮唯一令牌 + finalize 轮换。
历史基线重核：同一 composition 适配探针 a2378c6 每版 1 缺陷、
f77d345 每版 6 缺陷、abdba20 为 0。

返工轮（0c32cee → 新提交）：Codex 复核 FAIL 后修复六项缺陷，
详见 HANDOFF 返工章节与 tests/r_rework_check.py。

- 分支 `main`，最终提交与工作区状态以 `git log --oneline -1` 与
  `git status --short` 实查为准；P0–P7 各阶段提交见 git log。
- 安装包：`dist/` 下按文件名取当前候选（0.1.8），SHA-256 见
  `dist/SHA256SUMS.txt`（多版本共存，按包文件名核对，勿取清单首行）。
- 协作补丁：`patches/0001-uctx-turn-exclusion-protocol.patch`
  （基线 d8a7147，SHA-256 见 `patches/SHA256SUMS.txt`）。

## 阶段记录

| 阶段 | 提交 | 要点 |
| --- | --- | --- |
| P0 | ccd9265 | 现场核对、Git 初始化、ADR-001..008 冻结、真实宿主验证 12/12×2 |
| P1 | c5b8786 | 四元身份/枚举校验/SQLite 乐观锁与 epoch，33/33×2 |
| P2 | e8051d6 | 决策引擎与受限渲染（冲突取严/节奏压制/注入防御），28/28×2 |
| P3 | 492358e | 命令层/权限/clear 确认/删除边界 + 真实宿主导入冒烟，25+4×2 |
| P4 | 18afbcc | priority=20 注入器/uctx 三态/真实 Runner 与写回证据，21/21×2 |
| P5 | 26429a0 | Relation 只读快照 + uctx 捕获前排除补丁（基线 d8a7147），23/23×2 |
| P6 | fd1cc10 | V01–V18 映射定稿、绑定层/生命周期/流式失败补充，19/19×2；全量 165×2 |
| P7 | （本提交） | README/CHANGELOG/HANDOFF/打包/泄漏检查/V19–V20 |

全量回归（P7 时点历史）：4.28.0 与 4.26.0 各 8 脚本 165 项断言 0 FAIL 0 跳过；当前统计见顶部八轮状态（16 脚本各 264）。
复现命令见 docs/ACCEPTANCE.md。

## 重要外部事件记录

- uctx 基线在 P5 执行期间由 8477eba 前进到 d8a7147（该项目独立验收
  返工，非本插件改动）；补丁以 d8a7147 为精确基线制作并验证。
- Relation Arc 基线 913ca59 全程未变（本插件只读）。
- Mimosa 工作区级门禁多次拦截源于**其他既有插件**（r18_filter/
  status_panel/qq_memory 路径穿越等）的真实发现；本仓库两次聚焦
  深度扫描 findings=0。外部插件不在本轮修改授权内，保留为用户侧
  待办，不宣称工作区安全。

## 待验收（不在本轮判定范围）

- A0/MIS-154：Codex 独立验收（最终 SHA、V01–V20 证据复跑、ZIP）。
- A1/MIS-155：用户实机 V21（模型效果差异）/V22（QQ 行为）。
- F1/F2（MIS-156/157）：Pages 面板、确认式学习，后续 Backlog。

## 已知限制（如实）

- 脱网环境未端到端执行宿主 CommandFilter 分发链（需完整
  PipelineContext）；已覆盖真实注册与真实绑定层方法（V22 实机覆盖）。
- V21 模型遵从度未实测；注入文本措辞已含"不发起/即停"约束但效果
  属实机验收。

## 二轮返工（0491cc9 → 2fdc6335ac35fbf4e0b82bfaee4f7c6bf2a1c858，含 6259829 修复提交与文档定稿提交）

- Codex 二轮确认原六反例已修、接受 R4 旧替身说明；本轮修复 7 个
  remaining 场景（4.28 五个/4.26 七个复现 → 双版本全部 0 复现）。
- 新增 tests/r3_rework_check.py（10 断言）与 tests/p7_lifecycle_check.py
  （11 断言，真实 PluginManager.load 完整加载/分发/停用/取消）。
- 全量：双 venv 各 11 脚本 211 PASS 0 FAIL；旧提交对照 r3 4/10、
  p7 9/11 失败。统计订正：旧候选为 190/版（非 194）。

## 三轮返工（8de2365 → 5ffc4ce）

- Codex 三轮确认 211×2 与前两轮 13 反例修复，判 T1–T4。
- T1 删 ExpirableTextPart（全局 text 注册污染）；T2 on_agent_begin(-1000)
  运行时消息终检；T3 损坏状态降级；T4 ACCEPTANCE 重建（4.8MB→7KB）。
- t_rework_check 16 断言双 venv 16/16（旧提交 1/16）；serialization_repro
  新副本 T1/T3/T4 双版本 0 复现，T2 探针替身差异经宿主源码证据说明。
- 全量 12 脚本双 venv 各 228 PASS 0 FAIL。

## 四轮返工（c77dd0d → 9ab9b2c+）

- Codex 四轮确认 T1/T3/T4 关闭、接受旧 T2 夹具说明；判 T2a（压缩/
  late-hook 等待窗口）、T2b（真实 turn_off_plugin 后 DB 关闭异常被吞）、
  T5（前缀匹配误删同标题内容）。
- 修复：推式失效（store/ObservableConfig 回调 + TurnRegistry）、
  fail-closed、activated 纳入校验、全文+对象身份精确归属。
- u_rework_check 9 断言双 venv 9/9（旧 2/9）；lifecycle_repro 新副本
  双版本 17 场景全绿；全量 13 脚本双 venv 各 237 PASS。

## 五轮返工（a06a5e4 → ba571a9+）

- Codex 五轮确认推式失效有效、四轮 17 场景保持；判 T5a（finalize
  前缀残留）、T5b（全文相等非归属）、T6（完成轮次不释放）。
- 修复：对象身份/「全文+temp 标记+数量」双凭证、终态释放钩子。
- v_rework_check 10 断言双 venv 10/10（旧 2/10）；ownership 双版本
  四反例 0 复现；lifecycle 17 场景保持全绿；全量 14 脚本各 247 PASS。

## 六轮返工（a2378c6 → edc5a75+）

- Codex 六轮判 T5b（同文 temp 误删）、T6a（装饰过早释放回归）、
  T6b（stop 中止/取消不释放）。
- 修复：位置映射归属、装饰条件回收、task-done 回调+弱引用回收。
- w_rework 9 断言双 venv 9/9（旧 6/9）；terminal_repro 双版 8 场景
  全绿；lifecycle 17×2、ownership 4×2 保持；全量 15 脚本各 256 PASS。
