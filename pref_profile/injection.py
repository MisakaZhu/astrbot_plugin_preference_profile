"""请求钩子注入器（ADR-003；四~六轮返工 T2a/T2b/T5/T5a/T5b/T6/T6a/T6b）。

唯一注入通道：req.extra_user_content_parts + **宿主原生** TextPart +
mark_as_temp()。以 priority=20 先于 Context Bridge（默认 0）执行。

核心机制（累计各轮）：
- **推式失效（T2a）**：失效由动作本身触发——PrefStore 写入回调与
  ObservableConfig 配置写回调驱动 TurnRegistry 在等待窗口（含内置
  LLMSummaryCompressor 与低优先级合法后续钩子）立即清理在途内容。
- **停用/卸载（T2b）**：terminate 先 purge_all 再关库；校验异常一律
  fail-closed（不可信=置空）；star_map.activated 纳入失效判定。
- **归属凭证（T5/T5a/T5b，九轮裁定后表述）**：组装前=TurnRecord.parts
  对象身份；组装后=**每轮唯一令牌子串匹配**——注入时在文本尾部嵌入
  每轮随机令牌（「〔偏好标识<random hex>〕」），运行时只
  置空含本轮令牌且带临时标记的块；令牌跨宿主重建链
  （model_dump_for_context → Message.model_validate）与多步 Agent
  组装原样保留。全文相等/公共 _no_save/数量/位置都不能证明创建者
  （其他插件可同文同 temp，位置映射在后续钩子追加/更早独立消息/
  运行时追加下均失配，第六/七轮已证）。**令牌不是所有权证明**：它
  只在「finalize 轮换之前」区分同文副本；finalize 之后任何合法钩子
  复制全文都会带走当前令牌（token_repro 七场景，第九轮受阻裁定），
  现有原生 Part 转换路径下缺少已验证的来源关联。失效置空后令牌
  一并消失。
- **注册表生命周期（T6/T6a/T6b）**：TurnRegistry 以 dict[id(event)]
  为骨架并为每次注册挂 event 弱引用回调，record 对 event/run_context
  只持**弱引用**——正常完成由 on_agent_done 显式释放；装饰阶段仅在
  记录已失效或从未挂接运行时才回收（多步 Agent 的中间回复装饰不释放，
  第六轮 T6a）；请求钩子 stop_event 中止与 asyncio 任务取消等无钩子
  终态由弱引用回调自动移除条目，挂接运行时后另有宿主 task done 回调
  兜底释放（第六轮 T6b、七轮保留）；finalize 对 dead 未挂接记录
  显式释放。在途轮次的推式失效不受影响。
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

# 每轮唯一令牌：全角括号包裹随机 hex，作为内容级归属凭证嵌入注入
# 文本尾部。模型可见（约 14 字符、无语义冲突）；经宿主重建链与多步
# Agent 组装原样保留（实测）；失效置空后令牌一并消失。
def make_token() -> str:
    return "〔偏好标识" + secrets.token_hex(4) + "〕"


class TurnRecord:
    """一个在途轮次的归属凭证与清理句柄（对宿主对象只持弱引用）。"""

    __slots__ = ("event_ref", "identity_key", "epoch", "tokens", "parts",
                 "run_context_ref", "dead", "_task_callback")

    def __init__(self, event: Any, identity_key: str, epoch: int):
        self.event_ref = weakref.ref(event)
        self.identity_key = identity_key
        self.epoch = epoch
        self.tokens: list[str] = []       # 每轮唯一令牌（归属凭证）
        self.parts: list[TextPart] = []   # req 上追加的块对象（组装前按身份移除）
        self.run_context_ref: Optional[weakref.ref] = None
        self.dead = False
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
        """按每轮唯一令牌置空运行时中本插件块。

        归属判据=「含本轮令牌 + _no_save 临时标记」；令牌为注入时随
        机生成并嵌入文本尾部的每轮唯一标记，跨宿主重建链与多步 Agent
        保留。其他插件同文/同 temp 但不含本轮令牌的块保留；**注意**：
        finalize 之后被复制的同文 temp 副本会携带当前令牌、与本插件
        块不可区分（第九轮受阻裁定），此路径不能区分它们。
        """

        run_context = record.run_context
        tokens = list(record.tokens)
        if run_context is None or not tokens:
            return 0
        cleaned = 0
        try:
            for message in getattr(run_context, "messages", None) or []:
                content = getattr(message, "content", None)
                if not isinstance(content, list):
                    continue
                for part in content:
                    txt = getattr(part, "text", None)
                    if (
                        isinstance(txt, str)
                        and txt
                        and any(tok in txt for tok in tokens)
                        and bool(getattr(part, "_no_save", False))
                    ):
                        part.text = ""
                        cleaned += 1
        except Exception:  # noqa: BLE001 - 清理失败不中断宿主
            logger.warning("preference_profile 运行时块清理失败", exc_info=True)
        return cleaned

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

        丢弃全部引用（parts/tokens/run_context 弱引用）。
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
        record.tokens = []
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
                    record.tokens = []

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
        token = make_token()
        # T7：max_inject_chars 是单轮注入总字符上限，最终表示中的全部
        # 模型可见字符（含归属标识）都计入预算；预算不足按既有规则丢
        # 尾部条目，连头部都放不下则不注入（prompt_builder 语义）。
        text = render_decision(decision, max_chars=max_chars - len(token))
        event.set_extra(_DONE_EXTRA, True)
        if text is None:
            return
        # 宿主原生 TextPart（T1：不定义子类）。T5b：每轮唯一令牌嵌入
        # 文本尾部；finalize 时轮换（见下），此后失效清理只认新令牌，
        # 任何更早产生的同文副本（旧令牌）不受影响。
        part = TextPart(text=text + token).mark_as_temp()
        req.extra_user_content_parts.append(part)
        record = TurnRecord(event, identity.key, epoch_snapshot)
        record.tokens.append(token)
        record.parts.append(part)
        self.registry.register(record)
        event.set_extra(
            STATE_EXTRA,
            {"identity_key": identity.key, "epoch": epoch_snapshot, "req": req},
        )

    # -- 收尾与运行时钩子 -----------------------------------------------------

    def finalize(self, event: Any, req: ProviderRequest) -> None:
        """收尾失效校验与令牌轮换（priority=-1000，Runner 组装前）。

        七轮 T5b：注入后的请求钩子（priority 介于 20 与 -1000 之间）
        可能复制含旧令牌的本插件全文。此处（-1000 晚于常见合法注入
        钩子、对象身份仍有效；注意 -1000 只是相对排序，不保证全链
        最后，晚于本钩子的复制见受阻记录）把本插件块中的令牌**轮换**为随机新值并同步 record——
        此后任何失效清理只按新令牌匹配；更早产生的同文副本（持旧令
        牌）不会被误删，本插件真实块在 reset 后仍可被精确定位置空。
        失效（fail-closed 同前）时按对象身份移除本插件块。
        """

        record = self.registry.get(event)
        try:
            state = event.get_extra(STATE_EXTRA)
            if not state and record is None:
                return
            if record is not None and record.tokens and record.parts:
                # 令牌轮换：按对象身份定位自己块，仅替换其中我方旧令牌
                own_part = record.parts[0]
                old_token = record.tokens[0]
                new_token = make_token()
                if old_token in own_part.text:
                    own_part.text = own_part.text.replace(old_token, new_token, 1)
                    record.tokens[0] = new_token
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
        按令牌凭证置空并释放记录。返回清理数量。
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
