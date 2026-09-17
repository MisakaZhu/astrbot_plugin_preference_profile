"""astrbot_plugin_preference_profile 宿主入口。

命令绑定 + 生命周期。on_llm_request 注入钩子在 P4 加入（见 docs/PLAN.md）；
本文件任何路径都不记录档案原文日志。
"""

from __future__ import annotations

from typing import Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

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
    resolve_persona_scope,
)
from .pref_profile.injection import PreferenceInjector
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
        self._config = _as_plain_config(config)
        self._astrbot_config = config if config is not None else None
        self._data_dir = StarTools.get_data_dir()
        self._store = PrefStore(self._data_dir / "preference_profile.db")
        self._commands = CommandService(
            self._store, self._config, self._resolve_identity
        )
        self._bridge_guard = BridgeGuard()
        self._injector = PreferenceInjector(
            self._store,
            self._config,
            lambda: self.context.persona_manager,
            self._bridge_guard,
        )

    async def initialize(self) -> None:
        await super().initialize()
        logger.info("preference_profile 已加载（默认关闭，用户需 /xp on）")

    async def terminate(self) -> None:
        # epoch 已在 store 内持久化；关闭连接即可，在途请求按 epoch 失效
        self._store.close()
        await super().terminate()

    # -- 身份解析 -----------------------------------------------------------

    async def _resolve_identity(self, event: AstrMessageEvent) -> Optional[PrefIdentity]:
        """命令/钩子共用：解析失败返回 None（调用方保守跳过）。"""

        persona_scope = await resolve_persona_scope(
            self.context.persona_manager, event, None
        )
        if persona_scope is None:
            return None
        return build_identity(
            platform_id=str(event.get_platform_id() or ""),
            self_id=str(event.get_self_id() or ""),
            persona_scope=persona_scope,
            sender_id=str(event.get_sender_id() or ""),
        )

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
        if not bool(getattr(event, "is_private_chat", False)):
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
