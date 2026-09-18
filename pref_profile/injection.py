"""请求钩子注入器（ADR-003；四~六轮返工 T2a/T2b/T5/T5a/T5b/T6/T6a/T6b）。

唯一注入通道：req.extra_user_content_parts + **宿主原生** TextPart +
mark_as_temp()。以 priority=20 先于 Context Bridge（默认 0）执行。

核心机制（累计各轮）：
- **推式失效（T2a）**：失效由动作本身触发——PrefStore 写入回调与
  ObservableConfig 配置写回调驱动 TurnRegistry 在等待窗口（含内置
  LLMSummaryCompressor 与低优先级合法后续钩子）立即清理在途内容。
- **停用/卸载（T2b）**：terminate 先 purge_all 再关库；校验异常一律
  fail-closed（不可信=置空）；star_map.activated 纳入失效判定。
- **归属凭证（T5/T5a/T5b，六轮定稿）**：组装前=TurnRecord.parts
  对象身份；组装后=**位置映射**——宿主 assemble_context 按序追加
  每个 extra part，重建后的 user 消息 content 中
  base = len(content) - len(extra_parts)，本插件块位于
  content[base + 登记索引]（两版源码确定性，实测有/无 prompt 均命中）。
  定位后仍校验全文相等 + _no_save + 数量上限三重确认；位置失配即
  fail-safe 不清理（宁漏勿误）。公共 _no_save 与全文相等都不能证明
  创建者（其他插件可同文同 temp，第六轮 T5b），位置是注入时刻记录
  的每轮唯一关联；用户原文与其他插件内容（即使同文同 temp）位于
  不同索引故保留。
- **注册表生命周期（T6/T6a/T6b）**：TurnRegistry 以
  WeakKeyDictionary(event) 为骨架，record 对 event/run_context 只持
  **弱引用**——正常完成由 on_agent_done 显式释放；装饰阶段仅在记录
  已失效或从未挂接运行时才回收（多步 Agent 的中间回复装饰不释放，
  第六轮 T6a）；请求钩子 stop_event 中止与 asyncio 任务取消等无钩子
  终态由弱引用自动回收（event 死亡即条目消失，第六轮 T6b）；
  finalize 对 dead 未挂接记录显式释放。在途轮次的推式失效不受影响。
- OnAgentBegin 返回后到首次 Provider 调用之间仍无插件钩子点（接口
  缺口，如实声明）；已真正发出的请求不可撤回。
"""

from __future__ import annotations

import logging
import secrets
import weakref
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
# 兼容常量：注入块标题前缀（HEADER 至首个「」」）。仅作外部引用/
# 测试构造碰撞文本用；生产清理不按此匹配（T5a 已移除前缀路径）。
HEADER_PREFIX = HEADER[: HEADER.index("】") + 1]

logger = logging.getLogger("pref_profile.injection")


class TurnRecord:
    """一个在途轮次的归属凭证与清理句柄（对宿主对象只持弱引用）。"""

    __slots__ = ("event_ref", "identity_key", "epoch", "texts", "parts",
                 "run_context_ref", "dead", "part_index", "extra_count",
                 "_task_callback")

    def __init__(self, event: Any, identity_key: str, epoch: int):
        self.event_ref = weakref.ref(event)
        self.identity_key = identity_key
        self.epoch = epoch
        self.texts: list[str] = []        # 注入块完整文本（位置校验用）
        self.parts: list[TextPart] = []   # req 上追加的块对象（组装前按身份移除）
        self.run_context_ref: Optional[weakref.ref] = None
        self.dead = False
        self.part_index: int = -1         # 注入时在 req.extra_user_content_parts 的绝对索引
        self.extra_count: int = -1        # 注入后 extra parts 总数（位置映射基准）
        self._task_callback = None        # 宿主执行 task 的 done 回调句柄（T6b）

    @property
    def event(self):
        return self.event_ref()

    @property
    def run_context(self):
        return self.run_context_ref() if self.run_context_ref is not None else None


class TurnRegistry:
    """在途轮次注册表：dict[id(event) -> record] + 每条弱引用回调。

    event 死亡（请求钩子 stop 中止、asyncio 取消等无钩子终态后宿主
    释放对象）→ finalize 回调按 id 移除条目，无插件侧无界保留。
    """

    def __init__(self):
        self._records: dict[int, TurnRecord] = {}
        self._by_identity: dict[str, list[weakref.ref]] = {}

    def register(self, record: TurnRecord) -> None:
        event = record.event
        if event is None:
            return
        key = id(event)
        self._records[key] = record
        bucket = self._by_identity.setdefault(record.identity_key, [])

        def _gone(_ref, _key=key, _ident=record.identity_key):
            self._records.pop(_key, None)
            b = self._by_identity.get(_ident)
            if b is not None:
                b[:] = [r for r in b if r() is not None]
                if not b:
                    self._by_identity.pop(_ident, None)

        bucket.append(weakref.ref(event, _gone))

    def _on_event_gone(self, identity_key: str) -> None:
        # 身份桶死引用的懒惰清理
        bucket = self._by_identity.get(identity_key)
        if bucket is not None:
            bucket[:] = [r for r in bucket if r() is not None]
            if not bucket:
                self._by_identity.pop(identity_key, None)

    def get(self, event: Any) -> Optional[TurnRecord]:
        return self._records.get(id(event))

    def attach_runtime(self, event: Any, run_context: Any) -> None:
        record = self.get(event)
        if record is None or record.run_context_ref is not None:
            return
        record.run_context_ref = weakref.ref(run_context)
        # T6b：注册执行本轮的宿主 asyncio task 的完成回调——取消与
        # 完成都触发，是无 AgentDone/decorating 终态（asyncio 取消）
        # 的可靠释放信号；回调经弱引用取回 event，闭包不强持任何宿主对象。
        try:
            import asyncio as _asyncio

            task = _asyncio.current_task()
            if task is not None and record._task_callback is None:
                event_ref = weakref.ref(event)

                def _on_task_done(_t):
                    ev = event_ref()
                    if ev is not None:
                        self.release(ev)

                task.add_done_callback(_on_task_done)
                record._task_callback = (task, _on_task_done)
        except Exception:  # noqa: BLE001 - 回调注册失败不影响主流程
            pass

    @staticmethod
    def _blank_runtime_parts(record: "TurnRecord") -> int:
        """按位置映射置空运行时中本插件块。

        宿主 assemble_context 按序追加每个 extra part；重建后 user 消息
        content 的 base = len(content) - extra_count（两版源码确定性，
        实测有/无 prompt 均命中），本插件块位于 content[base+part_index]。
        定位后三重校验（全文相等 + _no_save + 索引界内）；失配即
        fail-safe 跳过（宁漏勿误）。绝不触碰其他索引的块——用户原文
        与其他插件内容（即使同文同 temp）保留。
        """

        run_context = record.run_context
        part_index = record.part_index
        texts = list(record.texts)
        extra_count = record.extra_count
        if run_context is None or part_index < 0 or not texts:
            return 0
        expected = texts[0]
        try:
            for message in getattr(run_context, "messages", None) or []:
                content = getattr(message, "content", None)
                if not isinstance(content, list):
                    continue
                if extra_count <= 0 or len(content) < extra_count:
                    continue
                base = len(content) - extra_count
                idx = base + part_index
                if not (0 <= idx < len(content)):
                    continue
                part = content[idx]
                if (
                    getattr(part, "text", None) == expected
                    and bool(getattr(part, "_no_save", False))
                ):
                    part.text = ""
                    return 1
            return 0
        except Exception:  # noqa: BLE001 - 清理失败不中断宿主
            logger.warning("preference_profile 运行时块清理失败", exc_info=True)
            return 0

    def _blank_record(self, record: TurnRecord) -> None:
        """按归属凭证清理单个记录：req 对象身份移除 + 运行时位置置空。

        运行时引用未附加时保留记录（dead 标记），供后续 on_agent_begin
        按凭证再清理；仅在 run_context 已附加并完成置空后才可丢弃。
        """

        record.dead = True
        event = record.event
        parts = record.parts
        if parts and event is not None:
            try:
                state = event.get_extra(STATE_EXTRA)
                req = state.get("req") if isinstance(state, dict) else None
                if req is not None:
                    req.extra_user_content_parts = [
                        p for p in req.extra_user_content_parts
                        if not any(p is own for own in parts)
                    ]
            except Exception:  # noqa: BLE001
                pass
        rc = record.run_context
        if rc is None:
            return  # 未附加运行时引用：保留记录等待 on_agent_begin 清理
        self._blank_runtime_parts(record)

    def release(self, event: Any) -> bool:
        """释放该轮次的记录（终态：完成/取消后/装饰回收）。

        丢弃全部引用（parts/texts/run_context 弱引用/指纹）。
        返回是否存在记录。
        """

        record = self._records.pop(id(event), None)
        if record is None:
            return False
        if record._task_callback is not None:
            try:
                record._task_callback[0].remove_done_callback(
                    record._task_callback[1]
                )
            except Exception:  # noqa: BLE001
                pass
            record._task_callback = None
        self._on_event_gone(record.identity_key)
        record.parts = []
        record.texts = []
        record.part_index = -1
        record.extra_count = -1
        record.run_context_ref = None
        event.set_extra(STATE_EXTRA, None)
        return True

    def purge_identity(self, identity_key: str | None) -> None:
        """失效指定身份的在途轮次；None=全部（管理员开关/停用）。"""

        if identity_key is None:
            targets = list(self._records.values())
        else:
            self._on_event_gone(identity_key)
            targets = []
            for ref in list(self._by_identity.get(identity_key, [])):
                event = ref()
                if event is None:
                    continue
                record = self._records.get(id(event))
                if record is not None:
                    targets.append(record)
        for record in targets:
            self._blank_record(record)
            if record.run_context is not None:
                # 运行时已清理完毕；event 仍活（宿主持有）→ 安全丢弃
                if record.event is not None:
                    self.release(record.event)
                else:
                    record.run_context_ref = None
                    record.parts = []
                    record.texts = []

    def __len__(self) -> int:
        return len(self._records)

    def values(self):
        return list(self._records.values())


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
        self._store.add_invalidation_hook(self._on_store_invalidation)

    # -- 推式失效入口 -------------------------------------------------------

    def _on_store_invalidation(self, identity_key: str | None) -> None:
        self.registry.purge_identity(identity_key)

    def on_config_invalidation(self) -> None:
        self.registry.purge_identity(None)

    def purge_all(self) -> None:
        self.registry.purge_identity(None)

    # -- 校验 ---------------------------------------------------------------

    def _plugin_active(self) -> bool:
        if self._star_map is None or self._own_module_path is None:
            return True
        meta = self._star_map.get(self._own_module_path)
        if meta is None:
            return True
        return bool(getattr(meta, "activated", True))

    def _still_valid(self, identity: PrefIdentity, epoch_snapshot: int) -> bool:
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
        # 宿主原生 TextPart（T5b：位置映射归属——注入时记录 extra
        # parts 中的绝对索引与总数，运行时按 base+索引定位）。
        part = TextPart(text=text).mark_as_temp()
        req.extra_user_content_parts.append(part)
        record = TurnRecord(event, identity.key, epoch_snapshot)
        record.texts.append(text)
        record.part_index = len(req.extra_user_content_parts) - 1
        record.extra_count = len(req.extra_user_content_parts)
        record.parts.append(part)
        self.registry.register(record)
        event.set_extra(
            STATE_EXTRA,
            {"identity_key": identity.key, "epoch": epoch_snapshot, "req": req},
        )

    # -- 收尾与运行时钩子 -----------------------------------------------------

    def finalize(self, event: Any, req: ProviderRequest) -> None:
        """收尾失效校验（priority=-1000，Runner 组装前）。

        一律按 TurnRecord 登记凭证清理——组装前按对象身份移除；
        校验异常（fail-closed）同样按对象身份。dead 且未挂接运行时的
        记录在此释放（reset 前失效的块已从 req 移除）。
        """

        record = self.registry.get(event)
        try:
            state = event.get_extra(STATE_EXTRA)
            if not state and record is None:
                return
            invalid = (record is not None and record.dead) or not self._still_valid_key(
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
                        self.registry.release(event)
                    else:
                        record.dead = True
                event.set_extra(STATE_EXTRA, None)
        except Exception:  # noqa: BLE001 - 校验失败按不可信处理（T2b）
            logger.warning("preference_profile 收尾校验失败，按失效处理", exc_info=True)
            try:
                if record is not None:
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
        （含推式清理已标记 dead）或校验异常（fail-closed）→
        按指纹凭证置空并释放记录。返回清理数量。
        """

        cleaned = 0
        try:
            state = event.get_extra(STATE_EXTRA)
            record = self.registry.get(event)
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
            cleaned = self.registry._blank_runtime_parts(record)  # noqa: SLF001
            event.set_extra(STATE_EXTRA, None)
            self.registry.release(event)
        except Exception:  # noqa: BLE001 - fail-closed：异常时按凭证清理
            logger.warning(
                "preference_profile 运行时校验异常，按失效处理", exc_info=True
            )
            try:
                record = self.registry.get(event)
                if record is not None:
                    rc = record.run_context
                    if rc is not None:
                        cleaned = max(
                            cleaned,
                            self.registry._blank_runtime_parts(record),  # noqa: SLF001
                        )
                    self.registry.release(event)
                event.set_extra(STATE_EXTRA, None)
            except Exception:  # noqa: BLE001
                pass
        return cleaned

    def release_turn(self, event: Any) -> bool:
        """轮次终态释放（T6）：on_agent_done 调用（真实完成终态）。

        err 终态与异步取消等无 AgentDone 路径由弱引用自动回收；
        多步 Agent 的中间回复不触发本方法（T6a）。
        """

        return self.registry.release(event)

    def release_finished_or_dead(self, event: Any) -> bool:
        """装饰阶段回收（T6a 修订）：仅回收已失效或从未挂接运行时的记录。

        多步 Agent 的中间回复装饰时记录仍活且已挂接 → 保留（后续
        第二次模型调用仍可被失效）；正常完成已由 on_agent_done 先行
        释放；stop_event 中止/取消后的死记录在此回收。
        """

        try:
            record = self.registry.get(event)
            if record is None:
                return False
            if record.dead or record.run_context is None:
                return self.registry.release(event)
            return False
        except Exception:  # noqa: BLE001
            return False
