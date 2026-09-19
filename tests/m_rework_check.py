"""十一轮定向回归（M1/M2/M3：宿主来源映射原型边界）。

运行于隔离宿主源码副本（含宿主来源映射补丁 v2）。三项在旧组合
（6d73bb5 插件 + v1 原型补丁宿主）上以真实行为失败、本候选通过；
原生宿主（未打补丁）上 M1/M3 仍受接口限制、按受阻如实记录，不在
本脚本断言范围。

  M1  通道锁定：他人 AgentBegin 钩子复制本插件块后**移除本插件原
      运行时块**，clear 只清映射登记的本插件实例（已不在内容中=
      无自身目标），他人完整副本保留并到达 Provider；绝不回退
      令牌内容匹配。
  M2  快照语义：默认关闭本插件（无注入、无记录）时，其他插件
      extras 的序列化快照不受其源对象在媒体 I/O 等待期间的修改
      影响——Provider 收到先前的快照文本，而非修改后文本。
  M3  组装等待中正式停用：图片 I/O 等待点执行真实 turn_off_plugin
      （此后本插件钩子被宿主过滤），恢复后旧偏好不得送达首次
      Provider（源对象失效标记由宿主映射绑定处补偿置空）；用户
      原问题、其他插件内容保留，请求正常完成。
  对照  媒体等待不失效：偏好正常注入送达。

运行：<venv>/Scripts/python.exe tests/m_rework_check.py
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
OUT = Path(tempfile.mkdtemp(prefix="pref_m11_"))


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.star import Context  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.agent.message import TextPart  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook  # noqa: E402
from astrbot.core.star.star import star_map  # noqa: E402
from astrbot.core.star.star_handler import EventType, star_handlers_registry  # noqa: E402
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

_MODULE_PREFIX = f"data.plugins.{PLUGIN}"


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
    ident = module.build_identity(platform_id="aiocqhttp", self_id="bot1",
                                  persona_scope="persona_A", sender_id=uid)
    bot = module.build_identity(platform_id="aiocqhttp", self_id="bot1",
                                persona_scope="persona_A")
    obj._store.set_user_enabled(ident.key, True)
    obj._store.upsert_entry(owner_kind="user", identity_key=ident.key,
                            tag_id="joke", tag_display="合成偏好", status="like",
                            intensity=4, source="self_declared")
    if not obj._store.list_entries("bot", bot.key):
        obj._store.upsert_entry(owner_kind="bot", identity_key=bot.key,
                                tag_id="joke", tag_display="合成偏好", status="like",
                                intensity=4, source="admin_template")
    return ident, bot


def make_request(prompt):
    from astrbot.api.provider import ProviderRequest
    req = ProviderRequest(prompt=prompt)
    req.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0,
                                       cid="synthetic-cid")
    return req


class RecordingProvider(FakeProvider):
    async def text_chat(self, **kw):
        self.sent_parts = [
            p for m in kw.get("contexts", [])
            if isinstance(getattr(m, "content", None), list) for p in m.content
        ]
        self.sent_texts = [
            p.text for p in self.sent_parts
            if isinstance(getattr(p, "text", None), str)
        ]
        return await super().text_chat(**kw)


def assert_normal(provider, runner, tag):
    assert len(provider.call_log) == 1, f"{tag}: Provider 调用 {len(provider.call_log)}"
    assert str(runner._state) == "AgentState.DONE", f"{tag}: 终态 {runner._state}"
    assert runner.final_llm_resp is not None and \
        runner.final_llm_resp.role == "assistant", f"{tag}: role 非 assistant"
    assert runner.final_llm_resp.completion_text == "synthetic final reply", \
        f"{tag}: 回复内容不符"


def parts(rc):
    return [p for m in rc.messages
            if isinstance(m.content, list) for p in m.content]


async def m1_channel_lock(obj, meta, module):
    """M1：映射通道锁定——原块被移除后 clear 不回退令牌、副本保留。"""
    uid = "91001"
    ident, _ = seed(obj, module, uid)
    ev, req = event(uid), make_request("ordinary m1 question")
    ev.plugins_name = [meta.name]
    entered, release = asyncio.Event(), asyncio.Event()
    seen = {}
    gap_name = "m1_gap_" + uid
    ev.plugins_name.append(gap_name)

    async def gap(event, rc):
        rec = obj._injector.registry.get(event)
        source = rec.parts[0]
        pairs = getattr(req, "_extra_runtime_pairs", None) or []
        own = next((rt for src, rt in pairs if src is source), None)
        if own is None:  # 原生宿主等价定位（本脚本只在补丁宿主断言）
            own = next((p for p in parts(rc) if p.text == source.text), None)
        foreign = TextPart(text=own.text).mark_as_temp()
        current = next(m for m in reversed(rc.messages) if isinstance(m.content, list))
        current.content.append(foreign)
        # M1 关键动作：移除本插件原运行时块
        current.content[:] = [p for p in current.content if p is not own]
        seen.update(foreign=foreign, before=foreign.text, own_full=own.text,
                    own_rt=own)
        # 旧候选（6d73bb5）无 channel 字段：按 token 通道语义观测，
        # 失败必须来自真实行为（误删/快照改变/偏好送达），不能以
        # AttributeError 崩溃充当失败对照。
        seen.update(channel=getattr(rec, "channel", "token"), pairs=len(pairs))
        entered.set()
        await release.wait()
        rec2 = obj._injector.registry.get(event)
        seen["after_channel"] = getattr(rec2, "channel", "token") if rec2 else "released"
        seen["after_rt_text"] = [getattr(rt, "text", "")[:12]
                                 for _s, rt in rec.runtime_pairs]

    gap_name = gap_name.replace("m1_gap_", "m1_gap_")  # noqa: F841
    star_map[gap_name] = __import__("astrbot.core.star.star", fromlist=["StarMetadata"]).StarMetadata(
        name=gap_name, module_path=gap_name, activated=True, reserved=False)
    gap_meta = __import__("astrbot.core.star.star_handler", fromlist=["StarHandlerMetadata"]).StarHandlerMetadata(
        event_type=EventType.OnAgentBeginEvent, handler_full_name=gap_name + ".gap",
        handler_name="gap", handler_module_path=gap_name, handler=gap,
        event_filters=[], extras_configs={"priority": -2000})
    star_handlers_registry.append(gap_meta)
    provider, runner = RecordingProvider(["synthetic final reply"]), ToolLoopAgentRunner()
    async def flow():
        assert not await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
        await runner.reset(provider=provider, request=req,
                           run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
                           tool_executor=FunctionToolExecutor(),
                           agent_hooks=MAIN_AGENT_HOOKS, streaming=False)
        async for _ in run_agent(runner, max_step=2, show_tool_use=False,
                                 show_tool_call_result=False):
            pass
    task = asyncio.create_task(flow())
    try:
        await asyncio.wait_for(entered.wait(), 15)
        assert len(provider.call_log) == 0
        obj._store.clear_user(ident.key)
        release.set()
        await asyncio.wait_for(task, 15)
        assert_normal(provider, runner, "M1")
        foreign_ok = (seen["foreign"].text == seen["before"]
                      and seen["before"] in provider.sent_texts)
        # 本插件失效判定（对象级）：映射实例不以非空文本出现在 Provider。
        # 副本与偏好文本相同，按文本判定无法区分，必须按对象。
        own_absent = not any(
            p is seen["own_rt"] and p.text for p in provider.sent_parts)
        check("M1 通道锁定：移除原块后 clear 不回退令牌，他人副本完整保留",
              foreign_ok and own_absent,
              f"foreign_ok={foreign_ok}, own_absent={own_absent}, "
              f"channel={seen.get('channel')}, pairs={seen.get('pairs')}, "
              f"after_channel={seen.get('after_channel')}, "
              f"after_rt_text={seen.get('after_rt_text')}")
    finally:
        release.set()
        if not task.done():
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
        star_handlers_registry.remove(gap_meta)
        star_map.pop(gap_name, None)


async def m2_snapshot(obj, meta, module, manager):
    """M2：快照语义——默认关闭时其他插件 extras 的源对象等待期修改不影响本轮。"""
    obj._config.update(admin_enabled=False, relation_link_enabled=False)
    ev, req = event("92001"), make_request("ordinary m2 question")
    req.image_urls = ["local-synthetic-image"]
    foreign = TextPart(text="FOREIGN_BEFORE_SERIALIZE")
    req.extra_user_content_parts.append(foreign)
    provider, runner = RecordingProvider(["synthetic final reply"]), ToolLoopAgentRunner()
    entered, release = asyncio.Event(), asyncio.Event()

    async def media_io(_self, **kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(to_data_url=lambda: "data:image/png;base64,c3ludGhldGlj")

    entities = importlib.import_module(
        type(req).__module__)
    with patch.object(entities.MediaResolver, "to_base64_data", new=media_io):
        async def flow():
            assert not await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
            await runner.reset(provider=provider, request=req,
                               run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
                               tool_executor=FunctionToolExecutor(),
                               agent_hooks=MAIN_AGENT_HOOKS, streaming=False)
            async for _ in run_agent(runner, max_step=2, show_tool_use=False,
                                     show_tool_call_result=False):
                pass
        task = asyncio.create_task(flow())
        try:
            await asyncio.wait_for(entered.wait(), 15)
            assert len(provider.call_log) == 0
            assert obj._injector.registry._records == {}, "默认关闭不应有注入记录"
            foreign.text = "FOREIGN_CHANGED_DURING_MEDIA"
            release.set()
            await asyncio.wait_for(task, 15)
            assert_normal(provider, runner, "M2")
            check("M2 快照语义：源对象等待期修改不影响本轮序列化快照",
                  "FOREIGN_BEFORE_SERIALIZE" in provider.sent_texts
                  and "FOREIGN_CHANGED_DURING_MEDIA" not in provider.sent_texts,
                  f"sent_texts={[t for t in provider.sent_texts if 'FOREIGN' in t]}")
        finally:
            release.set()
            if not task.done():
                task.cancel(); await asyncio.gather(task, return_exceptions=True)


async def m3_media_turn_off(obj, meta, module, manager):
    """M3：组装等待中正式停用——旧偏好不得送达首次 Provider。"""
    obj._config.update(admin_enabled=True, relation_link_enabled=False)
    uid = "93001"
    ident, _ = seed(obj, module, uid)
    ev, req = event(uid), make_request("ordinary m3 question")
    req.image_urls = ["local-synthetic-image"]
    req.extra_user_content_parts.append(TextPart(text="FOREIGN_M3_KEEP"))
    provider, runner = RecordingProvider(["synthetic final reply"]), ToolLoopAgentRunner()
    entered, release = asyncio.Event(), asyncio.Event()
    own_text = {}

    async def media_io(_self, **kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(to_data_url=lambda: "data:image/png;base64,c3ludGhldGlj")

    entities = importlib.import_module(type(req).__module__)

    async def turn_off():
        async def get(key=None, default=None):
            return default

        async def put(key, value):
            return None
        with patch.object(sm.sp, "global_get", new=get), \
                patch.object(sm.sp, "global_put", new=put):
            await manager.turn_off_plugin(meta.name)

    with patch.object(entities.MediaResolver, "to_base64_data", new=media_io):
        async def flow():
            assert not await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
            rec = obj._injector.registry.get(ev)
            own_text["value"] = rec.parts[0].text if rec else None
            await runner.reset(provider=provider, request=req,
                               run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
                               tool_executor=FunctionToolExecutor(),
                               agent_hooks=MAIN_AGENT_HOOKS, streaming=False)
            async for _ in run_agent(runner, max_step=2, show_tool_use=False,
                                     show_tool_call_result=False):
                pass
        task = asyncio.create_task(flow())
        try:
            await asyncio.wait_for(entered.wait(), 15)
            assert len(provider.call_log) == 0
            await turn_off()
            release.set()
            await asyncio.wait_for(task, 15)
            assert_normal(provider, runner, "M3")
            own_sent = bool(own_text["value"]) and any(
                getattr(p, "text", None) == own_text["value"]
                for p in provider.sent_parts)
            check("M3 组装等待中正式停用：旧偏好不送达首次 Provider",
                  (not own_sent) and meta.activated is False
                  and "ordinary m3 question" in provider.sent_texts
                  and "FOREIGN_M3_KEEP" in provider.sent_texts,
                  f"own_sent={own_sent}, activated={meta.activated}")
        finally:
            release.set()
            if not task.done():
                task.cancel(); await asyncio.gather(task, return_exceptions=True)


async def media_none_control(obj, meta, module):
    """对照：媒体等待不失效——偏好正常注入送达（防「一律禁用」假通过）。"""
    obj._config.update(admin_enabled=True, relation_link_enabled=False)
    uid = "94001"
    ident, _ = seed(obj, module, uid)
    ev, req = event(uid), make_request("ordinary control question")
    req.image_urls = ["local-synthetic-image"]
    provider, runner = RecordingProvider(["synthetic final reply"]), ToolLoopAgentRunner()
    entered, release = asyncio.Event(), asyncio.Event()
    own_text = {}

    async def media_io(_self, **kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(to_data_url=lambda: "data:image/png;base64,c3ludGhldGlj")

    entities = importlib.import_module(type(req).__module__)
    with patch.object(entities.MediaResolver, "to_base64_data", new=media_io):
        async def flow():
            assert not await call_event_hook(ev, EventType.OnLLMRequestEvent, req)
            rec = obj._injector.registry.get(ev)
            own_text["value"] = rec.parts[0].text if rec else None
            await runner.reset(provider=provider, request=req,
                               run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
                               tool_executor=FunctionToolExecutor(),
                               agent_hooks=MAIN_AGENT_HOOKS, streaming=False)
            async for _ in run_agent(runner, max_step=2, show_tool_use=False,
                                     show_tool_call_result=False):
                pass
        task = asyncio.create_task(flow())
        try:
            await asyncio.wait_for(entered.wait(), 15)
            release.set()
            await asyncio.wait_for(task, 15)
            assert_normal(provider, runner, "CTL")
            check("对照 媒体等待不失效：偏好正常注入送达", bool(own_text["value"]) and any(
                getattr(p, "text", None) == own_text["value"]
                for p in provider.sent_parts))
        finally:
            release.set()
            if not task.done():
                task.cancel(); await asyncio.gather(task, return_exceptions=True)


async def main_async():
    obj, meta, module, manager = await load_plugin()
    await m1_channel_lock(obj, meta, module)
    await m2_snapshot(obj, meta, module, manager)
    await media_none_control(obj, meta, module)
    # M3 正式停用会关闭插件 DB/停用 handler，必须最后执行
    await m3_media_turn_off(obj, meta, module, manager)
    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    print("== m_rework_check（十一轮 M1/M2/M3，须运行于补丁宿主副本）==")
    asyncio.run(main_async())
