"""六轮返工回归（Codex 六轮复核 a2378c6，T5b/T6a/T6b）。

真实 PluginManager.load 夹具 + 真实 ResultDecorateStage / FunctionTool /
FunctionToolExecutor / 原生历史压缩 / asyncio 取消；合成数据、本地假
模型与无 I/O 假工具。脱网。

  W1  T5b 同文 temp（其他插件原生 mark_as_temp，与本插件渲染全文相同，
      位于本插件之前）：clear 后其他插件块保留、本插件块失效（early
      AgentBegin-0 与 late -2000 两时机），以 req 源对象序 + 运行时
      对象身份 + Provider 边界判定。
  W2  T6a 多步 Agent：真实工具调用 + 中间说明文字经真实装饰阶段后
      clear/admin_off，第二次 Provider 调用无旧偏好；runner 未 done
      时记录不释放；正常对照两调均含偏好。
  W3  T6b stop_before_agent：注入后请求钩子链中 stop_event 真实中止
      → 无 Agent/回复 → 记录被回收（gc 后 registry 0、event 可回收）。
  W4  T6b task_cancelled：原生压缩等待中取消执行轮次的 asyncio.Task
      （CancelledError 且 task.cancelled）→ 记录被释放。
  W5  正常对照：不失效时本插件 temp 块与用户输入均到达 Provider。

运行：<venv>/Scripts/python.exe tests/w_rework_check.py
"""

from __future__ import annotations

import asyncio
import copy
import gc
import importlib
import json
import shutil
import sys
import tempfile
import weakref
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
OUT = Path(tempfile.mkdtemp(prefix="pref_w6_"))


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
from astrbot.core.agent.message import TextPart  # noqa: E402
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
from astrbot.core.provider.entities import LLMResponse  # noqa: E402
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402
from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage  # noqa: E402
from astrbot.core.pipeline.context import PipelineContext  # noqa: E402
from astrbot.core.config.default import DEFAULT_CONFIG  # noqa: E402

from tests.fakes import FakeProvider  # noqa: E402


def event(uid, mid=None):
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE
    msg.self_id = "bot1"
    msg.sender = MessageMember(user_id=uid, nickname="synthetic")
    msg.message_str = "ordinary user question"
    msg.message_id = mid or ("case-" + uid)
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


_MODULE_PREFIX = f"data.plugins.{PLUGIN}"


def _purge_loaded_state():
    for m in [k for k in list(sys.modules) if str(k).startswith(_MODULE_PREFIX)]:
        sys.modules.pop(m, None)
    for h in [
        h for h in list(star_handlers_registry)
        if str(h.handler_module_path).startswith(_MODULE_PREFIX)
    ]:
        star_handlers_registry.remove(h)
    for k in [k for k in list(star_map) if str(k).startswith(_MODULE_PREFIX)]:
        star_map.pop(k, None)
    sys.modules.pop("data", None)
    sys.modules.pop("data.plugins", None)


async def load_plugin():
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
    obj._config["admin_enabled"] = True
    obj._config["relation_link_enabled"] = False
    return obj, meta, module, manager


def seed(obj, module, uid):
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
    return ident


def make_request(prompt="ordinary user question"):
    req = ProviderRequest(prompt=prompt)
    req.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0, cid="synthetic-cid")
    req.extra_user_content_parts.append(TextPart(text="OTHER_PLUGIN_SENTINEL"))
    return req


def render_exact(obj, module, ident):
    policy = importlib.import_module(module.__package__ + ".pref_profile.policy")
    builder = importlib.import_module(module.__package__ + ".pref_profile.prompt_builder")
    decision = policy.evaluate(
        policy.TurnInput(
            admin_enabled=True, is_private=True, user_enabled=True,
            user_entries=obj._store.list_entries("user", ident.key),
            bot_entries=obj._store.list_entries(
                "bot",
                module.build_identity(
                    platform_id="aiocqhttp", self_id="bot1",
                    persona_scope="persona_A",
                ).key,
            ),
            relation=None,
        ), max_items=6,
    )
    return builder.render_decision(decision, max_chars=600)


class InspectProvider(FakeProvider):
    def __init__(self, replies=None, error_script=None):
        super().__init__(replies)
        self.error_script = list(error_script or [])
        self.sent_parts: list = []

    async def text_chat(self, **kwargs):
        self.sent_parts = [
            p for m in kwargs.get("contexts", [])
            if isinstance(getattr(m, "content", None), list)
            for p in m.content if hasattr(p, "text")
        ]
        return await super().text_chat(**kwargs)


async def run_request(ev, req, provider, runner, reset_args=None):
    assert not await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
    await runner.reset(
        provider=provider, request=req,
        run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
        tool_executor=FunctionToolExecutor(),
        agent_hooks=MAIN_AGENT_HOOKS, streaming=False, **(reset_args or {}),
    )
    async for _ in run_agent(runner, max_step=2, show_tool_use=False, show_tool_call_result=False):
        pass


def add_gap(ev, meta, name, typ, priority):
    entered, release = asyncio.Event(), asyncio.Event()

    async def gap(event, context):
        entered.set()
        await release.wait()

    star_map[name] = StarMetadata(
        name=name, module_path=name, activated=True, reserved=False
    )
    gap_meta = StarHandlerMetadata(
        event_type=typ, handler_full_name=name + ".gap",
        handler_name="gap", handler_module_path=name, handler=gap,
        event_filters=[], extras_configs={"priority": priority},
    )
    star_handlers_registry.append(gap_meta)
    ev.plugins_name = [meta.name, name]
    return entered, release, gap_meta


def remove_gap(gap_meta):
    star_handlers_registry.remove(gap_meta)
    star_map.pop(gap_meta.handler_module_path, None)


async def decorator_stage():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["content_safety"]["also_use_in_response"] = False
    cfg["provider_tts_settings"]["enable"] = False
    cfg["platform_settings"]["segmented_reply"]["enable"] = False
    cfg["platform_settings"]["reply_prefix"] = ""
    cfg["platform_settings"]["reply_with_quote"] = False
    cfg["t2i"] = False

    async def no_tts(umo):
        return None

    ctx = PipelineContext(
        astrbot_config=cfg,
        plugin_manager=SimpleNamespace(
            context=SimpleNamespace(
                get_using_tts_provider_async=no_tts,
                get_using_tts_provider=lambda umo: None,
            )
        ),
        astrbot_config_id="synthetic",
    )
    stage = ResultDecorateStage()
    await stage.initialize(ctx)
    return stage


async def main() -> int:
    try:
        obj, meta, module, manager = await load_plugin()
        registry = obj._injector.registry

        # ===== W1 同文 temp（其他插件原生 mark_as_temp 在前）=====
        for label, priority in (("early(AgentBegin 前)", 0), ("late(-2000)", -2000)):
            uid = "41001" if priority == 0 else "41002"
            ident = seed(obj, module, uid)
            exact = render_exact(obj, module, ident)
            ev = event(uid)
            req = make_request(prompt="ordinary original question")
            foreign = TextPart(text=exact)
            foreign.mark_as_temp()
            req.extra_user_content_parts.append(foreign)
            entered, release, gap_meta = add_gap(
                ev, meta, f"w1_gap_{uid}", EventType.OnAgentBeginEvent, priority
            )
            provider = InspectProvider(["synthetic final reply"])
            runner = ToolLoopAgentRunner()
            task = asyncio.create_task(run_request(ev, req, provider, runner))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                assert len(provider.call_log) == 0
                all_parts = [
                    p for m in runner.run_context.messages
                    if isinstance(m.content, list) for p in m.content
                ]
                # 七轮令牌：本插件块=主体+尾部轮换令牌，按前缀识别；
                # 其他插件 temp 同文块仍按全等识别。
                foreign_matches = [p for p in all_parts if getattr(p, "text", None) == exact]
                own_matches = [
                    p for p in all_parts
                    if isinstance(getattr(p, "text", None), str)
                    and p.text != exact and p.text.startswith(exact)
                ]
                assert len(foreign_matches) == 1 and len(own_matches) == 1
                assert all(p._no_save for p in foreign_matches + own_matches)
                original_foreign = [p for p in req.extra_user_content_parts if p.text == exact]
                original_own = [
                    p for p in req.extra_user_content_parts
                    if isinstance(p.text, str) and p.text != exact and p.text.startswith(exact)
                ]
                assert len(original_foreign) == 1 and original_foreign[0] is foreign
                rec = registry._records.get(id(ev))
                assert len(original_own) == 1 and original_own[0] is rec.parts[0]
                foreign_rt, own_rt = foreign_matches[0], own_matches[0]
                obj._store.clear_user(ident.key)
                release.set()
                await asyncio.wait_for(task, 10)
                assert len(provider.call_log) == 1 and runner.done()
                own_at_provider = any(
                    p is own_rt and p.text and p.text.startswith(exact)
                    for p in provider.sent_parts
                )
                check(
                    f"W1 同文 temp {label}：其他插件 temp 块保留、本插件块失效",
                    foreign_rt.text == exact and not own_at_provider
                    and "OTHER_PLUGIN_SENTINEL" in json.dumps(provider.call_log, ensure_ascii=False),
                    f"foreign={foreign_rt.text == exact}, own@provider={own_at_provider}",
                )
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                remove_gap(gap_meta)

        # ===== W2 多步 Agent 中间装饰（真实工具 + 真实 ResultDecorateStage）=====
        stage = None
        for action in ("clear", "admin_off", "none"):
            obj._config["admin_enabled"] = True  # 上一用例可能已关闭
            uid = {"clear": "42001", "admin_off": "42002", "none": "42003"}[action]
            ident = seed(obj, module, uid)
            exact = render_exact(obj, module, ident)
            ev = event(uid)
            req = make_request(prompt="ordinary tool question")
            ev.plugins_name = [meta.name]
            calls = []

            async def local_tool(event):
                calls.append("tool")
                return "synthetic local tool result"

            req.func_tool = ToolSet([
                FunctionTool(
                    name="w2_local_tool", description="Local synthetic no-I/O tool",
                    parameters={"type": "object", "properties": {}},
                    handler=local_tool,
                )
            ])

            class ToolProvider(InspectProvider):
                async def text_chat(self, **kw):
                    await super().text_chat(**kw)
                    if len(self.call_log) == 1:
                        return LLMResponse(
                            role="assistant",
                            completion_text="I will check with the local tool.",
                            tools_call_name=["w2_local_tool"],
                            tools_call_args=[{}],
                            tools_call_ids=["w2-tool-1"],
                        )
                    return LLMResponse(role="assistant", completion_text="Synthetic final answer.")

            provider = ToolProvider(["unused"])
            runner = ToolLoopAgentRunner()
            assert not await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
            await runner.reset(
                provider=provider, request=req,
                run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
                tool_executor=FunctionToolExecutor(),
                agent_hooks=MAIN_AGENT_HOOKS, streaming=False,
            )
            if stage is None:
                stage = await decorator_stage()
            evidence = {}
            first = True
            async for _ in run_agent(runner, max_step=2, show_tool_use=False, show_tool_call_result=False):
                if first:
                    assert not runner.done() and len(provider.call_log) == 1
                    evidence["done_at_first"] = runner.done()
                    evidence["reg_before"] = len(registry._records)
                async for _ in stage.process(ev):
                    pass
                if first:
                    evidence["reg_after_decor"] = len(registry._records)
                    if action == "clear":
                        obj._store.clear_user(ident.key)
                    elif action == "admin_off":
                        obj._config["admin_enabled"] = False
                    evidence["calls_at_inval"] = len(provider.call_log)
                    first = False
            assert len(provider.call_log) == 2 and runner.done()
            assert calls == ["tool"]

            def has_exact(call):
                def _is_pref_text(t):
                    # 含七轮令牌的本插件块按前缀识别；用户同文仍全等
                    return t == exact or (
                        isinstance(t, str) and t != exact and t.startswith(exact)
                    )

                return any(
                    isinstance(m.get("content"), list)
                    and any(_is_pref_text(p.get("text")) for p in m["content"])
                    for m in call["contexts"]
                )

            stale = has_exact(provider.call_log[1])
            if action == "none":
                check(
                    "W2 正常对照：不失效时两次调用均含偏好",
                    has_exact(provider.call_log[0]) and stale
                    and "ordinary tool question" in json.dumps(provider.call_log[1], ensure_ascii=False),
                    f"first={has_exact(provider.call_log[0])}, second={stale}",
                )
            else:
                check(
                    f"W2 多步 Agent 中间装饰后 {action}：第二次调用无旧偏好、"
                    "中间装饰不提前释放、用户输入保留",
                    (not stale) and evidence.get("reg_after_decor") == 1
                    and "ordinary tool question" in json.dumps(provider.call_log[1], ensure_ascii=False)
                    and "OTHER_PLUGIN_SENTINEL" in json.dumps(provider.call_log[1], ensure_ascii=False),
                    f"evidence={evidence}, stale={stale}",
                )

        # ===== W3 stop_before_agent（真实请求钩子链中止）=====
        obj._config["admin_enabled"] = True
        ident = seed(obj, module, "43001")
        ev = event("43001")
        req = make_request()
        gap_name = "w3_stop"

        async def stop_hook(event, context):
            event.stop_event()

        star_map[gap_name] = StarMetadata(
            name=gap_name, module_path=gap_name, activated=True, reserved=False
        )
        stop_meta = StarHandlerMetadata(
            event_type=EventType.OnLLMRequestEvent,
            handler_full_name=gap_name + ".stop", handler_name="stop",
            handler_module_path=gap_name, handler=stop_hook,
            event_filters=[], extras_configs={"priority": 0},
        )
        star_handlers_registry.append(stop_meta)
        ev.plugins_name = [meta.name, gap_name]
        ev_ref = weakref.ref(ev)
        stopped = await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
        check("W3a 请求钩子链被真实 stop 中止", stopped is True and ev.is_stopped())
        star_handlers_registry.remove(stop_meta)
        star_map.pop(gap_name, None)
        del ev, req
        await asyncio.sleep(0)
        gc.collect()
        check(
            "W3 stop 中止后记录回收（无 Agent/回复路径）",
            len(registry._records) == 0 and ev_ref() is None,
            f"records={len(registry._records)}, ev_alive={ev_ref() is not None}",
        )

        # ===== W4 task_cancelled（原生压缩等待中取消轮次 Task）=====
        obj._config["admin_enabled"] = True
        ident = seed(obj, module, "44001")
        ev = event("44001")
        req = make_request()
        ev.plugins_name = [meta.name]
        entered, release = asyncio.Event(), asyncio.Event()

        class SummaryProvider(FakeProvider):
            async def text_chat(self, **kw):
                entered.set()
                await release.wait()
                return await super().text_chat(**kw)

        summary = SummaryProvider(["unused"])
        provider = FakeProvider(["should not be called"])
        provider.provider_config["max_context_tokens"] = 2000
        req.contexts = [
            {"role": "user", "content": "ordinary historical text " * 8000},
            {"role": "assistant", "content": "ordinary old answer"},
        ]
        runner = ToolLoopAgentRunner()
        ev_ref4 = weakref.ref(ev)

        async def run_with_compression():
            assert not await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
            await runner.reset(
                provider=provider, request=req,
                run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
                tool_executor=FunctionToolExecutor(),
                agent_hooks=MAIN_AGENT_HOOKS, streaming=False,
                llm_compress_provider=summary,
                llm_compress_keep_recent_ratio=0.15,
            )
            async for _ in run_agent(runner, max_step=2, show_tool_use=False, show_tool_call_result=False):
                pass

        task = asyncio.create_task(run_with_compression())
        await asyncio.wait_for(entered.wait(), 10)
        assert id(ev) in registry._records
        task.cancel()
        outcome = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcome[0], asyncio.CancelledError) and task.cancelled()
        release.set()
        del task, outcome, ev, req, runner, provider, summary
        await asyncio.sleep(0)
        gc.collect()
        check(
            "W4 真实取消（压缩等待中 cancel 轮次 Task）后记录释放",
            len(registry._records) == 0,
            f"records={len(registry._records)}",
        )

        # ===== W5 正常对照 =====
        obj._config["admin_enabled"] = True
        ident = seed(obj, module, "45001")
        ev = event("45001")
        req = make_request()
        provider = InspectProvider(["reply"])
        runner = ToolLoopAgentRunner()
        await run_request(ev, req, provider, runner)
        own_temp = any(
            bool(getattr(p, "_no_save", False)) and p.text
            for p in provider.sent_parts
        )
        check(
            "W5 正常对照：偏好 temp 块与用户输入到达 Provider",
            own_temp
            and any(p.text == "ordinary user question" for p in provider.sent_parts),
        )
    except Exception:
        import traceback

        traceback.print_exc()
        check("W 系列未预期异常", False, "")
    finally:
        shutil.rmtree(OUT, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
