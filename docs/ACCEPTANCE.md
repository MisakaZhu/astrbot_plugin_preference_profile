# ACCEPTANCE — 验收矩阵映射（P6 定稿）

- 运行环境：本机共享 venv（AstrBot 4.28.0 / 4.26.0，Python 3.12.10），
  脱网、合成数据、可控假模型；uctx 组合使用 git clone 隔离副本（基线
  d8a7147 + patches/0001）。
- 复现命令（cwd=仓库根；p3_import_check/p6 需 cwd=父目录或见文件头说明）：
  - `D:/第三方插件完善/.venv/Scripts/python.exe tests/<脚本>.py`
  - `D:/第三方插件完善/.venv426/Scripts/python.exe tests/<脚本>.py`
- 证据对应提交：P6 定稿时的最终提交见 git log（修改代码后本表须重跑更新）。

| 编号 | 行为 | 证据（脚本·断言） | 4.28.0 | 4.26.0 |
| --- | --- | --- | --- | --- |
| V01 | 默认关闭不采集 | p2·D01；p3·C03/C04；p4·H4a；p6·G5（数据目录删除后默认关） | PASS | PASS |
| V02 | 平台/机器人/人格/用户隔离 | p1·I1a-I1e/I2a-I2e；p4·H4e（他人不注入）；真实事件→身份在 p4/p5 全链路使用 | PASS | PASS |
| V03 | 群聊不读写/展示；伪造不越权 | p3·C01/C02（值不回显）；p4·H4b（不注入）；p6·G2/G3c（绑定层）；sender 取自宿主结构（FakeEvent 同构） | PASS | PASS |
| V04 | 模板与用户档案分离；权限边界 | p1·E3a-E3c；p3·C17-C24；p6·G3a/G3d | PASS | PASS |
| V05 | 状态/方向独立；非法输入被拒 | p1·E1/E2a-E2i；p2·D05-D12 | PASS | PASS |
| V06 | 冲突/禁止/强度/关系暂停优先 | p2·D06-D12/R01-R04；p5·RA8（真实快照联动压制） | PASS | PASS |
| V07 | 持久化/事务/revision 冲突 | p1·E4/E5a-E5e/E6 | PASS | PASS |
| V08 | 关闭/删除失效；慢请求不复活 | p1·E6（epoch）；p3·C05/C06/C13-C16；p4·H5（off 后新轮次不注入） | PASS | PASS |
| V09 | 真实宿主命令；群聊仅引导；管理不进模型 | p6·G1-G3（真实绑定层方法）；p3·C01/C02；命令轮次无 LLM 请求（结构上不触发 on_llm_request） | PASS | PASS |
| V10 | 真实请求可见偏好；无关不强套；无额外调用 | p4·H7a/H7b（真实 Runner 一次调用含偏好块）、H8（无数据零干预） | PASS | PASS |
| V11 | 注入去重/上限/不覆盖他人提示 | p4·H2c（不改 system/contexts/tools/conversation）、H3（不双注）；p2·B01/B09（预算） | PASS | PASS |
| V12 | 临时块不入持久化；回复/轨迹隔离 | p0·F5（真实 _save_to_history 跳过）；p4·H2b/H7c；p5·B1/B3（uctx 账本级隔离） | PASS | PASS |
| V13 | Relation Arc 各形态 + 原语义回归 | p5·RA1-RA8（正常/缺失/无账户/过期/paused/只读证明）；原库不写（RA7）；六维/绑定不由本插件触碰（结构只读） | PASS | PASS |
| V14 | 偏好私聊不入共享库；群聊读不到 | p5·B1d/B3a/B3b（同身份后续读历史：无偏好轮次、有普通轮次） | PASS | PASS |
| V15 | 钩子顺序/重试/流式/失败/取消 | p0·F2/F3（顺序与标志）；p4·H3、p5·B5（重试）；p6·S1（流式）/S2（模型失败）/S3（恢复）；uctx abort 轨道由其 decorating_result 兜底（补丁不触及该轨） | PASS | PASS |
| V16 | 缺协议先禁用并解释 | p4·H6c/H6d；p5·B6（未打补丁 uctx 在场 → 私人注入禁用）；命令层说明文案（commands.py status/on 提示） | PASS | PASS |
| V17 | 重载/重启/停用/并发后恢复 | p1·E4（重启持久化）；p6·G4a-G4d（terminate→重载全恢复）；p6·G6b（无类级可变状态） | PASS | PASS |
| V18 | 双版本真实包集成与干净装/卸 | 全部脚本双 venv 通过；p6·G5（数据目录删除重建）/G6a（安装件完整）；p3_import_check（真实注册） | PASS | PASS |
| V19 | 交付物无真实数据泄漏 | 最终提交时：`git ls-files` 全量核对仅源码/文档/测试/补丁；ZIP 解包 35 项清单核对无 db/log/凭据/venv/宿主源码/.git；内容扫描长数字均为代码常量（busy_timeout/max_context_tokens）或合成值（10001/99999） | PASS（P7） | PASS（P7） |
| V20 | SHA/补丁/证据一致性 | ZIP 与补丁的 SHA-256 以**包外** dist/SHA256SUMS.txt、patches/SHA256SUMS.txt 为准（避免包内自引用失真）；包内容与最终提交一致（重打包于文档定稿后）；全量回归在最终提交复跑通过（见 STATUS 阶段表） | PASS（P7） | PASS（P7） |

## 待实际验收（不计入自动化 PASS）

| 编号 | 行为 | 责任 |
| --- | --- | --- |
| V21 | 实际模型对方向/节奏差异的体现、冲突限制有效性 | MIS-155（用户实机 + Codex 复核） |
| V22 | 测试 QQ 私聊→群聊、多人/多人格隔离、停用恢复 | MIS-155（用户实机 + Codex 复核） |

## 已知边界与如实声明

- 命令解析链（宿主 CommandFilter → handler 分发）未在脱网环境端到端执行
  （需完整 PipelineContext）；已覆盖真实 registry 注册（p3_import_check）
  与真实绑定层方法调用（p6·G 系列）。实机命令体验归 V22。
- V13 的"原六维/绑定语义不变"由结构性只读保证（mode=ro + 无写入代码路径）
  与 RA7（字节不变）证明；Relation Arc 自身功能回归以其项目自身测试为准。
- 用户中止（abort）轨道：uctx 补丁不改变其 abort 兜底逻辑；本插件在
  失败/中止轮次的注入块为 mark_as_temp，无持久化残留（p6·S2）。
