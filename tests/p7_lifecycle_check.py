"""MIS-152 补证据：真实宿主插件加载/注册、完整命令与钩子分发、
停用与取消组合（二轮复核要求）。

使用真实 PluginManager.load(specified_dir_name=...) 从临时插件目录
完整加载插件（真实 metadata/config/实例化/initialize/注册），再用
真实 CommandFilter + call_handler 分发命令、真实 call_event_hook 分发
钩子；停用=真实 star_map.activated 过滤；取消=真实 event.stop_event()
中止钩子链。脱网、合成数据、受控替身。

  L1  真实 PluginManager.load 完整加载：star_map 注册、10 个命令
      handler + 2 个 LLM 钩子（20/-1000）注册、插件实例与 config。
  L2  完整命令分发（私聊 /xp show）：真实 CommandFilter 匹配参数 →
      call_handler → 正常回执（走加载后的真实绑定方法）。
  L3  停用（真实 registry 过滤）：activated=False 后
      get_handlers_by_event_type 不再返回该插件任何 handler；
      重新激活后恢复。
  L4  取消（真实事件传播停止）：priority=10 钩子调用 event.stop_event()
      → call_event_hook 返回 True、后续 0 优先级钩子不执行。注意：只
      证明钩子链传播停止，不等同于对在途 Agent 协程的 asyncio 取消。
  L5  卸载语义：terminate 后 store 关闭（新轮次不再注入）。注意：仅
      证明新轮次零注入，不等于在途请求或全局状态的恢复验证。

运行：<venv>/Scripts/python.exe tests/p7_lifecycle_check.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
ROOT = PLUGIN_ROOT.parent
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook, call_handler  # noqa: E402
from astrbot.core.star.star import StarMetadata, star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.star.filter.command import CommandFilter  # noqa: E402
from astrbot.core.star.star_manager import PluginManager  # noqa: E402
import astrbot.core.star.star_manager as star_manager_mod  # noqa: E402
from astrbot.core.persona_mgr import PersonaManager  # noqa: E402
import astrbot.core.persona_mgr as persona_mod  # noqa: E402

PLUGIN_DIR_NAME = "astrbot_plugin_preference_profile"
LOAD_MODULE_PREFIX = f"data.plugins.{PLUGIN_DIR_NAME}"

TMP = Path(tempfile.mkdtemp(prefix="pref_p7_"))


def real_event(group="", text="合成测试内容", mid="p7"):
    msg = AstrBotMessage()
    msg.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
    msg.self_id = "bot1"
    msg.sender = MessageMember(user_id="10001", nickname="synthetic")
    msg.group_id = group
    msg.message_str = text
    msg.message_id = mid
    msg.message = [Plain(text)]
    ev = AstrMessageEvent(
        text, msg,
        PlatformMetadata(name="aiocqhttp", description="synthetic", id="aiocqhttp"),
        group or "10001",
    )
    ev.is_wake = True
    ev.is_at_or_wake_command = True
    return ev


class Conversations:
    async def get_curr_conversation_id(self, umo):
        return None  # 无选中会话 → 走宿主默认链


class ControlledPersona:
    async def resolve_selected_persona(self, **kw):
        return ("persona_A", {}, None, False)


def host_cfg():
    return {
        "provider_settings": {"default_personality": "persona_A"},
        "agent_runner": {"runner_type": "local", "config": {"persona": {"persona_id": "persona_A"}}},
        "data": str(TMP / "host_data"),
    }


async def main() -> int:
    plugin_root_module = None
    loaded_meta = None
    try:
        # 布置临时插件目录（真实加载源）：data/plugins/<插件名>
        plugins_dir = TMP / "data" / "plugins" / PLUGIN_DIR_NAME
        plugins_dir.mkdir(parents=True)
        for f in (
            "main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt",
        ):
            shutil.copyfile(PLUGIN_ROOT / f, plugins_dir / f)
        shutil.copytree(
            PLUGIN_ROOT / "pref_profile", plugins_dir / "pref_profile",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        sys.path.insert(0, str(TMP))  # 使 data.plugins.<name>.main 可导入

        # 受控 context（真实 Context.get_config 语义绑定）
        context = SimpleNamespace(
            persona_manager=ControlledPersona(),
            conversation_manager=Conversations(),
            _config=host_cfg(),
            astrbot_config_mgr=SimpleNamespace(get_conf=lambda umo: host_cfg()),
        )
        from astrbot.api.star import Context

        context.get_config = Context.get_config.__get__(context, type(context))

        async def _global_get(key=None, default=None):
            return default

        # 数据目录隔离：真实加载路径的 StarTools.get_data_dir() 基于
        # cwd 相对的宿主 data 根（生产语义）。测试 patch 到临时目录，
        # 避免把合成档案写进仓库 data/（方法级 patch 在
        # StarTools.initialize 之后仍然生效）。
        _iso_data = TMP / "isolated_data" / PLUGIN_DIR_NAME

        def _isolated_get_data_dir(plugin_name=None):
            _iso_data.mkdir(parents=True, exist_ok=True)
            return _iso_data

        with patch.object(star_manager_mod.sp, "global_get", new=_global_get),                 patch(
                    "astrbot.api.star.StarTools.get_data_dir",
                    new=_isolated_get_data_dir,
                ), patch(
                    "astrbot.core.star.star_tools.StarTools.get_data_dir",
                    new=_isolated_get_data_dir,
                ):
            mgr = PluginManager(context, {})
            mgr.plugin_store_path = str(TMP / "data" / "plugins")
            mgr.plugin_config_path = str(TMP / "data" / "config")
            Path(mgr.plugin_config_path).mkdir(parents=True, exist_ok=True)
            success, err = await mgr.load(specified_dir_name=PLUGIN_DIR_NAME)

        check(
            "L1a 真实 PluginManager.load 成功加载插件",
            success is True and err is None,
            f"success={success}, err={err}",
        )
        plugin_root_module = f"{LOAD_MODULE_PREFIX}.main"
        loaded_meta = star_map.get(plugin_root_module)
        check(
            "L1b star_map 注册（data.plugins.<name>.main）",
            loaded_meta is not None and loaded_meta.activated,
        )
        handlers = [
            h for h in star_handlers_registry
            if h.handler_module_path == plugin_root_module
        ]
        cmd_names = {h.handler_name for h in handlers}
        check(
            "L1c 10 命令 + 2 个 LLM 钩子 + 1 个 AgentBegin 钩子注册",
            len(handlers) == 13
            and {"xp_show", "xp_set", "xp_on", "xp_off", "xp_admin", "xp_clear"} <= cmd_names,
            f"n={len(handlers)}, names={sorted(cmd_names)}",
        )
        llm_hooks = [
            h for h in handlers if h.event_type == EventType.OnLLMRequestEvent
        ]
        prios = sorted(h.extras_configs.get("priority", 0) for h in llm_hooks)
        check("L1d LLM 钩子优先级 20/-1000", prios == [-1000, 20], f"prios={prios}")
        begin_hooks = [
            h for h in handlers if h.event_type == EventType.OnAgentBeginEvent
        ]
        check(
            "L1d2 AgentBegin 失效钩子 priority=-1000（链末尾）",
            len(begin_hooks) == 1
            and begin_hooks[0].extras_configs.get("priority", 0) == -1000,
            f"n={len(begin_hooks)}",
        )
        check(
            "L1e 插件实例已创建并加载 schema 配置",
            loaded_meta.star_cls is not None
            and isinstance(loaded_meta.config, dict)
            and loaded_meta.config.get("admin_enabled") is False,
            f"cfg_admin={getattr(loaded_meta.config, 'get', lambda k, d=None: d)('admin_enabled', 'NA')}",
        )

        # 种子数据（直接用加载实例的 store）
        obj = loaded_meta.star_cls
        obj._config.update({"admin_enabled": True})
        ident_store = obj._store
        from astrbot_plugin_preference_profile.pref_profile.identity import build_identity

        ident = build_identity(
            platform_id="aiocqhttp", self_id="bot1",
            persona_scope="persona_A", sender_id="10001",
        )
        bot = build_identity(
            platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
        )
        ident_store.set_user_enabled(ident.key, True)
        for kind, identity, source in (
            ("user", ident, "self_declared"),
            ("bot", bot, "admin_template"),
        ):
            ident_store.upsert_entry(
                owner_kind=kind, identity_key=identity.key, tag_id="joke",
                tag_display="合成偏好", status="like", intensity=4, source=source,
            )

        # L2 完整命令分发（真实 CommandFilter 参数匹配 + call_handler）
        ev = real_event(text="xp show", mid="p7-show")
        md = next(
            h for h in handlers
            if h.handler_name == "xp_show" and isinstance(
                next((f for f in h.event_filters if isinstance(f, CommandFilter)), None),
                CommandFilter,
            )
        )
        filt = next(f for f in md.event_filters if isinstance(f, CommandFilter))
        matched = filt.filter(ev, {})
        check("L2a 真实 CommandFilter 匹配 /xp show", bool(matched))
        async for _ in call_handler(ev, obj.xp_show, **(ev.get_extra("parsed_params") or {})):
            pass
        result = ev.get_result()
        text_out = "".join(
            getattr(x, "text", "") for x in (result.chain if result else [])
        )
        check(
            "L2b 完整分发回执包含私人条目（私聊）",
            "合成偏好" in text_out,
            f"out={text_out[:80]!r}",
        )

        # L3 停用（真实 registry 过滤）
        loaded_meta.activated = False
        after = star_handlers_registry.get_handlers_by_event_type(
            EventType.OnLLMRequestEvent,
            only_activated=True,
            plugins_name=None,
        )
        check(
            "L3a 停用后 registry 不再返回该插件 handler",
            all(h.handler_module_path != plugin_root_module for h in after),
        )
        loaded_meta.activated = True
        after2 = star_handlers_registry.get_handlers_by_event_type(
            EventType.OnLLMRequestEvent, only_activated=True, plugins_name=None
        )
        check(
            "L3b 重新激活后恢复",
            any(h.handler_module_path == plugin_root_module for h in after2),
        )

        # L4 取消（真实钩子链中止语义）
        cancel_marker = {"later_ran": False}

        async def later_hook(event, req):
            cancel_marker["later_ran"] = True

        path = "p7_cancel_probe"
        star_map[path] = StarMetadata(
            name=path, module_path=path, activated=True, reserved=False
        )
        meta_later = StarHandlerMetadata(
            event_type=EventType.OnLLMRequestEvent,
            handler_full_name=f"{path}_later",
            handler_name="later",
            handler_module_path=path,
            handler=later_hook,
            event_filters=[],
            extras_configs={"priority": 0},
        )

        async def canceller(event, req):
            event.stop_event()

        meta_cancel = StarHandlerMetadata(
            event_type=EventType.OnLLMRequestEvent,
            handler_full_name=f"{path}_cancel",
            handler_name="cancel",
            handler_module_path=path,
            handler=canceller,
            event_filters=[],
            extras_configs={"priority": 10},
        )
        star_handlers_registry.append(meta_later)
        star_handlers_registry.append(meta_cancel)
        try:
            ev4 = real_event(mid="p7-cancel")
            req4 = ProviderRequest(prompt="取消场景")
            req4.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0, cid="c")
            stopped = await call_event_hook(ev4, EventType.OnLLMRequestEvent, req4)
            check(
                "L4 事件传播停止（后续钩子未执行；非 Agent 异步取消）",
                stopped is True and cancel_marker["later_ran"] is False,
                f"stopped={stopped}, later_ran={cancel_marker['later_ran']}",
            )
        finally:
            star_handlers_registry.remove(meta_later)
            star_handlers_registry.remove(meta_cancel)
            star_map.pop(path, None)

        # L5 terminate 后新轮次不注入
        await obj.terminate()
        ev5 = real_event(mid="p7-term")
        req5 = ProviderRequest(prompt="卸载后")
        req5.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0, cid="c")
        await obj.on_llm_request(ev5, req5)  # 直接调用仍应受 store 关闭影响→不注入
        check(
                "L5 terminate 后新轮次零注入（非在途/全局恢复断言）",
            len(req5.extra_user_content_parts) == 0,
            f"parts={len(req5.extra_user_content_parts)}",
        )
    except Exception:
        traceback.print_exc()
        check("P7 未预期异常", False, traceback.format_exc(limit=2))
    finally:
        # 清理加载痕迹（进程内）
        if plugin_root_module:
            for h in [
                h for h in list(star_handlers_registry)
                if h.handler_module_path == plugin_root_module
            ]:
                star_handlers_registry.remove(h)
            star_map.pop(plugin_root_module, None)
            sys.modules.pop(plugin_root_module, None)
            for m in [k for k in sys.modules if str(k).startswith(f"{LOAD_MODULE_PREFIX}")]:
                sys.modules.pop(m, None)
        shutil.rmtree(TMP, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
