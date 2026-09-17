"""请求钩子注入器（ADR-003；三轮返工 T1/T2 修复）。

唯一注入通道：req.extra_user_content_parts + **宿主原生** TextPart +
mark_as_temp()。以 priority=20 先于 Context Bridge（默认 0）执行。

三轮返工要点（T1/T2）：
- **不再定义 TextPart 子类**——宿主 ContentPart.__init_subclass__ 会把
  子类写入全局类型注册表，仅导入插件就会把普通 text 类型替换掉，
  导致其他插件/历史摘要的普通文本被误判失效清空（Codex T1）。失效
  机制全部改用"内容前缀识别 + 钩子时机清理"，不触碰宿主类型注册。
- 失效时机三道防线：
  1) 追加前 `_still_valid`（admin + enabled + epoch）复查；
  2) finalize_request（priority=-1000，钩子链末尾、Runner reset 前）
     按前缀从 req.extra_user_content_parts 移除本插件块；
  3) on_agent_begin（真实 Agent 钩子：Runner reset 完成后、首次
     Provider 调用前）对 run_context.messages 中已固化的本插件块按
     前缀定位并置空文本——覆盖 reset 之后、发送之前的窗口（T2）。
- 本插件块以固定前缀 HEADER_PREFIX 识别；只动自己的块，普通输入与
  其他插件内容不受影响。
- OnAgentBegin 返回后到首次 Provider 调用之间不存在插件可介入的
  宿主钩子点（接口缺口，如实声明）；已真正发出的请求不可撤回。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart

from .bridge_guard import MODE_PROTOCOL_OK, BridgeGuard
from .identity import PrefIdentity, build_identity, host_is_private_chat, resolve_persona_scope
from .policy import RelationSnapshot, TurnInput, evaluate
from .prompt_builder import HEADER, render_decision
from .store import PrefStore

_DONE_EXTRA = "__pref_profile_turn_done"
STATE_EXTRA = "__pref_profile_state"
HEADER_PREFIX = HEADER[: HEADER.index("】") + 1]  # 「【互动边界参考（系统，仅本轮）】」

logger = logging.getLogger("pref_profile.injection")


def is_own_part(part: Any) -> bool:
    """识别本插件追加的文本块（固定前缀，不依赖类型注册）。"""

    return (
        isinstance(part, TextPart)
        and bool(getattr(part, "text", ""))
        and str(part.text).startswith(HEADER_PREFIX)
    )


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
        provider_settings_getter: Optional[Callable[[Any], Optional[dict]]] = None,
    ):
        self._store = store
        self._config = config
        self._get_persona_manager = persona_manager_getter
        self._bridge = bridge_guard
        self._relation_loader = relation_loader
        self._provider_settings_getter = provider_settings_getter

    def _still_valid(self, identity: PrefIdentity, epoch_snapshot: int) -> bool:
        """提交前失效校验：管理员总开关 + 本人开关 + epoch。"""

        if not bool(self._config.get("admin_enabled", False)):
            return False
        enabled_now, epoch_now = self._store.get_user_state(identity.key)
        return bool(enabled_now) and epoch_now == epoch_snapshot

    def _still_valid_key(self, identity_key: str, epoch_snapshot: int) -> bool:
        if not bool(self._config.get("admin_enabled", False)):
            return False
        enabled_now, epoch_now = self._store.get_user_state(identity_key)
        return bool(enabled_now) and epoch_now == epoch_snapshot

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

        provider_settings = None
        if self._provider_settings_getter is not None:
            try:
                provider_settings = self._provider_settings_getter(event)
            except Exception:  # noqa: BLE001 - 配置读取失败按未提供处理
                provider_settings = None
        persona_scope = await resolve_persona_scope(
            self._get_persona_manager(),
            event,
            getattr(req, "conversation", None),
            provider_settings=provider_settings,
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
                event.set_extra(_DONE_EXTRA, True)
                return

        if not self._bridge.injection_allowed():
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

        # T2：异步边界（relation_loader await）之后、追加之前，完整失效
        # 校验（个人 off/clear 与管理员总开关均覆盖）。
        if not self._still_valid(identity, epoch_snapshot):
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
                admin_enabled=bool(self._config.get("admin_enabled", False)),
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
        # 宿主原生 TextPart（T1：不定义子类，不触碰全局类型注册）。
        # 失效状态记入 event extra，供 finalize / on_agent_begin 复核。
        part = TextPart(text=text).mark_as_temp()
        req.extra_user_content_parts.append(part)
        event.set_extra(
            STATE_EXTRA, {"identity_key": identity.key, "epoch": epoch_snapshot}
        )

    def finalize(self, event: Any, req: ProviderRequest) -> None:
        """收尾失效校验（priority=-1000 钩子，链末尾、Runner reset 前）。

        按前缀移除本插件已追加但宿主尚未组装的块（保留其他插件内容）。
        """

        try:
            state = event.get_extra(STATE_EXTRA)
            if not state:
                return
            if not self._still_valid_key(state["identity_key"], state["epoch"]):
                req.extra_user_content_parts = [
                    p for p in req.extra_user_content_parts if not is_own_part(p)
                ]
                event.set_extra(STATE_EXTRA, None)
        except Exception:  # noqa: BLE001 - 收尾失败不中断宿主
            logger.warning("preference_profile 收尾校验失败", exc_info=True)

    def invalidate_runtime_messages(self, event: Any, run_context: Any) -> int:
        """on_agent_begin 钩子（T2）：Runner reset 完成后、首次 Provider
        调用前，对 run_context.messages 中已固化的本插件块做最终校验。

        失效时将该 TextPart.text 置空（保持消息结构与其他内容不变，
        后续所有 Provider 调用复用修改后的 messages）。返回清理数量。
        """

        cleaned = 0
        try:
            state = event.get_extra(STATE_EXTRA)
            if not state:
                return 0
            if self._still_valid_key(state["identity_key"], state["epoch"]):
                return 0
            messages = getattr(run_context, "messages", None) or []
            for message in messages:
                content = getattr(message, "content", None)
                if not isinstance(content, list):
                    continue
                for part in content:
                    if is_own_part(part):
                        part.text = ""
                        cleaned += 1
            if cleaned:
                event.set_extra(STATE_EXTRA, None)
        except Exception:  # noqa: BLE001 - 清理失败不中断 Agent
            logger.warning("preference_profile 运行时失效清理失败", exc_info=True)
        return cleaned
