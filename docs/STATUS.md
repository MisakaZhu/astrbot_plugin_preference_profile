# STATUS — astrbot_plugin_preference_profile

最后更新：2026-09-17（P0 收尾）

## 当前状态：P0 完成，待 P1

- Git：main 分支；首笔提交见 `git log`；工作区以 `git status --short` 为准。
- Linear：MIS-146 In Progress → 本阶段自测完成后置 In Review 并继续 P1。

## P0 已完成

1. 现场核对：目标目录原不存在（全新初始化）；父目录 D:\第三方插件完善 非 Git
   仓库；参考仓库 HEAD 与规划记录一致（relation_arc 913ca59 / uctx 8477eba，
   两仓库 `git status --short` 均干净）。
2. 独立 Git main 初始化，仓库级作者身份沿用本机既有插件提交身份
   （Ewnscat-ya <317573784+Ewnscat-ya@users.noreply.github.com>，未改全局配置）。
3. 先建 .gitignore（数据/环境/产物隔离），再显式跟踪文档与测试。
4. 真实宿主验证（两个共享 venv，脱网、合成数据）：
   `tests/p0_host_contract_check.py` 12/12 PASS × 4.28.0 与 4.26.0：
   - F1 mark_as_temp 临时块序列化语义（_no_save 传播）
   - F2 钩子 priority 降序执行（20 先于 0）
   - F3 真实 call_event_hook 链上高优先级 set_extra → 低优先级可读
     （uctx 捕获前排除协议机制核心）
   - F4 resolve_selected_persona 两版签名一致
   - F5 真实 InternalAgentSubStage._save_to_history 跳过 _no_save 消息；
     conversation=None 短路写回（与 uctx 行为对齐）
   - F6 extra part 独立于 prompt 本体
5. 合同冻结：docs/ADR.md ADR-001..008（范围/身份/注入/uctx 排除协议/
   Relation 快照/存储/决策顺序/命令面）。
6. 宿主事实核对（源码级，供 ADR 引用）：
   - 内置 Agent：OnWaitingLLMRequest → build_main_agent（人格在此解析）→
     OnLLMRequest(req) → Runner → _save_to_history（internal.py:225/239/277）。
   - uctx 捕获链：on_llm_request(priority=0) 内 begin_turn 即写库 +
     req.conversation=None；on_decorating_result 唯一提交点。
     排除必须发生在其钩子之前 → 高优先级钩子 + event extra 标志（已实测可行）。
   - Relation Arc 无对外程序化 API；直读其 SQLite（accounts/timed_safety）
     为只读联动路径，身份映射 platform:sender，双 scope 探测。
   - 插件数据目录：StarTools.get_data_dir（两版一致）。
   - 管理员权限：filter.permission_type(PermissionType.ADMIN)。

## P0 待办（无阻塞；移交后续阶段）

- Context Bridge 隔离副本补丁（P5）：按 ADR-004 制作并回归。
- Relation Arc 快照读取实现与组合测试（P5）。

## 下一步：P1（MIS-147）

实现 pref_profile/identity.py、store.py 及其真实宿主测试（V01/V02/V04/V05/V07
对应的存储层断言）。
