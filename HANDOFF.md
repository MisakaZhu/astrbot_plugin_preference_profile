# HANDOFF — astrbot_plugin_preference_profile v0.1.0

交接日期：2026-09-17。交付状态：**P0–P7 全部完成（In Review），
本地候选可供 Codex 独立验收（A0/MIS-154）**。未宣称实机、云端或发布完成。

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
