# STATUS — astrbot_plugin_preference_profile

最后更新：2026-09-17（R1–R6 返工候选）

## 当前状态：R1–R6 返工完成（P0–P7 + 返工均 In Review），新候选待 Codex 独立复验（A0）

返工轮（0c32cee → 新提交）：Codex 复核 FAIL 后修复六项缺陷，
详见 HANDOFF 返工章节与 tests/r_rework_check.py。

- 分支 `main`，最终提交与工作区状态以 `git log --oneline -1` 与
  `git status --short` 实查为准；P0–P7 各阶段提交见 git log。
- 安装包：`dist/astrbot_plugin_preference_profile-0.1.0.zip`（SHA-256
  见 `dist/SHA256SUMS.txt`）。
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

全量回归（最终提交）：4.28.0 与 4.26.0 各 8 脚本 165 项断言 0 FAIL 0 跳过。
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
