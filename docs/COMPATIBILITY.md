# COMPATIBILITY — astrbot_plugin_preference_profile

## 宿主兼容矩阵（以真实包实测为准）

| 宿主 | 验证方式 | 结果 | 证据 |
| --- | --- | --- | --- |
| AstrBot 4.28.0（本机 .venv，Python 3.12.10） | tests/p0_host_contract_check.py | 12/12 PASS | P0 阶段，本地运行记录 |
| AstrBot 4.26.0（本机 .venv426，Python 3.12.10） | tests/p0_host_contract_check.py | 12/12 PASS | 同上 |

覆盖的宿主接口：ProviderRequest.extra_user_content_parts、TextPart.mark_as_temp、
star_handlers_registry priority 排序、call_event_hook、
PersonaManager.resolve_selected_persona、InternalAgentSubStage._save_to_history、
StarTools.get_data_dir、filter.permission_type / PermissionType.ADMIN。

未实测版本与其他适配器/第三方 Agent 不宣称支持。

## 协作插件矩阵

| 插件 | 基线 | 交互 | 状态 |
| --- | --- | --- | --- |
| astrbot_plugin_user_context_bridge | 8477eba | 捕获前排除协议（ADR-004），隔离副本补丁 | P5 实现与验证 |
| astrbot_plugin_relation_arc | 913ca59 | 只读 SQLite 快照（ADR-005） | P5 实现与验证 |

- 两参考仓库本轮全程只读；协作改动在隔离副本以补丁交付。
- uctx 在场且未打补丁 → 私人偏好注入禁用（保守降级，原因码
  `uctx_protocol_missing`）；uctx 不在场 → 独立功能完整可用。
- Relation Arc 缺失/版本未知/读取异常 → 关系限制不生效，偏好独立可用。

## 声明边界

- 服务器/数据库运维人员仍可能读取落盘数据；不宣称端到端保密。
- 已送模型内容不可撤回；宿主聊天/共享库/备份的清理按各自既有途径。
