"""请求钩子注入器（ADR-003；二轮返工 R4/R5 修复）。

唯一注入通道：req.extra_user_content_parts + TextPart.mark_as_temp()。
以 priority=20 先于 Context Bridge（默认 0）执行。

二轮返工要点：
- R4：请求侧与命令侧同一套人格解析——resolve_persona_scope 按
  provider_settings_getter(event) 取当前 UMO 作用域配置（4.26 需要
  provider_settings.default_personality；4.28 签名不接受该参数，
  按签名适配）。
- R5：追加前重校验 admin_enabled + 本人 enabled/epoch（异步边界后的
  完整失效：个人 off/clear、管理员总开关）。追加后由 main 注册的
  收尾钩子（priority=-1000，钩子链末尾、Runner 组装前）再次校验并
  移除失效的本插件块（按对象身份，不动其他插件内容）。已进入
  Runner/发出的内容不可撤回（如实边界）。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart
from pydantic import PrivateAttr

from .bridge_guard import MODE_PROTOCOL_OK, BridgeGuard
from .identity import PrefIdentity, build_identity, host_is_private_chat, resolve_persona_scope
from .policy import RelationSnapshot, TurnInput, evaluate
from .prompt_builder import render_decision
from .store import PrefStore

_DONE_EXTRA = "__pref_profile_turn_done"
PARTS_EXTRA = "__pref_profile_appended_parts"

logger = logging.getLogger("pref_profile.injection")


class ExpirableTextPart(TextPart):
    """发送时刻才决定内容的临时偏好块（R5 二轮）。

    宿主在 Runner 组装/模型发送时才调用 model_dump_for_context 序列化
    extra parts（两版一致：ProviderRequest.assemble_context →
    part.model_dump_for_context()）。失效（个人 off/clear、管理员总开关、
    epoch 变化）后序列化为空文本块——不依赖任何后续钩子被执行，也不
    触碰其他插件追加的块。已真正发出的请求仍不可撤回（如实边界）。
    """

    _validator: Optional[Callable[[], bool]] = PrivateAttr(default=None)

    type: str = "text"  # ContentPart.__init_subclass__ 要求子类显式 str type

    def arm(self, validator: Callable[[], bool]) -> "ExpirableTextPart":
        self._validator = validator
        return self

    def model_dump_for_context(self) -> dict:  # noqa: D102
        valid = False
        try:
            valid = bool(self._validator is not None and self._validator())
        except Exception:  # noqa: BLE001 - 校验异常按失效处理
            valid = False
        if not valid:
            return {"type": "text", "text": "", "_no_save": True}
        return super().model_dump_for_context()


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

        # 人格解析：钩子先于 uctx 执行，req.conversation 尚未被置空。
        # R4：请求侧同样按当前 UMO 作用域配置取 provider_settings。
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

        # R5：异步边界（relation_loader await）之后、追加之前，完整失效
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
        # 双重失效机制（R5 二轮）：
        # 1) ExpirableTextPart 在宿主发送序列化时刻校验（不依赖后续
        #    钩子被执行，钩子白名单/停用调度均不影响）；
        # 2) finalize 收尾钩子（priority=-1000）在链末尾按对象身份移除。
        part = ExpirableTextPart(text=text).arm(
            lambda: self._still_valid(identity, epoch_snapshot)
        )
        part.mark_as_temp()
        req.extra_user_content_parts.append(part)
        # 记录本插件追加块的对象身份：收尾钩子（finalize_request）在
        # 钩子链末尾、Runner 组装前做最终失效校验，失效时按身份移除
        #（仅本插件块，其他插件内容不动）。
        recorded = event.get_extra(PARTS_EXTRA) or []
        recorded.append((part, identity, epoch_snapshot))
        event.set_extra(PARTS_EXTRA, recorded)

    def finalize(self, event: Any, req: ProviderRequest) -> None:
        """收尾失效校验（priority=-1000 钩子，链末尾、Runner 组装前）。

        对本插件已追加但宿主尚未发送的块做最终校验；失效则按对象身份
        从 req.extra_user_content_parts 移除（保留其他插件块）。已进入
        Runner/已发出的内容不可撤回（如实边界，不由本方法重定义）。
        """

        recorded = event.get_extra(PARTS_EXTRA)
        if not recorded:
            return
        try:
            valid_parts = [
                part for part, identity, epoch in recorded
                if self._still_valid(identity, epoch)
            ]
            invalid = [
                part for part, identity, epoch in recorded
                if not self._still_valid(identity, epoch)
            ]
            if invalid:
                # 按对象身份移除（is 比较），绝不动其他插件的块
                req.extra_user_content_parts = [
                    p for p in req.extra_user_content_parts
                    if not any(p is bad for bad in invalid)
                ]
                event.set_extra(PARTS_EXTRA, valid_parts or None)
        except Exception:  # noqa: BLE001 - 收尾失败不中断宿主
            logger.warning("preference_profile 收尾校验失败", exc_info=True)
