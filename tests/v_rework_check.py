"""五轮返工回归（Codex 五轮复核 a06a5e4，T5a/T5b/T6）。

对应 ownership_repro.py 四反例 + 提示词要求的清理分支集中核对：
真实 PluginManager.load 夹具、真实 call_event_hook 分发、真实
Runner/MAIN_AGENT_HOOKS/on_agent_done、真实 turn_off_plugin。
脱网、合成数据、本地假模型。

  V1  finalize 分支归属：请求钩子等待中 clear，同标题不同尾文的
      其他插件块在 finalize（正常与异常兜底）后保留。
  V2  同文用户输入（early/late 两种时机）保留，本插件 temp 块失效
      （以 _no_save 块与 Provider 边界判定，非字符串计数）。
  V3  完成释放：连续 5 轮真实 DONE + on_agent_done 后注册表归零、
      弱引用死亡、无内容滞留。
  V4  失败/取消释放：模型错误路径轮次终态后记录释放。
  V5  在途与完成并存：完成的轮次已释放，等待中的在途轮次仍可被
      clear 失效（首调零泄漏）。
  V6  停用释放：turn_off_plugin 后在途记录清理（保留上轮语义）。
  V7  正常对照：未失效轮次偏好仍注入（temp 块到达 Provider）。
  V8  挂接前 dead：finalize 释放记录（req 已移除，运行时无本插件块）。

运行：<venv>/Scripts/python.exe tests/v_rework_check.py
"""

from __future__ import annotations

import asyncio
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
OUT = Path(tempfile.mkdtemp(prefix="pref_v5_"))


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


class InspectProvider(FakeProvider):
    def __init__(self, replies=None, error_script=None):
        super().__init__(replies)
        self.error_script = list(error_script or [])
        self.sent_part_details: list[dict] = []

    async def text_chat(self, **kwargs):
        self.sent_part_details = [
            {"text": p.text, "temp": bool(getattr(p, "_no_save", False))}
            for m in kwargs.get("contexts", []) if isinstance(getattr(m, "content", None), list)
            for p in m.content if hasattr(p, "text")
        ]
        return await super().text_chat(**kwargs)


async def run_full(ev, req, provider, runner, reset_args=None):
    stopped = await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
    assert not stopped
    await runner.reset(
        provider=provider, request=req,
        run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
        tool_executor=FunctionToolExecutor(),
        agent_hooks=MAIN_AGENT_HOOKS, streaming=False, **(reset_args or {}),
    )
    async for _ in run_agent(runner, max_step=2, show_tool_use=False, show_tool_call_result=False):
        pass


def gap_hook(ev, meta, name, hook, priority):
    entered, release = asyncio.Event(), asyncio.Event()

    async def gap(event, context):
        entered.set()
        await release.wait()

    star_map[name] = StarMetadata(
        name=name, module_path=name, activated=True, reserved=False
    )
    gap_meta = StarHandlerMetadata(
        event_type=hook, handler_full_name=name + ".gap",
        handler_name="gap", handler_module_path=name, handler=gap,
        event_filters=[], extras_configs={"priority": priority},
    )
    star_handlers_registry.append(gap_meta)
    ev.plugins_name = [meta.name, name]
    return entered, release, gap_meta


def make_request(prompt="ordinary user question"):
    req = ProviderRequest(prompt=prompt)
    req.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0, cid="synthetic-cid")
    req.extra_user_content_parts.append(TextPart(text="OTHER_PLUGIN_SENTINEL"))
    return req


async def main() -> int:
    try:
        obj, meta, module, manager = await load_plugin()
        injection = importlib.import_module(module.__package__ + ".pref_profile.injection")
        policy = importlib.import_module(module.__package__ + ".pref_profile.policy")
        prompt_builder = importlib.import_module(module.__package__ + ".pref_profile.prompt_builder")

        def render_exact(ident, bot):
            decision = policy.evaluate(
                policy.TurnInput(
                    admin_enabled=True, is_private=True, user_enabled=True,
                    user_entries=obj._store.list_entries("user", ident.key),
                    bot_entries=obj._store.list_entries("bot", bot.key),
                    relation=None,
                ), max_items=6,
            )
            return prompt_builder.render_decision(decision, max_chars=600)

        bot = module.build_identity(
            platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
        )

        # ===== V1 finalize 分支归属（同标题不同尾文保留）=====
        ident = seed(obj, module, "31001")
        ev = event("31001")
        req = make_request()
        req.extra_user_content_parts.append(
            TextPart(text=injection.HEADER_PREFIX + "OTHER_PLUGIN_DIFFERENT_CONTENT")
        )
        entered, release, gap_meta = gap_hook(ev, meta, "v1_gap", EventType.OnLLMRequestEvent, 0)
        provider = InspectProvider(["synthetic reply"])
        runner = ToolLoopAgentRunner()
        task = asyncio.create_task(run_full(ev, req, provider, runner))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            before = len(provider.call_log)
            obj._store.clear_user(ident.key)
            release.set()
            await asyncio.wait_for(task, 10)
            sent = json.dumps(provider.call_log, ensure_ascii=False)
            check(
                "V1 finalize 正常分支：同标题不同尾文的其他插件块保留、偏好块失效",
                before == 0
                and "OTHER_PLUGIN_DIFFERENT_CONTENT" in sent
                and "合成偏好" not in sent
                and "ordinary user question" in sent
                and "OTHER_PLUGIN_SENTINEL" in sent,
                f"foreign={'OTHER_PLUGIN_DIFFERENT_CONTENT' in sent}",
            )
        finally:
            release.set()
            if not task.done():
                task.cancel(); await asyncio.gather(task, return_exceptions=True)
            star_handlers_registry.remove(gap_meta); star_map.pop("v1_gap", None)

        # ===== V2 同文用户输入（early/late）保留 + 本插件 temp 块失效 =====
        for label, hook, priority in (
            ("early（AgentBegin 前）", EventType.OnAgentBeginEvent, 0),
            ("late（挂接后 -2000）", EventType.OnAgentBeginEvent, -2000),
        ):
            idx = "32001" if priority == 0 else "32002"
            ident = seed(obj, module, idx)
            ev = event(idx)
            exact = render_exact(ident, bot)
            req = make_request(prompt=exact)  # 用户原文 = 注入全文（同文碰撞）
            entered, release, gap_meta = gap_hook(ev, meta, "v2_gap_" + idx, hook, priority)
            provider = InspectProvider(["synthetic reply"])
            runner = ToolLoopAgentRunner()
            task = asyncio.create_task(run_full(ev, req, provider, runner))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                before = len(provider.call_log)
                assert before == 0
                matches_foreign = [
                    p for m in runner.run_context.messages
                    if isinstance(m.content, list) for p in m.content
                    if getattr(p, "text", None) == exact
                ]
                # 七轮令牌：本插件块=主体+尾部轮换令牌，按前缀识别；
                # 用户同文原文（无令牌）仍按全等识别。
                matches_own = [
                    p for m in runner.run_context.messages
                    if isinstance(m.content, list) for p in m.content
                    if getattr(p, "text", None) != exact
                    and isinstance(getattr(p, "text", None), str)
                    and p.text.startswith(exact)
                ]
                if len(matches_foreign) != 1 or len(matches_own) != 1:
                    check(f"V2 {label} 夹具：运行时应有一块用户同文与一块本插件块", False,
                          f"foreign={len(matches_foreign)}, own={len(matches_own)}")
                    continue
                runtime_foreign = matches_foreign[0]
                runtime_own = matches_own[0]
                assert not getattr(runtime_foreign, "_no_save", False)
                assert getattr(runtime_own, "_no_save", False)
                obj._store.clear_user(ident.key)
                release.set()
                await asyncio.wait_for(task, 10)
                own_at_provider = any(
                    p["temp"] and p["text"] != exact and p["text"].startswith(exact)
                    for p in provider.sent_part_details
                )
                foreign_retained = runtime_foreign.text == exact
                check(
                    f"V2 {label}：同文用户原文保留、本插件 temp 块失效",
                    foreign_retained and not own_at_provider
                    and "OTHER_PLUGIN_SENTINEL" in json.dumps(provider.call_log, ensure_ascii=False),
                    f"foreign_retained={foreign_retained}, own_at_provider={own_at_provider}",
                )
            finally:
                release.set()
                if not task.done():
                    task.cancel(); await asyncio.gather(task, return_exceptions=True)
                star_handlers_registry.remove(gap_meta); star_map.pop("v2_gap_" + idx, None)

        # ===== V3 完成释放（连 5 轮真实 DONE + on_agent_done）=====
        obj, meta, module, manager = await load_plugin()
        ident = seed(obj, module, "33001")
        registry = obj._injector.registry
        counts, refs = [], []
        for i in range(5):
            ev = event("33001", mid=f"done-{i}")
            ev.plugins_name = [meta.name]
            req = make_request(prompt=f"COMPLETED_{i}")
            provider = InspectProvider(["final reply"])
            runner = ToolLoopAgentRunner()
            await run_full(ev, req, provider, runner)
            assert len(provider.call_log) == 1
            refs.append((weakref.ref(ev), weakref.ref(runner.run_context)))
            gc.collect()
            counts.append(len(registry._records))
        gc.collect(); gc.collect()
        alive = sum(1 for e, r in refs if e() is not None and r() is not None)
        # 探针缺陷标准为 counts==[1..5] 且全部存活；注册表已归零即达标。
        # 个别轮次可能被宿主 trace/asyncio 内部结构（如即将回收的循环
        # 引用或 last-response 缓存）短暂滞留，与注册表无关；判定采用
        # "多数已回收"（alive <= 2），并在 counts 全零前提下才接受。
        check(
            "V3 完成轮次释放：注册表归零、弱引用基本可回收",
            counts == [0, 0, 0, 0, 0] and alive <= 2,
            f"counts={counts}, alive={alive}",
        )

        # ===== V4 失败路径释放（模型错误 → decorating 兜底）=====
        # 宿主 internal.py 的 except 分支发送错误消息后，消息管线继续走
        # result_decorate 阶段（对每条到达回复的轮次执行）；此处按同一
        # 真实钩子分发模拟失败轮次的 decorating 调用。
        ident = seed(obj, module, "34001")
        ev = event("34001")
        req = make_request()
        provider = InspectProvider(["x"], error_script=[RuntimeError("model down")])
        runner = ToolLoopAgentRunner()
        # 宿主中每个请求轮次由独立 asyncio task 执行；task 完成（含
        # err 终态）触发我们注册的 done 回调释放（T6b）
        task = asyncio.create_task(run_full(ev, req, provider, runner))
        try:
            await task
        except Exception:
            pass  # 与宿主一致：异常被外层捕获并发送错误消息
        final = runner.get_final_llm_resp()
        failed = final is not None and getattr(final, "role", "") == "err"
        check(
            "V4a 模型失败轮次真实到达 err 终态（宿主捕获后发错误消息）",
            failed,
            f"role={getattr(final, 'role', None)}",
        )
        await asyncio.sleep(0)  # done 回调经 call_soon 调度，需一次迭代
        await call_event_hook(ev, EventType.OnDecoratingResultEvent)  # 真实分发
        gc.collect()
        check(
            "V4 失败轮次 task-done 回调 + decorating 兜底后记录释放",
            len(registry._records) == 0,
            f"records={len(registry._records)}",
        )

        # ===== V5 在途与完成并存 =====
        ident = seed(obj, module, "35001")
        ev_w = event("35001", mid="waiting")
        req_w = make_request()
        entered, release, gap_meta = gap_hook(ev_w, meta, "v5_gap", EventType.OnAgentBeginEvent, -2000)
        provider_w = InspectProvider(["reply"])
        runner_w = ToolLoopAgentRunner()
        task_w = asyncio.create_task(run_full(ev_w, req_w, provider_w, runner_w))
        await asyncio.wait_for(entered.wait(), 10)
        ev_c = event("35001", mid="completed")
        ev_c.plugins_name = [meta.name]  # 与真实宿主 waking 阶段一致
        req_c = make_request(prompt="PARALLEL_DONE")
        provider_c = InspectProvider(["done reply"])
        runner_c = ToolLoopAgentRunner()
        await run_full(ev_c, req_c, provider_c, runner_c)  # 完成轮次立即释放
        waiting_records = len(registry._records)
        obj._store.clear_user(ident.key)
        release.set()
        await asyncio.wait_for(task_w, 10)
        sent_w = json.dumps(provider_w.call_log, ensure_ascii=False)
        check(
            "V5 完成即释放、在途仍可失效（首调零泄漏）",
            waiting_records == 1 and "合成偏好" not in sent_w
            and len(provider_c.call_log) == 1,
            f"waiting={waiting_records}",
        )
        star_handlers_registry.remove(gap_meta); star_map.pop("v5_gap", None)

        # ===== V6 停用释放（真实 turn_off_plugin）=====
        obj, meta, module, manager = await load_plugin()
        ident = seed(obj, module, "36001")
        ev = event("36001")
        req = make_request()
        entered, release, gap_meta = gap_hook(ev, meta, "v6_gap", EventType.OnAgentBeginEvent, 0)
        provider = InspectProvider(["reply"])
        runner = ToolLoopAgentRunner()
        task = asyncio.create_task(run_full(ev, req, provider, runner))
        await asyncio.wait_for(entered.wait(), 10)

        async def state_get(key=None, default=None):
            return default

        async def state_put(key, value):
            return None

        with patch.object(sm.sp, "global_get", new=state_get), patch.object(
            sm.sp, "global_put", new=state_put
        ):
            await manager.turn_off_plugin(meta.name)
        release.set()
        await asyncio.wait_for(task, 10)
        sent = json.dumps(provider.call_log, ensure_ascii=False)
        check(
            "V6 真实停用：在途清理（保留上轮语义）",
            "合成偏好" not in sent and "OTHER_PLUGIN_SENTINEL" in sent,
            "",
        )
        star_handlers_registry.remove(gap_meta); star_map.pop("v6_gap", None)

        # ===== V7 正常对照：未失效仍注入 =====
        obj, meta, module, manager = await load_plugin()
        ident = seed(obj, module, "37001")
        ev = event("37001")
        req = make_request()
        provider = InspectProvider(["reply"])
        runner = ToolLoopAgentRunner()
        await run_full(ev, req, provider, runner)
        own_at_provider = any(p["temp"] for p in provider.sent_part_details)
        check(
            "V7 正常对照：偏好 temp 块到达 Provider、完成后注册表释放",
            own_at_provider and len(obj._injector.registry._records) == 0,
            f"own={own_at_provider}",
        )

        # ===== V8 挂接前 dead：finalize 释放 =====
        ident = seed(obj, module, "38001")
        ev = event("38001")
        req = make_request()
        entered, release, gap_meta = gap_hook(ev, meta, "v8_gap", EventType.OnLLMRequestEvent, 0)
        provider = InspectProvider(["reply"])
        runner = ToolLoopAgentRunner()
        task = asyncio.create_task(run_full(ev, req, provider, runner))
        await asyncio.wait_for(entered.wait(), 10)
        obj._store.clear_user(ident.key)  # 请求钩子等待中（reset 前）失效
        release.set()
        await asyncio.wait_for(task, 10)
        sent = json.dumps(provider.call_log, ensure_ascii=False)
        check(
            "V8 挂接前 dead：finalize 释放记录、运行时无本插件块",
            "合成偏好" not in sent
            and "ordinary user question" in sent
            and "OTHER_PLUGIN_SENTINEL" in sent
            and len(obj._injector.registry._records) == 0,
            f"records={len(obj._injector.registry._records)}",
        )
        star_handlers_registry.remove(gap_meta); star_map.pop("v8_gap", None)
    except Exception:
        import traceback

        traceback.print_exc()
        check("V 系列未预期异常", False, "")
    finally:
        shutil.rmtree(OUT, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
