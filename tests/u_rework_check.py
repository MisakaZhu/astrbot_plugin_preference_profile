"""四轮返工回归（Codex 四轮复核 c77dd0d，T2a/T2b/T5）。

对应 lifecycle_repro.py 的核心场景，断言修复后的正确行为；真实
PluginManager.load（自动绑定 handler/补全元数据）、真实 call_event_hook
分发、真实 Runner/ContextManager/LLMSummaryCompressor、真实
turn_off_plugin；仅合成数据、本地假模型与受控等待。脱网。

  U1  prefix_collision：同标题不同尾文的用户引用与其他插件引用保留，
      不同前缀哨兵保留，偏好块按全文归属精确消失。
  U2  compression_clear/admin_off/off：内置压缩等待窗口（主模型 0 调）
      中失效 → 推式清理，恢复后首调无旧偏好；普通输入/哨兵保留。
  U3  late_begin_hook_clear：-2000 合法后续钩子等待中 clear → 不泄漏。
  U4  begin_hook_disable：activated=False → 失效。
  U5  turn_off_plugin：真实停用（terminate 关 DB）→ 推式清理 + fail-closed。
  U6  正常对照：未失效时偏好仍注入；普通文本类型不受影响。
  U7  重试幂等：同事件重复请求钩子不双注（推式清理后 state 清除）。

运行：<venv>/Scripts/python.exe tests/u_rework_check.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
ROOT = PLUGIN_ROOT.parent
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []
PLUGIN = "astrbot_plugin_preference_profile"
OUT = Path(tempfile.mkdtemp(prefix="pref_u4_"))


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.api.star import Context  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.agent.message import Message, TextPart  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook  # noqa: E402
from astrbot.core.star.star import StarMetadata, star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.star.star_manager import PluginManager  # noqa: E402
import astrbot.core.star.star_manager as sm  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
)
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.astr_agent_hooks import MAIN_AGENT_HOOKS  # noqa: E402
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor  # noqa: E402
from astrbot.core.astr_agent_run_util import run_agent  # noqa: E402

from tests.fakes import FakeProvider  # noqa: E402


def event(uid):
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE
    msg.self_id = "bot1"
    msg.sender = MessageMember(user_id=uid, nickname="synthetic")
    msg.message_str = "ordinary user question"
    msg.message_id = "case-" + uid
    msg.message = [Plain(msg.message_str)]
    ev = AstrMessageEvent(
        msg.message_str, msg,
        PlatformMetadata(name="aiocqhttp", description="synthetic", id="aiocqhttp"),
        uid,
    )
    ev.is_wake = ev.is_at_or_wake_command = True
    return ev


class Persona:
    async def resolve_selected_persona(self, **kwargs):
        return ("persona_A", {}, None, False)


class Conversations:
    async def get_curr_conversation_id(self, umo):
        return None


LOAD_COUNT = {"n": 0}
_MODULE_PREFIX = f"data.plugins.{PLUGIN}"


def _purge_loaded_state():
    """清除上一次加载的模块缓存与注册项（支持重复加载）。"""
    for m in [k for k in list(sys.modules) if str(k).startswith(_MODULE_PREFIX)]:
        sys.modules.pop(m, None)
    # namespace 包 __path__ 缓存指向旧目录，一并清除（data / data.plugins）
    sys.modules.pop("data", None)
    sys.modules.pop("data.plugins", None)
    for h in [
        h for h in list(star_handlers_registry)
        if str(h.handler_module_path).startswith(_MODULE_PREFIX)
    ]:
        star_handlers_registry.remove(h)
    for k in [k for k in list(star_map) if str(k).startswith(_MODULE_PREFIX)]:
        star_map.pop(k, None)


async def load_plugin():
    # 支持重复加载（U5 停用后 U6 需新实例）：使用递增子目录并清缓存
    _purge_loaded_state()
    dest = OUT / "data" / "plugins" / PLUGIN
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in ("main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt"):
        shutil.copyfile(PLUGIN_ROOT / name, dest / name)
    shutil.copytree(
        PLUGIN_ROOT / "pref_profile", dest / "pref_profile",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    sys.path.insert(0, str(OUT))
    cfg = {
        "data": str(OUT / "host_data"),
        "provider_settings": {"default_personality": "persona_A"},
        "agent_runner": {"runner_type": "local", "config": {"persona": {"persona_id": "persona_A"}}},
    }
    context = SimpleNamespace(
        persona_manager=Persona(), conversation_manager=Conversations(),
        _config=cfg,
        astrbot_config_mgr=SimpleNamespace(get_conf=lambda umo: cfg),
    )
    context.get_config = Context.get_config.__get__(context, type(context))
    context.get_registered_star = lambda name: next(
        (m for m in star_map.values() if m.name == name), None
    )

    async def storage_get(key=None, default=None):
        return default

    def data_dir(plugin_name=None):
        result = OUT / "profile_data"
        result.mkdir(exist_ok=True)
        return result

    with patch.object(sm.sp, "global_get", new=storage_get), patch(
        "astrbot.core.star.star_tools.StarTools.get_data_dir", new=data_dir
    ):
        manager = PluginManager(context, {})
        manager.plugin_store_path = str(OUT / "data" / "plugins")
        manager.plugin_config_path = str(OUT / "data" / "config")
        Path(manager.plugin_config_path).mkdir(parents=True, exist_ok=True)
        ok, error = await manager.load(specified_dir_name=PLUGIN)
    if ok is not True or error is not None:
        raise RuntimeError(f"load failed: {ok}, {error}")
    module = importlib.import_module(f"data.plugins.{PLUGIN}.main")
    meta = star_map[module.__name__]
    assert meta.star_cls is not None and meta.activated
    obj = meta.star_cls
    return obj, meta, module, manager


async def runtime_case(obj, meta, module, manager, index, phase, action):
    uid = str(10000 + index)
    obj._config["admin_enabled"] = True
    obj._config["relation_link_enabled"] = False
    meta.activated = True
    ident = module.build_identity(
        platform_id="aiocqhttp", self_id="bot1",
        persona_scope="persona_A", sender_id=uid,
    )
    bot = module.build_identity(
        platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
    )
    obj._store.set_user_enabled(ident.key, True)
    obj._store.upsert_entry(
        owner_kind="user", identity_key=ident.key, tag_id="joke",
        tag_display="合成偏好", status="like", intensity=4, source="self_declared",
    )
    if not obj._store.list_entries("bot", bot.key):
        obj._store.upsert_entry(
            owner_kind="bot", identity_key=bot.key, tag_id="joke",
            tag_display="合成偏好", status="like", intensity=4,
            source="admin_template",
        )
    ev = event(uid)
    entered, release = asyncio.Event(), asyncio.Event()
    gap_name = "u4_gap_" + uid
    ev.plugins_name = [meta.name, gap_name]
    req = ProviderRequest(prompt="ordinary user question")
    req.conversation = SimpleNamespace(
        persona_id="persona_A", token_usage=0, cid="synthetic-" + uid
    )
    req.extra_user_content_parts.append(TextPart(text="OTHER_PLUGIN_SENTINEL"))
    provider = FakeProvider(["synthetic reply"])
    summary_provider = None
    gap_meta = None
    header = importlib.import_module(
        module.__package__ + ".pref_profile.injection"
    ).HEADER_PREFIX
    if phase == "prefix_collision":
        req.prompt = header + "USER_QUOTED_REFERENCE"
        req.extra_user_content_parts.append(
            TextPart(text=header + "OTHER_PLUGIN_QUOTED_REFERENCE")
        )

    async def gap(event, context):
        entered.set()
        await release.wait()

    if phase != "compression":
        hook = (
            EventType.OnLLMRequestEvent
            if phase == "request_hook"
            else EventType.OnAgentBeginEvent
        )
        priority = -2000 if phase == "late_begin_hook" else 0
        star_map[gap_name] = StarMetadata(
            name=gap_name, module_path=gap_name, activated=True, reserved=False
        )
        gap_meta = StarHandlerMetadata(
            event_type=hook, handler_full_name=gap_name + ".gap",
            handler_name="gap", handler_module_path=gap_name, handler=gap,
            event_filters=[], extras_configs={"priority": priority},
        )
        star_handlers_registry.append(gap_meta)

    reset_args = {}
    if phase == "compression":
        class SummaryProvider(FakeProvider):
            async def text_chat(self, **kw):
                self.summary_input = json.dumps(
                    [
                        m.model_dump() if not isinstance(m, dict) else m
                        for m in kw.get("contexts", [])
                    ],
                    ensure_ascii=False,
                )
                entered.set()
                await release.wait()
                return await super().text_chat(**kw)

        summary_provider = SummaryProvider(["short ordinary history summary"])
        provider.provider_config["max_context_tokens"] = 2000
        req.contexts = [
            {"role": "user", "content": "ordinary old history " * 8000},
            {"role": "assistant", "content": "ordinary old answer"},
        ]
        reset_args.update(
            llm_compress_provider=summary_provider, llm_compress_keep_recent_ratio=0.15
        )

    runner = ToolLoopAgentRunner()

    async def run():
        stopped = await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
        assert not stopped
        await runner.reset(
            provider=provider, request=req,
            run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
            tool_executor=FunctionToolExecutor(),
            agent_hooks=MAIN_AGENT_HOOKS, streaming=False, **reset_args,
        )
        async for _ in run_agent(runner, max_step=2, show_tool_use=False, show_tool_call_result=False):
            pass

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        before = len(provider.call_log)
        if action == "clear":
            obj._store.clear_user(ident.key)
        elif action == "off":
            obj._store.set_user_enabled(ident.key, False)
        elif action == "admin_off":
            obj._config["admin_enabled"] = False
        elif action == "disable":
            meta.activated = False
        elif action == "turn_off_plugin":
            async def state_get(key=None, default=None):
                return default

            async def state_put(key, value):
                return None

            with patch.object(sm.sp, "global_get", new=state_get), patch.object(
                sm.sp, "global_put", new=state_put
            ):
                await manager.turn_off_plugin(meta.name)
        elif action != "none":
            raise ValueError(action)
        release.set()
        await asyncio.wait_for(task, timeout=15)
        if len(provider.call_log) != 1:
            return {"fixture_error": f"calls={len(provider.call_log)}"}
        sent = json.dumps(provider.call_log, ensure_ascii=False)
        return {
            "before": before,
            "stale": "合成偏好" in sent,
            "ordinary": "ordinary user question" in sent,
            "sentinel": "OTHER_PLUGIN_SENTINEL" in sent,
            "user_quote": ("USER_QUOTED_REFERENCE" in sent) if phase == "prefix_collision" else None,
            "other_quote": ("OTHER_PLUGIN_QUOTED_REFERENCE" in sent) if phase == "prefix_collision" else None,
            "summary_clean": (
                ("合成偏好" not in summary_provider.summary_input)
                if summary_provider else None
            ),
        }
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if gap_meta:
            star_handlers_registry.remove(gap_meta)
            star_map.pop(gap_name, None)
        meta.activated = True


async def main() -> int:
    try:
        obj, meta, module, manager = await load_plugin()

        # U1 前缀碰撞：归属精确（该场景 prompt 为用户引用，非 ordinary）
        r = await runtime_case(obj, meta, module, manager, 1, "prefix_collision", "clear")
        check(
            "U1 同标题不同尾文的用户引用/其他插件引用保留；偏好块精确消失",
            r.get("before") == 0 and not r["stale"]
            and r["user_quote"] and r["other_quote"]
            and r["sentinel"],
            f"{r}",
        )
        # U2 压缩窗口三动作
        for idx, action in ((5, "clear"), (6, "admin_off"), (7, "off")):
            r = await runtime_case(obj, meta, module, manager, idx, "compression", action)
            check(
                f"U2 压缩等待窗口 {action}：首调无旧偏好、摘要不含偏好、普通内容保留",
                r.get("before") == 0 and not r["stale"] and r["summary_clean"]
                and r["ordinary"] and r["sentinel"],
                f"{r}",
            )
        # U3 -2000 后续钩子窗口
        r = await runtime_case(obj, meta, module, manager, 8, "late_begin_hook", "clear")
        check(
            "U3 -2000 合法后续钩子等待中 clear → 不泄漏",
            r.get("before") == 0 and not r["stale"] and r["ordinary"] and r["sentinel"],
            f"{r}",
        )
        # U4 activated=False
        r = await runtime_case(obj, meta, module, manager, 9, "begin_hook", "disable")
        check(
            "U4 activated=False → 失效不泄漏",
            r.get("before") == 0 and not r["stale"] and r["ordinary"] and r["sentinel"],
            f"{r}",
        )
        # U5 真实停用
        r = await runtime_case(obj, meta, module, manager, 90, "begin_hook", "turn_off_plugin")
        check(
            "U5 真实 turn_off_plugin（terminate 关 DB）→ 推式清理 + fail-closed",
            r.get("before") == 0 and not r["stale"] and r["ordinary"] and r["sentinel"],
            f"{r}",
        )
        # U6 正常对照（U5 已真实停用插件：重新加载新实例）
        obj, meta, module, manager = await load_plugin()
        r = await runtime_case(obj, meta, module, manager, 10, "begin_hook", "none")
        check(
            "U6 正常对照：未失效偏好仍注入",
            r.get("before") == 0 and r["stale"] and r["ordinary"] and r["sentinel"],
            f"{r}",
        )
        # U7 普通文本类型不受影响（真实加载后）
        part = Message.model_validate(
            {"role": "user", "content": [{"type": "text", "text": "ORDINARY"}]}
        ).content[0]
        check("U7 真实加载后普通文本仍为 TextPart", type(part) is TextPart)
    except Exception:
        import traceback

        traceback.print_exc()
        check("U 系列未预期异常", False, "")
    finally:
        shutil.rmtree(OUT, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
