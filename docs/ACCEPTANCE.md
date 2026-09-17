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
| V02 | 平台/机器人/人格/用户隔离 | p1·I1a-I1e/I2a-I2e；p4·H4e；r_rework·RR4a/RR4b（真实解析算法下命令与请求同人格） | PASS | PASS |
| V03 | 群聊不读写/展示；伪造不越权 | r_rework·RR1a-RR1e（真实事件+CommandFilter+call_handler：群聊仅通用引导、零注入、私聊对照正常）；p3·C01/C02 | PASS | PASS |
| V04 | 模板与用户档案分离；权限边界 | p1·E3a-E3c；p3·C17-C24；p6·G3a/G3d | PASS | PASS |
| V05 | 状态/方向独立；非法输入被拒 | p1·E1/E2a-E2i；p2·D05-D12；r_rework·RR6b（九方向组合唯一） | PASS | PASS |
| V06 | 冲突/禁止/强度/关系暂停优先 | p2·D06-D12/R01-R04；r_rework·RR3a-RR3d（真实 RelationStore 管理员/timed/global） | PASS | PASS |
| V07 | 持久化/事务/revision 冲突 | p1·E4/E5a-E5e/E6 | PASS | PASS |
| V08 | 关闭/删除失效；慢请求不复活 | p1·E6；p3·C05/C06/C13-C16；p4·H5；r_rework·RR5a（await 边界 clear 后不注入）/RR5b（对照） | PASS | PASS |
| V09 | 真实宿主命令；群聊仅引导；管理不进模型 | r_rework·RR1（真实 CommandFilter.filter + call_handler 分发）；p6·G1-G3（正式构造绑定层） | PASS | PASS |
| V10 | 真实请求可见偏好；无关不强套；无额外调用 | p4·H7a/H7b（真实 Runner 一次调用含偏好块）、H8（无数据零干预） | PASS | PASS |
| V11 | 注入去重/上限/不覆盖他人提示 | p4·H2c（不改 system/contexts/tools/conversation）、H3（不双注）；p2·B01/B09（预算） | PASS | PASS |
| V12 | 临时块不入持久化；回复/轨迹隔离 | p0·F5（真实 _save_to_history 跳过）；p4·H2b/H7c；p5·B1/B3（uctx 账本级隔离） | PASS | PASS |
| V13 | Relation Arc 各形态 + 原语义回归 | r_rework·RR3a-RR3d（真实管理员 API/timed 过期/global）；p5·RA1-RA8；只读证明（RA7 + 纯 SELECT 过期判定） | PASS | PASS |
| V14 | 偏好私聊不入共享库；群聊读不到 | r_rework·RR2a-RR2d（正式构造+真实 star_map：protocol_ok/标志已写/uctx 不接管）；p5·B1-B6 | PASS | PASS |
| V15 | 钩子顺序/重试/流式/失败/取消 | p0·F2/F3；r_rework·RR2（装卸/停用路径）；p4·H3、p5·B5（重试）；p6·S1/S2/S3（正式构造下流式/失败/恢复） | PASS | PASS |
| V16 | 缺协议先禁用并解释 | r_rework·RR2e（停用→no_bridge）/RR2f（无协议→不注入）；p4·H6c/H6d；p5·B6 | PASS | PASS |
| V17 | 重载/重启/停用/并发后恢复 | p6·G4a-G4d（正式构造 terminate→重载）；r_rework·RR2e（协作插件停用路径）；p1·E4 | PASS | PASS |
| V18 | 双版本真实包集成与干净装/卸 | 全部脚本（含正式构造 p6 与 r_rework）双 venv 通过；p6·G5/G6a；p3_import_check | PASS | PASS |
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
