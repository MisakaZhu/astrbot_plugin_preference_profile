"""astrbot_plugin_preference_profile 宿主入口。

命令绑定 + 生命周期 + LLM 请求钩子。本文件任何路径都不记录档案原文。

返工加固（Codex 复核 R1/R2/R4）：
- 私聊判定统一 host_is_private_chat（真实宿主是方法）；
- BridgeGuard 接入**真实宿主注册表** star_map（此前空参构造导致恒为
  no_bridge，已加载的共享插件被漏判）；
- 命令人格解析读取当前会话 conversation.persona_id（与请求轮次同一
  选中会话），并按宿主版本签名传入 provider_settings（4.26 需要）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.star import star_map as host_star_map

from .pref_profile.bridge_guard import BridgeGuard
from .pref_profile.commands import (
    GROUP_HINT,
    CommandError,
    CommandService,
    _GroupOnly,
)
from .pref_profile.identity import (
    PrefIdentity,
    build_identity,
    host_is_private_chat,
    resolve_persona_scope,
)
from .pref_profile.injection import ObservableConfig, PreferenceInjector
from .pref_profile.relation_snapshot import RelationSnapshotReader
from .pref_profile.store import PrefStore


def _as_plain_config(config) -> dict:
    """AstrBotConfig 是 dict 子类；测试或异常场景退化为普通 dict 默认值。"""

    if isinstance(config, dict):
        return config
    return {"admin_enabled": False, "relation_link_enabled": True,
            "max_inject_items": 6, "max_inject_chars": 600}


class PreferenceProfilePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        base_config = _as_plain_config(config)
        self._astrbot_config = config if config is not None else None
        self._data_dir = StarTools.get_data_dir()
        self._store = PrefStore(self._data_dir / "preference_profile.db")
        self._bridge_guard = BridgeGuard(star_map=host_star_map)
        self._relation_reader: Optional[RelationSnapshotReader] = None
        self._injector = PreferenceInjector(
            self._store,
            {},  # 占位：ObservableConfig 在注入器创建后包装（见下）
            lambda: self.context.persona_manager,
            self._bridge_guard,
            self._load_relation_snapshot,
            provider_settings_getter=self._provider_settings,
            star_map=host_star_map,
            own_module_path=f"{__package__}.main",
        )
        # 四轮 T2a：管理开关等配置写入同步触发推式清理（等待窗口中的
        # 在途轮次立即失效）。写操作透传底层宿主配置对象，保持
        # save_config 语义；不污染宿主类型注册。
        self._config = ObservableConfig(
            base_config, on_write=self._injector.on_config_invalidation
        )
        self._injector._config = self._config  # noqa: SLF001
        self._commands = CommandService(
            self._store, self._config, self._resolve_identity
        )

    async def initialize(self) -> None:
        await super().initialize()
        # 宿主插件装载/卸载会改变 star_map；每次初始化重新探测协作协议
        self._bridge_guard.refresh()
        logger.info("preference_profile 已加载（默认关闭，用户需 /xp on）")

    async def terminate(self) -> None:
        # 四轮 T2b：先主动清理全部在途轮次（运行时消息/req 块），
        # 再关闭存储——后续清理钩子读已关闭 DB 异常时按 fail-closed
        # 处理，双保险确保停用/卸载不复活旧内容。
        self._injector.purge_all()
        self._store.close()
        await super().terminate()

    # -- 身份解析 -----------------------------------------------------------

    async def _current_conversation_persona_id(
        self, event: AstrMessageEvent
    ) -> Optional[str] | LookupError:
        """当前 UMO 选中会话的 persona_id（与请求轮次同一会话，R4）。

        与宿主 _get_session_conv 同源：get_curr_conversation_id →
        get_conversation。
        返回 LookupError 表示"存在选中会话但读取失败"——调用方必须
        拒绝私人档案操作，不得当作"成功读取且未指定人格"落默认链。
        其他非存在性异常（如无会话记录）走 None（宿主默认解析链）。
        """

        manager = getattr(self.context, "conversation_manager", None)
        if manager is None:
            return None
        try:
            cid = await manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
            if not cid:
                return None
            conversation = await manager.get_conversation(
                event.unified_msg_origin, cid
            )
            if conversation is None:
                return None
            return getattr(conversation, "persona_id", None)
        except Exception as exc:  # noqa: BLE001 - 读取失败≠未指定人格（R4）
            return LookupError(str(exc))

    def _provider_settings(self, event: AstrMessageEvent = None) -> Optional[dict]:
        """按当前 UMO 取作用域配置中的 provider_settings（R4）。

        真实宿主 Context.get_config(umo) 优先返回该会话作用域配置；
        4.26 的默认人格解析需要 provider_settings.default_personality。
        读取异常返回 None（交由解析优先级链，不写死人格）。
        """

        try:
            getter = self.context.get_config
            umo = getattr(event, "unified_msg_origin", None) if event else None
            try:
                cfg = getter(umo=umo) if umo is not None else getter()
            except TypeError:
                # 替身/旧签名不支持 umo 参数：退化为全局配置
                cfg = getter()
            if cfg is None:
                return None
            ps = cfg.get("provider_settings") if isinstance(cfg, dict) else None
            return ps if isinstance(ps, dict) else None
        except Exception:  # noqa: BLE001
            return None

    async def _resolve_identity(self, event: AstrMessageEvent) -> Optional[PrefIdentity]:
        """命令/钩子共用：解析失败返回 None（调用方保守跳过）。

        conversation 取当前选中会话（R4：命令与请求同一生效人格）。
        会话读取失败（LookupError）→ 返回 None 拒绝操作。
        """

        conversation_persona = await self._current_conversation_persona_id(event)
        if isinstance(conversation_persona, LookupError):
            # 读取失败 ≠ 未指定人格：拒绝，绝不落可能串档的默认链
            logger.warning(
                "preference_profile 当前会话读取失败，拒绝档案操作",
                exc_info=False,
            )
            return None
        conversation = SimpleNamespace(persona_id=conversation_persona)
        persona_scope = await resolve_persona_scope(
            self.context.persona_manager,
            event,
            conversation,
            provider_settings=self._provider_settings(event),
        )
        if persona_scope is None:
            return None
        return build_identity(
            platform_id=str(event.get_platform_id() or ""),
            self_id=str(event.get_self_id() or ""),
            persona_scope=persona_scope,
            sender_id=str(event.get_sender_id() or ""),
        )

    def _load_relation_snapshot(self, identity, event):
        """Relation Arc 只读快照（缺库/异常→保守不可用，ADR-005）。"""

        if self._relation_reader is None:
            try:
                host_config = self.context.get_config() or {}
                data_root = Path(
                    host_config.get(
                        "plugin.data_dir", host_config.get("data", "./data")
                    )
                )
                self._relation_reader = RelationSnapshotReader(data_root)
            except Exception:  # noqa: BLE001 - 配置异常按关系不可用处理
                from .pref_profile.policy import RelationSnapshot

                async def _none(identity, event):
                    return RelationSnapshot.unavailable()

                return _none(identity, event)
        return self._relation_reader.load(identity, event.unified_msg_origin)

    def _save_host_config(self) -> None:
        if self._astrbot_config is not None:
            try:
                self._astrbot_config.save_config()
            except Exception:  # noqa: BLE001 - 保存失败不阻塞命令回执
                logger.warning("preference_profile 配置保存失败", exc_info=True)

    async def _reply(self, event: AstrMessageEvent, text: str) -> None:
        event.set_result(MessageEventResult().message(text))

    async def _dispatch(self, event: AstrMessageEvent, coro) -> None:
        """统一命令门禁：群聊引导 / 命令错误回执。"""

        try:
            text = await coro
        except _GroupOnly:
            text = GROUP_HINT
        except CommandError as exc:
            text = exc.user_message
        except Exception:  # noqa: BLE001 - 插件异常不得中断宿主
            logger.error("preference_profile 命令处理失败", exc_info=True)
            text = "命令处理出现内部错误，请稍后再试。"
        await self._reply(event, text)

    # -- LLM 请求钩子（priority=20：先于 Context Bridge 的捕获钩子） ---------

    @filter.on_llm_request(priority=20)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        await self._injector.handle(event, req)

    @filter.on_llm_request(priority=-1000)
    async def finalize_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """钩子链末尾（Runner 组装前）的最终失效校验（R5/T2）。

        对本插件已追加但宿主尚未组装的临时块校验 admin/enabled/epoch，
        失效则按前缀移除（仅本插件块，其他插件内容不动）。
        """

        self._injector.finalize(event, req)

    @filter.on_agent_begin(priority=-1000)
    async def on_agent_begin(self, event: AstrMessageEvent, run_context):
        """真实 Agent 钩子（T2）：Runner reset 完成后、首次 Provider 调用前。

        priority=-1000：在本轮其他 OnAgentBegin 钩子（含受控等待）之后、
        链末尾执行——等待期间发生的失效在恢复后仍会被本校验捕获。
        对 run_context.messages 中已固化的本插件块做最终失效校验，
        失效时按「全文+temp 标记+数量上限」置空（普通输入与其他插件
        块不受影响）。此后到首次 Provider 调用之间无插件可介入的宿主
        钩子点（接口缺口，如实声明）；已真正发出的请求不可撤回。
        """

        self._injector.invalidate_runtime_messages(event, run_context)

    @filter.on_agent_done()
    async def on_agent_done(self, event: AstrMessageEvent, run_context, llm_response=None):
        """轮次终态释放（T6）：真实 Agent 完成终态（DONE）触发。

        释放该轮记录；不影响仍在途请求的推式失效能力。err 终态与
        asyncio 取消等无 AgentDone 路径由注册表弱引用自动回收（T6b）。
        """

        self._injector.release_turn(event)

    @filter.on_decorating_result()
    async def on_decorating_result_release(self, event: AstrMessageEvent):
        """装饰阶段回收（T6a 修订）：仅回收已失效或从未挂接运行时的记录。

        多步 Agent 的中间回复（工具调用伴随文字）也会到达装饰阶段且
        runner 尚未 done——此时记录必须保留，否则失效窗口丢失；正常
        完成已由 on_agent_done 先行释放，此处不重复处理活记录。
        stop_event 中止与取消后的死记录在此回收。
        """

        self._injector.release_finished_or_dead(event)

    # -- 命令组（/xp 主名，/偏好 中文别名） ---------------------------------

    @filter.command_group("xp", alias={"偏好"})
    def xp_group(self):
        pass

    @xp_group.command("help", alias={"帮助"})
    async def xp_help(self, event: AstrMessageEvent):
        await self._dispatch(event, self._commands.handle_help(event))

    @xp_group.command("status", alias={"状态"})
    async def xp_status(self, event: AstrMessageEvent):
        await self._dispatch(event, self._commands.handle_status(event))

    @xp_group.command("on", alias={"开启"})
    async def xp_on(self, event: AstrMessageEvent):
        await self._dispatch(event, self._commands.handle_on(event))

    @xp_group.command("off", alias={"关闭"})
    async def xp_off(self, event: AstrMessageEvent):
        await self._dispatch(event, self._commands.handle_off(event))

    @xp_group.command("show", alias={"查看"})
    async def xp_show(self, event: AstrMessageEvent, page: int = 1):
        await self._dispatch(event, self._commands.handle_show(event, page))

    @xp_group.command("set", alias={"设置"})
    async def xp_set(
        self,
        event: AstrMessageEvent,
        tag: str,
        status: str = "",
        direction: str = "",
        intensity: str = "",
    ):
        await self._dispatch(
            event,
            self._commands.handle_set(event, tag, status, direction, intensity),
        )

    @xp_group.command("remove", alias={"删除"})
    async def xp_remove(self, event: AstrMessageEvent, tag: str):
        await self._dispatch(event, self._commands.handle_remove(event, tag))

    @xp_group.command("clear", alias={"清空"})
    async def xp_clear(self, event: AstrMessageEvent, code: str = ""):
        await self._dispatch(event, self._commands.handle_clear(event, code))

    # -- 管理员（私聊 + ADMIN 权限） ---------------------------------------

    @xp_group.command("admin")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def xp_admin(
        self,
        event: AstrMessageEvent,
        action: str = "",
        sub: str = "",
        tag: str = "",
        status: str = "",
        direction: str = "",
        intensity: str = "",
    ):
        # 管理命令统一私聊（避免在群聊暴露开关与模板操作轨迹）
        if not host_is_private_chat(event):
            await self._reply(event, GROUP_HINT)
            return
        action_l = (action or "").strip().lower()
        if action_l in ("switch", "总开关"):
            await self._dispatch(event, self._commands.handle_admin_switch(event, sub))
            self._save_host_config()
        elif action_l == "stats":
            await self._dispatch(event, self._commands.handle_admin_stats(event))
        elif action_l in ("template", "模板"):
            sub_l = (sub or "").strip().lower()
            if sub_l in ("show", "查看"):
                await self._dispatch(event, self._commands.handle_admin_template_show(event))
            elif sub_l in ("set", "设置"):
                await self._dispatch(
                    event,
                    self._commands.handle_admin_template_set(
                        event, tag, status, direction, intensity
                    ),
                )
            elif sub_l in ("remove", "删除"):
                await self._dispatch(
                    event, self._commands.handle_admin_template_remove(event, tag)
                )
            elif sub_l in ("clear", "清空"):
                await self._dispatch(event, self._commands.handle_admin_template_clear(event))
            else:
                await self._reply(event, "用法：/xp admin template show|set|remove|clear")
        else:
            await self._reply(
                event,
                "用法：/xp admin switch on|off｜/xp admin stats｜"
                "/xp admin template show|set|remove|clear",
            )
