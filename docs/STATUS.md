> **2026-09-20 当前状态：指定宿主 v3 组合的 A0 已独立验收通过，A1 待实机。以下保留历史阶段记录；其中“待复验 / 未通过 / 受阻”须结合当时版本阅读。请先读[当前发行说明](RELEASE_STATUS.md)。**

# STATUS — astrbot_plugin_preference_profile

最后更新：2026-09-19（十三轮收尾候选 0.1.12）

## 当前状态：N1/N2/N3 收尾完成（重建入口保护 + 清单哈希门 + 映射寿命管理），双版单一入口全清单 ALL_OK，待 Codex 复验；A0 未通过

十三轮（9517bd4 版本提交 → 0.1.12 文档定稿）：Codex 第十二轮确认 M1/M2/M3 通过、七 T5b 消失后，仅处理三个收尾项。

- **N1 重建入口保护来源与已有目录**（`rebuild_and_test.py` v3）：work 输出约束为「尚不存在、与任一来源（venv/插件仓库/协作仓库/冻结探针与复核目录）无重叠」的新目录——等于来源、是来源祖先、位于来源内部、普通已有目录一律拒绝，拒绝路径零删除；先完整校验全部输入（宿主原始/补丁/清单哈希/插件提交/协作固定提交可达）后才创建输出；已存在目录内的 canary 在拒绝前后保持不变。
- **N2 重建固定版本并校验交付**：机器可读清单 `combo_manifest.json`（schema combo-manifest/1）记录宿主原始/补丁/应用后逐文件哈希、插件提交与 git 树哈希、协作固定提交（relation 913ca59 / uctx d8a7147+0001 应用后树哈希）与补丁哈希；重建一律从固定 git 提交 `git archive` 导出（不依赖当前检出）；探针输出逐字段解析（token/boundary 等 JSON 行校验 `defect_reproduced` 字段、identity 探针按自身 7 字段逐场景、E1 按数组 4 场景校验 final_role/final_text、m_rework 校验 PASS 4/FAIL 0）；结果记录退出码/场景数/缺陷数/终态/导入路径/组合逐文件哈希（summary.json）。
- **N3 来源映射寿命管理**：宿主补丁 v3——`_bind_extra_runtime_pairs` 的映射条目改为 `(源对象, weakref.ref(运行时实例))`（runtime 侧弱引用不延长最终实例寿命，src 侧强引用与请求同寿命），并设 `_extra_runtime_channel = "identity"` 能力标记；插件侧 `_lock_channel`/m_rework 对条目做 callable 探测兼容解引用，identity 分支按源归属过滤置空运行时实例（不误删他人条目）；`_blank_record` 经请求映射同时置空运行时实例并锁定 identity 通道；`release()` 终态清空 `req._extra_runtime_pairs` 条目；清理「-1000 必在所有合法钩子之前」过宽表述为「相对排序较早、不保证先于所有合法钩子」。新增 `tests/n3_lifetime_check.py` 寿命对照（DONE/真实 ERROR/真实 Task 取消/正式停用/默认关闭五场景）：终态后 registry=0、映射条目=0、运行时实例弱引用死亡；调用方保留 request 引用的场景归因明确。
- **验证（双版单一入口全清单 ALL_OK，11 项 0 缺陷）**：boundary(8)、token(12)、corrected(6)、remaining(6)、composition(9)、terminal(8)、ownership(4)、lifecycle(17)、identity_map(4)、evidence(4)、m_rework(4)。gate 负例 8/8 拒绝（work==plugin/venv/内部/祖先/已存在、错宿主哈希、错 tag、缺失协作提交），已存在目录 canary 保持。旧组合真实行为失败对照：原生宿主+0.1.12 下 token_repro 12 场景 **7 缺陷复现**（clear/admin_off 组）→ 新组合 0 缺陷；原生 m_rework PASS 2/FAIL 2（M1/M3 受阻如实）；原生宿主 16 脚本 264 PASS 保持。
- **交付物（v3）**：`偏好管理-宿主来源映射原型-20260919/`——host_patch_v3_{428,426}.diff、patched_host_{428,426}/、combo_manifest.json（机器可读哈希清单）、rebuild_and_test.py（N1 保护 + N2 哈希门的单一重建入口，双版 ALL_OK）、rebuild_and_test_v2_frozen.py（v2 冻结入口留档）。插件包 0.1.12 见 dist/SHA256SUMS.txt；**安装包不含宿主补丁**——单独安装插件在原生宿主仍受限（identity 通道不可用，回退令牌行为保持 0.1.9 一致），宿主补丁须按 N2 清单另行应用。A0 仍待 Codex 复验与宿主侧接入决策，Goal 结束不等于 A0 通过。

### 历史轮次摘要

十二轮（b22ae0b 时点复验）：Codex 确认 M1/M2/M3 通过、七 T5b 消失，
判定收尾 N1（重建入口任意 rmtree）/ N2（哈希不校验+协作漂移）/
N3（映射强引用不释放）；冻结材料 `偏好管理-独立复核-b22ae0b-20260919/`。

十一轮（6d73bb5 → 22aac1a + 文档，0.1.11）：Codex 判 M1/M2/M3 三项 P2 后
定向修复——M1 通道锁定（on_agent_begin 一次性判定归属通道，锁定后不回退
令牌）、M2 快照语义（宿主补丁 v2 恢复 extras 序列化→重建原生语义，
model_validate 后建源→运行时实例映射）、M3 组装等待中停用
（`_source_invalidated` 标记 + 映射绑定处补偿置空）；隔离副本双版 boundary
八场景 0 defect、token12/历史50 双版 0、m_rework 4/4、原生 264 保持。

十轮（8a8bdbf → dabaa61/6d73bb5，0.1.10）：宿主来源映射原型 v1（七误删
双版 0 复现首次达成）+ 边界三缺陷交付。九轮（47bc1c9 → 154e8da/8a8bdbf，
0.1.9）：证据收尾通过、T7 复核通过、T5b 接口受阻已接受。历史 composition
适配探针基线：a2378c6 每版 1 缺陷、f77d345 每版 6、abdba20 起 0。

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
