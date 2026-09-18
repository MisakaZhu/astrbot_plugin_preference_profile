"""八轮返工回归（Codex 八轮复核 abdba20，T7 预算 + 转换链归属证据）。

真实 PluginManager.load 夹具、真实 call_event_hook 分发、真实 Runner。
脱网、合成数据、本地假模型。

  X1  总字符预算：max_inject_chars == 指导主体长时，注入与送达总长
      ≤ 上限（旧 abdba20 注入=主体+14 令牌超上限，FAIL）。
  X2  对照（主体长+令牌长）：完整主体+令牌恰好占满上限送达。
  X3  小预算（连头部都放不下）：不注入、无注册、Provider 无偏好。
  X4  正常对照：默认 600 配置注入成功（排除「一律禁用」假通过）。
  X5  转换链事实（宿主信道证据）：model_dump_for_context →
      Message.model_validate 之后对象身份丢失、TextPart 模型字段仅
      type/text、私有属性不入 dump、同文 temp 复制副本与本尊在
      (type,text,_no_save) 上可观测不可区分。
  X6  受阻声明：T5b「finalize 后/AgentBegin 期合法复制的同文 temp
      副本」与本尊不可区分（宿主 Part 接口无归属信道），保持未修，
      交受阻证据；本行不进 PASS/FAIL 计数。

运行：<venv>/Scripts/python.exe tests/x_rework_check.py
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
OUT = Path(tempfile.mkdtemp(prefix="pref_x8_"))


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
from astrbot.core.agent.message import Message, TextPart  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook  # noqa: E402
from astrbot.core.star.star import star_map  # noqa: E402
from astrbot.core.star.star_handler import star_handlers_registry  # noqa: E402
from astrbot.core.star.star_manager import PluginManager  # noqa: E402
import astrbot.core.star.star_manager as sm  # noqa: E402

_MODULE_PREFIX = f"data.plugins.{PLUGIN}"


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


def render_full(obj, module, ident, bot):
    policy = importlib.import_module(module.__package__ + ".pref_profile.policy")
    builder = importlib.import_module(module.__package__ + ".pref_profile.prompt_builder")
    decision = policy.evaluate(policy.TurnInput(
        admin_enabled=True, is_private=True, user_enabled=True,
        user_entries=obj._store.list_entries("user", ident.key),
        bot_entries=obj._store.list_entries("bot", bot.key), relation=None), max_items=6)
    return builder.render_decision(decision, max_chars=600)


def make_request(prompt):
    from astrbot.api.provider import ProviderRequest
    req = ProviderRequest(prompt=prompt)
    req.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0,
                                       cid="synthetic-cid")
    return req


async def run_turn(obj, meta, ev, req):
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_hooks import MAIN_AGENT_HOOKS
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot.core.astr_agent_run_util import run_agent
    from tests.fakes import FakeProvider

    class BudgetProvider(FakeProvider):
        async def text_chat(self, **kw):
            self.sent_texts = [
                p.text for m in kw.get("contexts", [])
                if isinstance(getattr(m, "content", None), list)
                for p in m.content if isinstance(getattr(p, "text", None), str)
            ]
            # E1：必须返回父类真实 LLMResponse——丢弃响应会让宿主走
            # ERROR 终态（role=err），runner.done() 仍为 True，不能当
            # 正常完成。
            return await super().text_chat(**kw)

    provider, runner = BudgetProvider(["synthetic final reply"]), ToolLoopAgentRunner()
    assert not await call_event_hook(ev, EventType_LLM(), req)
    await runner.reset(provider=provider, request=req,
                       run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
                       tool_executor=FunctionToolExecutor(), agent_hooks=MAIN_AGENT_HOOKS,
                       streaming=False)
    async for _ in run_agent(runner, max_step=2, show_tool_use=False, show_tool_call_result=False):
        pass
    # 正常完成终态：DONE、最终 role=assistant、预期合成回复。
    assert len(provider.call_log) == 1 and runner.done()
    assert runner.final_llm_resp is not None, "无最终响应（异常/空终态）"
    assert runner.final_llm_resp.role == "assistant", \
        f"终态非 assistant: {runner.final_llm_resp.role}"
    assert runner.final_llm_resp.completion_text == "synthetic final reply", \
        f"回复内容不符: {runner.final_llm_resp.completion_text!r}"
    return provider, obj._injector.registry


def EventType_LLM():
    from astrbot.core.star.star_handler import EventType
    return EventType.OnLLMRequestEvent


async def x_budget(obj, meta, module):
    """X1/X2：预算语义（上限含最终表示中的全部模型可见字符）。"""
    for label, extra, expect_full, uid in (
            ("X1 预算恰好=主体长", 0, False, "81011"),
            ("X2 对照（主体长+14）", 14, True, "81012")):
        ident, bot = seed(obj, module, uid)
        body = render_full(obj, module, ident, bot)
        assert isinstance(body, str) and len(body) > 0
        limit = len(body) + extra
        obj._config["max_inject_chars"] = limit
        ev = event(uid)
        ev.plugins_name = [meta.name]
        req = make_request("ordinary budget question")
        provider, registry = await run_turn(obj, meta, ev, req)
        rec = registry._records.get(id(ev))
        # 轮次正常完成后记录应已释放；注入文本从 Provider 侧取证。
        injected = [t for t in provider.sent_texts
                    if isinstance(t, str) and t.startswith(body[:8])]
        source_text = injected[0] if injected else ""
        released = id(ev) not in registry._records
        if expect_full:
            # 完整主体 + 14 字令牌，恰好占满上限。
            ok = (len(injected) == 1 and len(source_text) == limit
                  and source_text.startswith(body) and released)
        else:
            # 总长不得超上限（旧 abdba20 在此为 主体+14 > 上限）。
            ok = (len(source_text) <= limit
                  and all(len(t) <= limit for t in injected) and released)
        check(f"{label}：注入与送达总长≤上限" if not expect_full
              else f"{label}：完整主体+令牌恰好占满上限", ok,
              f"limit={limit}, injected_lens={[len(t) for t in injected]}, "
              f"released={released}")
    obj._config["max_inject_chars"] = 600


async def x_small_budget_and_normal(obj, meta, module):
    """X3 小预算不注入；X4 正常对照注入成功。"""
    obj._config["max_inject_chars"] = 10
    uid = "81003"
    ident, bot = seed(obj, module, uid)
    ev = event(uid)
    ev.plugins_name = [meta.name]
    req = make_request("ordinary small-budget question")
    provider, registry = await run_turn(obj, meta, ev, req)
    injected = [t for t in provider.sent_texts if "偏好" in t or "【" in t]
    check("X3 小预算：不注入（无注册、Provider 无偏好块）",
          len(injected) == 0 and len(registry._records) == 0,
          f"injected={len(injected)}, registry={len(registry._records)}")

    obj._config["max_inject_chars"] = 600
    uid = "81004"
    ident, bot = seed(obj, module, uid)
    ev = event(uid)
    ev.plugins_name = [meta.name]
    req = make_request("ordinary normal question")
    provider, registry = await run_turn(obj, meta, ev, req)
    injected = [t for t in provider.sent_texts if t.startswith("【")]
    check("X4 正常对照：默认 600 注入成功、总长≤上限、正常完成已释放",
          len(injected) == 1 and 0 < len(injected[0]) <= 600
          and id(ev) not in registry._records,
          f"injected_lens={[len(t) for t in injected]}, "
          f"released={id(ev) not in registry._records}")


async def x_chain_facts():
    """X5：extra part → 运行时消息的转换链可观测面（宿主归属信道证据）。"""
    from astrbot.core.provider.entities import ProviderRequest

    own = TextPart(text="X5_PROBE_OWN_BODY").mark_as_temp()
    own._probe_meta = "X5_OWN_MARK"  # 模拟任何 Part 级私有凭据
    copy_part = TextPart(text=own.text).mark_as_temp()  # 合法复制副本
    req = ProviderRequest(prompt="chain probe")
    req.extra_user_content_parts.extend([own, copy_part])

    ctx = await req.assemble_context()
    msg = Message.model_validate(ctx)

    from astrbot.core.agent.message import TextPart as TP
    fields_ok = sorted(TP.model_fields.keys()) == ["text", "type"]
    blocks = [p for p in msg.content
              if isinstance(getattr(p, "text", None), str)
              and p.text.startswith("X5_PROBE_OWN_BODY")]
    identity_ok = all(p is not own and p is not copy_part for p in msg.content)
    mark_ok = all(getattr(p, "_probe_meta", None) is None for p in blocks)
    indist = (len(blocks) == 2
              and blocks[0].text == blocks[1].text
              and all(bool(getattr(p, "_no_save", False)) for p in blocks))
    dump_ok = sorted(own.model_dump_for_context().keys()) == ["_no_save", "text", "type"]
    check("X5 转换链：TextPart 模型字段仅 type/text", fields_ok)
    check("X5 转换链：对象身份不跨重建", identity_ok)
    check("X5 转换链：私有属性不入 dump（仅宿主特判 _no_save）", mark_ok and dump_ok)
    check("X5 转换链：同文 temp 复制副本与本尊可观测不可区分", indist,
          f"blocks={len(blocks)}")


async def main_async():
    obj, meta, module, manager = await load_plugin()
    await x_chain_facts()
    await x_budget(obj, meta, module)
    await x_small_budget_and_normal(obj, meta, module)
    await obj.terminate()
    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    print("BLOCKED T5b：finalize 后/AgentBegin 期合法复制的同文 temp 副本"
          "与本尊在宿主 Part 接口下不可区分（X5 证据），保持未修，交受阻证据。")
    if FAIL:
        raise SystemExit(1)
    return obj, meta, module, manager


if __name__ == "__main__":
    print("== x_rework_check（第八轮）==")
    asyncio.run(main_async())
