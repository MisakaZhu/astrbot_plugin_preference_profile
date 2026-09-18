"""请求钩子注入器（ADR-003；四轮返工 T2a/T2b/T5 修复）。

唯一注入通道：req.extra_user_content_parts + **宿主原生** TextPart +
mark_as_temp()。以 priority=20 先于 Context Bridge（默认 0）执行。

四轮返工要点：
- **推式失效（T2a）**：-1000 等钩子优先级只是相对排序，不是"链末尾"
  保证——宿主在 AgentBegin 之后还有 ContextManager/LLMSummaryCompressor
  的真实 await 窗口与更低优先级的合法后续钩子。因此失效不再依赖"下一
  个检查点"，而是由**失效动作本身主动清理**：TurnRegistry 登记每个在途
  轮次（event、req 上的块对象、run_context 引用、注入全文与数量）；
  PrefStore 的 clear/off 写入与 ObservableConfig 的管理开关写入都同步
  触发 registry 清理（等待窗口中的运行时消息被立即置空）。
- **停用/卸载（T2b）**：terminate 主动 purge_all（在关闭 DB 之前）；
  清理钩子读取已关闭 DB 抛异常时按 fail-closed 处理（校验失败=不可信
  =置空本插件块），绝不因异常放行旧内容。
- **精确归属（T5）**：不按可见前缀匹配任意消息。注入时记录块的
  **完整文本与数量**为归属凭证；运行时清理只置空 text 与记录全文
  完全相等且不超过登记数量的块——用户引用/其他插件的同标题不同
  尾文不会被误删；组装前（finalize）按登记的**对象身份**从 req 移除。
- 插件激活状态纳入 _still_valid（star_map.activated=False → 失效）；
  OnAgentBegin 返回后到首次 Provider 调用之间仍无插件钩子点（接口
  缺口，如实声明）；已真正发出的请求不可撤回。

五轮返工要点（T5a/T5b/T6）：
- **统一归属（T5a/T5b）**：全部清理入口（finalize 正常/异常、推式
  清理、AgentBegin 终检）一律以 TurnRecord 登记凭证为准——组装前按
  **对象身份**（record.parts）；组装后按 **全文相等 + _no_save 临时
  标记 + 数量上限**（用户原文/其他插件普通块无 temp 标记，即使文本
  完全相同也保留；mark_as_temp 经宿主序列化链 model_dump_for_context
  → Message.model_validate 重建后保留在新 part 上，作为运行时身份
  组合凭证之一）。移除一切按可见前缀批量删除的路径（含异常兜底——
  拿不到凭证时宁可不清理也不误删）。
- **注册表生命周期（T6）**：轮次终态释放——on_agent_done（真实
  完成/失败/中止均触发）与 on_decorating_result（兜底，宿主对每条
  消息执行）按 event 释放记录的全部强引用（event/req 引用/
  run_context/texts）；finalize 对 dead 且未挂接运行时的记录同样释
  放（reset 前失效的块已从 req 移除，不会进入运行时）。在途请求的
  推式失效能力不受影响。
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


class TurnRecord:
    """一个在途轮次的归属凭证与清理句柄。"""

    __slots__ = ("event", "identity_key", "epoch", "texts", "parts", "run_context", "dead")

    def __init__(self, event: Any, identity_key: str, epoch: int):
        self.event = event
        self.identity_key = identity_key
        self.epoch = epoch
        self.texts: list[str] = []      # 注入块完整文本（归属凭证）
        self.parts: list[TextPart] = []  # req 上追加的块对象（组装前按身份移除）
        self.run_context: Any = None     # AgentBegin 时附加（组装后按全文置空）
        self.dead = False


class TurnRegistry:
    """进程内在途轮次注册表（推式失效的清理目标集合）。"""

    def __init__(self):
        self._records: dict[int, TurnRecord] = {}  # id(event) -> record
        self._by_identity: dict[str, set[int]] = {}

    def register(self, record: TurnRecord) -> None:
        key = id(record.event)
        self._records[key] = record
        self._by_identity.setdefault(record.identity_key, set()).add(key)

    def attach_runtime(self, event: Any, run_context: Any) -> None:
        record = self._records.get(id(event))
        if record is not None and record.run_context is None:
            record.run_context = run_context

    def _blank_record(self, record: TurnRecord) -> None:
        """按归属凭证清理单个记录：req 对象身份移除 + 运行时全文置空。

        运行时引用未附加（Runner reset 尚未完成 / AgentBegin 未执行）时
        **保留记录**（dead 标记），供后续 on_agent_begin 钩子按全文凭证
        再次清理；仅在 run_context 已附加并完成置空后才可丢弃。
        """

        record.dead = True
        # 组装前：按对象身份从 req 移除（不动其他插件内容）
        parts = record.parts
        if parts:
            try:
                state = record.event.get_extra(STATE_EXTRA)
                req = state.get("req") if isinstance(state, dict) else None
                if req is not None:
                    req.extra_user_content_parts = [
                        p for p in req.extra_user_content_parts
                        if not any(p is own for own in parts)
                    ]
            except Exception:  # noqa: BLE001
                pass
        # 组装后：按「全文相等 + _no_save 临时标记 + 数量上限」置空
        # （T5b：用户原文/其他插件普通块无 temp 标记，即使文本完全相同
        # 也保留——mark_as_temp 经宿主序列化链重建后保留在新 part 上）
        rc = record.run_context
        texts = list(record.texts)
        if rc is None or not texts:
            return  # 未附加运行时引用：保留记录等待 on_agent_begin 清理
        try:
            self._blank_runtime_parts(rc, texts)
        except Exception:  # noqa: BLE001 - 清理失败不中断宿主
            logger.warning("preference_profile 推式清理失败", exc_info=True)

    @staticmethod
    def _blank_runtime_parts(run_context: Any, texts: list[str]) -> int:
        """在运行时消息中置空本插件块（全文+temp 标记+数量上限）。

        返回置空数量。绝不触碰无 _no_save 临时标记的块——用户原文与
        其他插件的普通块即使文本完全相同也保留。
        """

        cleaned = 0
        remaining = len(texts)
        try:
            for message in getattr(run_context, "messages", None) or []:
                content = getattr(message, "content", None)
                if not isinstance(content, list) or remaining <= 0:
                    continue
                for part in content:
                    if remaining <= 0:
                        break
                    txt = getattr(part, "text", None)
                    if (
                        isinstance(txt, str)
                        and txt
                        and txt in texts
                        and bool(getattr(part, "_no_save", False))
                    ):
                        part.text = ""
                        remaining -= 1
                        cleaned += 1
        except Exception:  # noqa: BLE001 - 清理失败不中断宿主
            logger.warning("preference_profile 运行时块清理失败", exc_info=True)
        return cleaned

    def release(self, event: Any) -> bool:
        """释放该轮次的记录（终态：完成/失败/取消/装饰兜底）。

        丢弃全部强引用（event/req 引用/run_context/texts/parts）。
        返回是否存在记录。
        """

        record = self._records.pop(id(event), None)
        if record is None:
            return False
        bucket = self._by_identity.get(record.identity_key)
        if bucket is not None:
            bucket.discard(id(event))
            if not bucket:
                self._by_identity.pop(record.identity_key, None)
        # 显式清空强引用，阻断 record→event/run_context 的保留链
        record.parts = []
        record.texts = []
        record.run_context = None
        try:
            record.event.set_extra(STATE_EXTRA, None)
        except Exception:  # noqa: BLE001
            pass
        return True

    def purge_identity(self, identity_key: str | None) -> None:
        """失效指定身份的在途轮次；None=全部（管理员开关/停用）。"""

        targets: list[TurnRecord] = []
        if identity_key is None:
            targets = list(self._records.values())
        else:
            for key in self._by_identity.get(identity_key, ()):
                record = self._records.get(key)
                if record is not None:
                    targets.append(record)
        for record in targets:
            self._blank_record(record)
            if record.run_context is not None:
                # 运行时已清理完毕，可安全丢弃；否则保留 dead 记录，
                # 由后续 on_agent_begin 钩子（若执行）按凭证再清理。
                self._discard(record)

    def _discard(self, record: TurnRecord) -> None:
        key = id(record.event)
        self._records.pop(key, None)
        bucket = self._by_identity.get(record.identity_key)
        if bucket is not None:
            bucket.discard(key)
            if not bucket:
                self._by_identity.pop(record.identity_key, None)


class ObservableConfig(dict):
    """dict 子类：管理开关等写入同步触发推式清理（T2a admin 窗口）。

    写操作透传底层宿主配置对象（保持 save_config 语义），并触发
    on_write 回调（→ registry.purge_all）。不污染宿主类型注册。
    """

    def __init__(self, source: dict, on_write: Callable[[], None]):
        super().__init__(source)
        self._source = source
        self._on_write = on_write

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if self._source is not None and self._source is not self:
            try:
                self._source[key] = value
            except Exception:  # noqa: BLE001
                pass
        self._fire()

    def __delitem__(self, key):
        super().__delitem__(key)
        if self._source is not None and self._source is not self:
            try:
                self._source.pop(key, None)
            except Exception:  # noqa: BLE001
                pass
        self._fire()

    def update(self, *args, **kwargs):  # type: ignore[override]
        super().update(*args, **kwargs)
        if self._source is not None and self._source is not self:
            try:
                self._source.update(*args, **kwargs)
            except Exception:  # noqa: BLE001
                pass
        self._fire()

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
            return default
        return super().setdefault(key, default)

    def _fire(self):
        try:
            self._on_write()
        except Exception:  # noqa: BLE001
            pass


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
        star_map: Optional[dict] = None,
        own_module_path: Optional[str] = None,
    ):
        self._store = store
        self._config = config
        self._get_persona_manager = persona_manager_getter
        self._bridge = bridge_guard
        self._relation_loader = relation_loader
        self._provider_settings_getter = provider_settings_getter
        self._star_map = star_map
        self._own_module_path = own_module_path
        self.registry = TurnRegistry()
        # 推式失效：存储任何失效写入 → 清理对应身份在途轮次
        self._store.add_invalidation_hook(self._on_store_invalidation)

    # -- 推式失效入口 -------------------------------------------------------

    def _on_store_invalidation(self, identity_key: str | None) -> None:
        self.registry.purge_identity(identity_key)

    def on_config_invalidation(self) -> None:
        """管理开关等配置写入 → 全部在途轮次失效（ObservableConfig 回调）。"""

        self.registry.purge_identity(None)

    def purge_all(self) -> None:
        """terminate / 停用：主动清理全部在途轮次（关闭 DB 之前调用）。"""

        self.registry.purge_identity(None)

    # -- 校验 ---------------------------------------------------------------

    def _plugin_active(self) -> bool:
        """本插件在宿主注册表的激活状态；信息缺失时不额外阻断。"""

        if self._star_map is None or self._own_module_path is None:
            return True
        meta = self._star_map.get(self._own_module_path)
        if meta is None:
            return True
        return bool(getattr(meta, "activated", True))

    def _still_valid(self, identity: PrefIdentity, epoch_snapshot: int) -> bool:
        """提交前失效校验（fail-closed：校验异常=不可信=失效）。"""

        try:
            if not self._plugin_active():
                return False
            if not bool(self._config.get("admin_enabled", False)):
                return False
            enabled_now, epoch_now = self._store.get_user_state(identity.key)
            return bool(enabled_now) and epoch_now == epoch_snapshot
        except Exception:  # noqa: BLE001 - DB 关闭等异常按失效处理（T2b）
            return False

    def _still_valid_key(self, identity_key: str, epoch_snapshot: int) -> bool:
        try:
            if not self._plugin_active():
                return False
            if not bool(self._config.get("admin_enabled", False)):
                return False
            enabled_now, epoch_now = self._store.get_user_state(identity_key)
            return bool(enabled_now) and epoch_now == epoch_snapshot
        except Exception:  # noqa: BLE001
            return False

    # -- 注入 ----------------------------------------------------------------

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
            except Exception:  # noqa: BLE001
                provider_settings = None
        persona_scope = await resolve_persona_scope(
            self._get_persona_manager(),
            event,
            getattr(req, "conversation", None),
            provider_settings=provider_settings,
        )
        if persona_scope is None:
            return
        identity = build_identity(
            platform_id=str(event.get_platform_id() or ""),
            self_id=str(event.get_self_id() or ""),
            persona_scope=persona_scope,
            sender_id=str(event.get_sender_id() or ""),
        )

        enabled, epoch_snapshot = self._store.get_user_state(identity.key)
        if not enabled:
            return

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

        # 追加前完整失效校验（个人 off/clear、管理员开关、激活状态）。
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
        # 登记归属凭证（完整文本+对象身份），供 finalize 与推式清理使用。
        part = TextPart(text=text).mark_as_temp()
        req.extra_user_content_parts.append(part)
        record = TurnRecord(event, identity.key, epoch_snapshot)
        record.texts.append(text)
        record.parts.append(part)
        self.registry.register(record)
        event.set_extra(
            STATE_EXTRA,
            {"identity_key": identity.key, "epoch": epoch_snapshot, "req": req},
        )

    # -- 收尾与运行时钩子 -----------------------------------------------------

    def finalize(self, event: Any, req: ProviderRequest) -> None:
        """收尾失效校验（priority=-1000，Runner 组装前）。

        T5a/T5b/T6：一律按 TurnRecord 登记凭证清理——组装前按对象身份
        （record.parts）移除，绝不按前缀/文本匹配删除。校验异常
        （fail-closed）同样按对象身份；拿不到凭证时宁可不清理也不误删。
        dead 且未挂接运行时的记录在此释放（reset 前失效的块已从 req
        移除，不会进入运行时）。
        """

        record = self.registry._records.get(id(event))  # noqa: SLF001
        try:
            state = event.get_extra(STATE_EXTRA)
            if not state and record is None:
                return
            invalid = (
                record is not None
                and record.dead
            ) or not self._still_valid_key(
                state.get("identity_key", "") if state else "",
                state.get("epoch", -1) if state else -1,
            )
            if invalid:
                if record is not None:
                    req.extra_user_content_parts = [
                        p for p in req.extra_user_content_parts
                        if not any(p is own for own in record.parts)
                    ]
                    if record.run_context is None:
                        # 未挂接运行时：请求若继续走 Runner，运行时不会
                        # 含本插件块（req 已移除）；记录可释放
                        self.registry.release(event)
                    else:
                        record.dead = True
                event.set_extra(STATE_EXTRA, None)
        except Exception:  # noqa: BLE001 - 校验失败按不可信处理（T2b）
            logger.warning("preference_profile 收尾校验失败，按失效处理", exc_info=True)
            try:
                if record is not None:
                    # 异常兜底同样只按登记对象身份，绝不前缀批量删除
                    req.extra_user_content_parts = [
                        p for p in req.extra_user_content_parts
                        if not any(p is own for own in record.parts)
                    ]
                    self.registry.release(event)
                event.set_extra(STATE_EXTRA, None)
            except Exception:  # noqa: BLE001
                pass

    def invalidate_runtime_messages(self, event: Any, run_context: Any) -> int:
        """on_agent_begin 钩子（-1000）：登记运行时引用并做终检。

        先 attach（供后续推式清理定位运行时消息），再校验；失效
        （含推式清理已标记 dead 的记录）或校验异常（fail-closed）→
        按「全文+temp 标记+数量上限」置空并释放记录。返回清理数量。
        """

        cleaned = 0
        try:
            state = event.get_extra(STATE_EXTRA)
            record = self.registry._records.get(id(event))  # noqa: SLF001
            if not state and record is None:
                return 0
            self.registry.attach_runtime(event, run_context)
            if record is None:
                return 0
            invalid = record.dead or not self._still_valid_key(
                state.get("identity_key", "") if state else "",
                state.get("epoch", -1) if state else -1,
            )
            if not invalid:
                return 0
            cleaned = self.registry._blank_runtime_parts(  # noqa: SLF001
                run_context, list(record.texts)
            )
            event.set_extra(STATE_EXTRA, None)
            self.registry.release(event)
        except Exception:  # noqa: BLE001 - fail-closed：异常时按凭证清理
            logger.warning(
                "preference_profile 运行时校验异常，按失效处理", exc_info=True
            )
            try:
                record = self.registry._records.get(id(event))  # noqa: SLF001
                if record is not None:
                    if record.run_context is not None:
                        cleaned = max(
                            cleaned,
                            self.registry._blank_runtime_parts(  # noqa: SLF001
                                record.run_context, list(record.texts)
                            ),
                        )
                    self.registry.release(event)
                event.set_extra(STATE_EXTRA, None)
            except Exception:  # noqa: BLE001
                pass
        return cleaned

    def release_turn(self, event: Any) -> bool:
        """轮次终态释放（T6）：on_agent_done / on_decorating_result 调用。

        正常完成、失败与中止的轮次都到达这两个钩子之一；释放记录的
        全部强引用。不影响仍在途请求的推式失效能力（它们尚未到达
        终态钩子，记录保持活动）。
        """

        return self.registry.release(event)
