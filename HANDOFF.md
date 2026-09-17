# HANDOFF — astrbot_plugin_preference_profile v0.1.1（返工候选）

交接日期：2026-09-17（返工轮）。交付状态：**R1–R6 已修复，本地门槛
满足的新候选，待 Codex 独立复验（A0/MIS-154）**。未宣称实机、云端或
发布完成。

## 0. 返工摘要（0c32cee → 本候选）

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
