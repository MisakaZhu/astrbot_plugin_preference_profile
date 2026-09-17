"""请求钩子注入器（ADR-003）。

唯一注入通道：req.extra_user_content_parts + TextPart.mark_as_temp()。
以 priority=20 先于 Context Bridge（默认 0）执行：人格读取发生在
req.conversation 被其置空之前；排除标志在其捕获（begin_turn 写库）之前
打上。幂等（event extra 标记）；不触碰 system_prompt / contexts /
func_tool / unified_msg_origin；不发起任何额外模型调用。

返工加固（Codex 复核 R1/R2/R5）：
- 私聊判定统一走 host_is_private_chat（真实宿主是方法）；
- 排除标志写入失败（protocol_ok 但 mark_excluded 返回 False）→ 本轮
  不注入私人偏好（保守）；
- 追加注入块之前重新校验本人 enabled 与 epoch（异步边界后的失效，
  off/clear/停用使未提交快照失效）。append 即视为提交：此后请求进入
  宿主 Runner，已送出内容不可撤回（如实边界）。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart

from .bridge_guard import MODE_PROTOCOL_OK, BridgeGuard
from .identity import PrefIdentity, build_identity, host_is_private_chat, resolve_persona_scope
from .policy import RelationSnapshot, TurnInput, evaluate
from .prompt_builder import render_decision
from .store import PrefStore

_DONE_EXTRA = "__pref_profile_turn_done"

logger = logging.getLogger("pref_profile.injection")


class PreferenceInjector:
    def __init__(
        self,
        store: PrefStore,
        config: dict,
        persona_manager_getter: Callable[[], Any],
        bridge_guard: BridgeGuard,
        relation_loader: Optional[
            Callable[[PrefIdentity, Any], Awaitable[RelationSnapshot]]
        ] = None,
    ):
        self._store = store
        self._config = config
        self._get_persona_manager = persona_manager_getter
        self._bridge = bridge_guard
        self._relation_loader = relation_loader

    async def handle(self, event: Any, req: ProviderRequest) -> None:
        """on_llm_request 钩子体。任何内部异常都不得中断宿主请求。"""

        try:
            await self._handle_inner(event, req)
        except Exception:  # noqa: BLE001 - 注入失败按不注入处理
            logger.warning("preference_profile 注入处理失败", exc_info=True)

    async def _handle_inner(self, event: Any, req: ProviderRequest) -> None:
        if event.get_extra(_DONE_EXTRA):
            return  # 同一事件对象重试不双注

        if not bool(self._config.get("admin_enabled", False)):
            return
        if not host_is_private_chat(event):
            return

        # 人格解析：钩子先于 uctx 执行，req.conversation 尚未被置空
        persona_scope = await resolve_persona_scope(
            self._get_persona_manager(), event, getattr(req, "conversation", None)
        )
        if persona_scope is None:
            return  # 解析失败不落默认人格
        identity = build_identity(
            platform_id=str(event.get_platform_id() or ""),
            self_id=str(event.get_self_id() or ""),
            persona_scope=persona_scope,
            sender_id=str(event.get_sender_id() or ""),
        )

        enabled, epoch_snapshot = self._store.get_user_state(identity.key)
        if not enabled:
            return

        # 已启用偏好的私聊轮次：与是否实际注入文本无关，均需排除共享
        if self._bridge.mode == MODE_PROTOCOL_OK:
            if not self._bridge.mark_excluded(event):
                # 标志写入失败：无法保证捕获前排除 → 保守禁用（R2）
                event.set_extra(_DONE_EXTRA, True)
                return

        if not self._bridge.injection_allowed():
            # uctx 在场但无排除协议：保守禁用私人注入（V16）
            event.set_extra(_DONE_EXTRA, True)
            return

        user_entries = self._store.list_entries("user", identity.key)
        bot_identity = build_identity(
            platform_id=identity.platform_id,
            self_id=identity.self_id,
            persona_scope=identity.persona_scope,
        )
        bot_entries = self._store.list_entries("bot", bot_identity.key)
        if not user_entries and not bot_entries:
            event.set_extra(_DONE_EXTRA, True)
            return

        relation = None
        if self._relation_loader is not None and self._config.get(
            "relation_link_enabled", True
        ):
            relation = await self._relation_loader(identity, event)

        # R5：异步边界（relation_loader await）之后、真正提交注入之前，
        # 重新校验本人开关与 epoch；off/clear/停用使未提交快照失效。
        enabled_now, epoch_now = self._store.get_user_state(identity.key)
        if not enabled_now or epoch_now != epoch_snapshot:
            event.set_extra(_DONE_EXTRA, True)
            return

        try:
            max_items = int(self._config.get("max_inject_items", 6))
            max_chars = int(self._config.get("max_inject_chars", 600))
        except (TypeError, ValueError):
            max_items, max_chars = 6, 600
        max_items = min(max(1, max_items), 10)
        max_chars = min(max(100, max_chars), 2000)

        decision = evaluate(
            TurnInput(
                admin_enabled=True,
                is_private=True,
                user_enabled=True,
                user_entries=user_entries,
                bot_entries=bot_entries,
                relation=relation,
            ),
            max_items=max_items,
        )
        text = render_decision(decision, max_chars=max_chars)
        event.set_extra(_DONE_EXTRA, True)
        if text is None:
            return
        # append 即视为本轮提交：此后由宿主 Runner 发送，不可撤回；
        # 失效校验已在上方完成（R5）。
        req.extra_user_content_parts.append(TextPart(text=text).mark_as_temp())
