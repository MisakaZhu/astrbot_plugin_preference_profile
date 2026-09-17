"""第三轮返工回归（Codex 二轮复核 0491cc9 剩余七场景，R3/R4/R5）。

对应 remaining_repro.py 的七个用例，断言修复后的正确行为；全部走
正式插件构造、真实人格算法、真实 Context.get_config 绑定、真实
RelationStore、真实钩子调度与 Runner。脱网、合成数据。

  Q1 4.26 默认人格：生产 on_llm_request 真正取到 UMO 作用域
     provider_settings → 注入正常（对照 4.28）。
  Q2 UMO 会话作用域配置同时约束管理命令（scoped persona_B 时命令=B）。
  Q3 会话读取失败 → 拒绝（identity=None），不落默认人格。
  Q4 未启用 scope 的旧记录不混入（真实 _scope 语义 + config.json）。
  Q5 未知 schema（user_version=999）→ 降级不可用。
  Q6 loader 等待中管理员总开关关闭 → 不再追加。
  Q7 append 后、首次模型调用前 clear → 收尾钩子移除本插件块，
     其他插件块保留，假模型未收到已删除档案。

运行：<venv>/Scripts/python.exe tests/r3_rework_check.py
"""

from __future__ import annotations

import asyncio
import inspect
import json
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
UCTX_PARENT = ROOT / ".tmp_uctx_isolation"
if str(UCTX_PARENT) not in sys.path:
    sys.path.insert(0, str(UCTX_PARENT))

from tests.fakes import FakeProvider  # noqa: E402

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
from astrbot.api.star import Context, StarTools  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.persona_mgr import PersonaManager  # noqa: E402
import astrbot.core.persona_mgr as persona_mod  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook  # noqa: E402
from astrbot.core.star.star import StarMetadata, star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
)
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.astr_agent_hooks import MAIN_AGENT_HOOKS  # noqa: E402
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor  # noqa: E402
from astrbot.core.astr_agent_run_util import run_agent  # noqa: E402

import astrbot_plugin_preference_profile.main as pm  # noqa: E402
from astrbot_plugin_preference_profile.pref_profile.identity import build_identity  # noqa: E402
from astrbot_plugin_preference_profile.pref_profile.relation_snapshot import (  # noqa: E402
    RelationSnapshotReader,
)
from astrbot_plugin_relation_arc.relation_store import RelationStore  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="pref_r3_"))
RUN_ID = 0


def next_dir() -> Path:
    global RUN_ID
    RUN_ID += 1
    d = TMP / f"case_{RUN_ID}"
    d.mkdir(exist_ok=True)
    return d


class ControlledPersona4:
    async def resolve_selected_persona(self, **kw):
        return ("persona_A", {}, None, False)


def real_event(group="", text="合成测试内容", mid="r3"):
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
    def __init__(self, persona=None, fail=False):
        self.persona = persona
        self.fail = fail

    async def get_curr_conversation_id(self, umo):
        return "synthetic-cid"

    async def get_conversation(self, umo, cid):
        if self.fail:
            raise OSError("synthetic conversation storage unavailable")
        return SimpleNamespace(persona_id=self.persona, cid=cid)


def cfg(name):
    return {
        "provider_settings": {"default_personality": name},
        "agent_runner": {"runner_type": "local", "config": {"persona": {"persona_id": name}}},
    }


def create_plugin(persona=None):
    root = next_dir()
    context = SimpleNamespace(
        persona_manager=persona or ControlledPersona4(),
        get_config=lambda: {"data": str(root / "data")},
        conversation_manager=None,
    )
    with patch.object(StarTools, "get_data_dir", return_value=root / "profile"):
        p = pm.PreferenceProfilePlugin(
            context,
            {"admin_enabled": True, "relation_link_enabled": False,
             "max_inject_items": 6, "max_inject_chars": 600},
        )
    ident = build_identity(
        platform_id="aiocqhttp", self_id="bot1",
        persona_scope="persona_A", sender_id="10001",
    )
    bot = build_identity(platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A")
    p._store.set_user_enabled(ident.key, True)
    for kind, identity, source in (
        ("user", ident, "self_declared"),
        ("bot", bot, "admin_template"),
    ):
        p._store.upsert_entry(
            owner_kind=kind, identity_key=identity.key, tag_id="joke",
            tag_display="合成偏好", status="like", intensity=4, source=source,
        )
    return p, ident


def configure(p, conversation=None, scoped="persona_A", fail=False):
    """绑定真实人格算法与真实 Context.get_config 语义（照 Codex 复现）。"""

    mgr = SimpleNamespace(
        acm=SimpleNamespace(get_conf=lambda umo: cfg(scoped)),
        personas_v3=[{"name": "persona_A"}, {"name": "persona_B"}],
    )
    mgr.resolve_selected_persona = PersonaManager.resolve_selected_persona.__get__(
        mgr, type(mgr)
    )
    p.context.persona_manager = mgr
    p.context.conversation_manager = Conversations(conversation, fail)
    p.context._config = cfg("persona_A")
    p.context.astrbot_config_mgr = SimpleNamespace(get_conf=lambda umo: cfg(scoped))
    p.context.get_config = Context.get_config.__get__(p.context, type(p.context))
    return mgr


async def host_persona(p, event, conversation):
    kw = dict(
        umo=event.unified_msg_origin,
        conversation_persona_id=conversation.persona_id,
        platform_name=event.get_platform_name(),
    )
    if "provider_settings" in inspect.signature(
        p.context.persona_manager.resolve_selected_persona
    ).parameters:
        kw["provider_settings"] = p.context.get_config(
            umo=event.unified_msg_origin
        )["provider_settings"]
    return (await p.context.persona_manager.resolve_selected_persona(**kw))[0]


async def main() -> int:
    try:
        # ===== Q1 4.26 默认人格：生产注入入口真正取到 provider_settings =====
        p, ident = create_plugin()
        configure(p)  # 会话 persona_id=None，默认 persona_A
        event = real_event(mid="default-persona")
        req = ProviderRequest(prompt="synthetic ordinary question")
        req.conversation = SimpleNamespace(persona_id=None)
        with patch.object(persona_mod.sp, "get_async", new=AsyncMock(return_value={})):
            command = await p._resolve_identity(event)
            truth = await host_persona(p, event, req.conversation)
            await p.on_llm_request(event, req)
        check(
            "Q1 默认人格下生产注入器正常注入（4.26 经 provider_settings）",
            truth == "persona_A"
            and getattr(command, "persona_scope", None) == "persona_A"
            and len(req.extra_user_content_parts) == 1,
            f"truth={truth}, cmd={getattr(command, 'persona_scope', None)}, "
            f"parts={len(req.extra_user_content_parts)}",
        )
        p._store.close()

        # ===== Q2 UMO 会话作用域配置约束管理命令 =====
        p, _ = create_plugin()
        configure(p, scoped="persona_B")
        event = real_event(mid="scoped-config")
        with patch.object(persona_mod.sp, "get_async", new=AsyncMock(return_value={})):
            command = await p._resolve_identity(event)
            truth = await host_persona(p, event, SimpleNamespace(persona_id=None))
        check(
            "Q2 会话作用域配置（persona_B）同时约束命令",
            getattr(command, "persona_scope", None) == truth == "persona_B",
            f"cmd={getattr(command, 'persona_scope', None)}, truth={truth}",
        )
        p._store.close()

        # ===== Q3 会话读取失败 → 拒绝 =====
        p, _ = create_plugin()
        configure(p, conversation="persona_B", fail=True)
        with patch.object(persona_mod.sp, "get_async", new=AsyncMock(return_value={})):
            command = await p._resolve_identity(real_event(mid="lookup-failure"))
        check(
            "Q3 会话读取失败拒绝私人档案操作（不落默认人格）",
            command is None,
            f"cmd={getattr(command, 'persona_scope', None)}",
        )
        p._store.close()

        # ===== Q4 未启用 scope 不混入（真实 _scope 语义 + config.json） =====
        root = next_dir()
        arc_dir = root / "plugin_data" / "astrbot_plugin_relation_arc"
        arc_dir.mkdir(parents=True, exist_ok=True)
        store = RelationStore(arc_dir)
        event = real_event(mid="scope-selection")
        user = "aiocqhttp:10001"
        store.set_interaction_safety_admin(user, "session", event.unified_msg_origin, "normal")
        store.set_interaction_safety_admin(user, "global", "", "pause_intimacy")
        # 真实 config（session 模式）：is_global_relation=False
        (arc_dir / "config.json").write_text(
            json.dumps({"config_version": 7, "is_global_relation": False}),
            encoding="utf-8",
        )
        ident4 = build_identity(
            platform_id="aiocqhttp", self_id="bot1",
            persona_scope="persona_A", sender_id="10001",
        )
        reader = RelationSnapshotReader(root)
        snap = await reader.load(ident4, event.unified_msg_origin)
        check(
            "Q4 未启用 scope（global 旧 pause）不混入 session 生效状态",
            snap.available and snap.interaction_rhythm == "normal",
            f"avail={snap.available}, rhythm={snap.interaction_rhythm}",
        )
        # 对照：global 模式读取 global
        (arc_dir / "config.json").write_text(
            json.dumps({"config_version": 7, "is_global_relation": True}),
            encoding="utf-8",
        )
        snap_g = await reader.load(ident4, event.unified_msg_origin)
        check(
            "Q4b global 模式读取 global 生效状态（pause）",
            snap_g.available and snap_g.interaction_rhythm == "pause_intimacy",
            f"rhythm={snap_g.interaction_rhythm}",
        )

        # Q4c（corrected_repro R3 等价场景 + 真实部署必有的 config.json）：
        # session 模式 + 管理员 pause 写在 session → 快照读取 pause
        (arc_dir / "config.json").write_text(
            json.dumps({"config_version": 7, "is_global_relation": False}),
            encoding="utf-8",
        )
        store.set_interaction_safety_admin(
            user, "session", event.unified_msg_origin, "pause_intimacy"
        )
        snap_pause = await reader.load(ident4, event.unified_msg_origin)
        check(
            "Q4c session 管理员暂停（带 config）被正确读取",
            snap_pause.available and snap_pause.interaction_rhythm == "pause_intimacy",
            f"avail={snap_pause.available}, rhythm={snap_pause.interaction_rhythm}",
        )
        store.set_interaction_safety_admin(
            user, "session", event.unified_msg_origin, "normal"
        )

        # ===== Q5 未知 schema → 降级 =====
        import sqlite3 as _sq

        with _sq.connect(reader.db_path) as conn:
            conn.execute("PRAGMA user_version=999")
        snap999 = await reader.load(ident4, event.unified_msg_origin)
        check(
            "Q5 未知 schema（user_version=999）按合同降级不可用",
            snap999.available is False,
            f"avail={snap999.available}",
        )
        with _sq.connect(reader.db_path) as conn:
            conn.execute("PRAGMA user_version=12")
        # config 无法确认（删 config.json）也降级
        (arc_dir / "config.json").unlink()
        snap_nc = await reader.load(ident4, event.unified_msg_origin)
        check(
            "Q5b 配置无法确认（缺 config.json）降级不可用",
            snap_nc.available is False,
            f"avail={snap_nc.available}",
        )

        # ===== Q6 loader 等待中管理员总开关关闭 =====
        p, ident = create_plugin()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed(identity, event):
            entered.set()
            await release.wait()
            from astrbot_plugin_preference_profile.pref_profile.policy import (
                RelationSnapshot,
            )

            return RelationSnapshot.unavailable()

        p._config["relation_link_enabled"] = True
        p._injector._relation_loader = delayed
        event = real_event(mid="admin-off")
        req = ProviderRequest(prompt="synthetic ordinary question")
        req.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0, cid="synthetic-cid")
        pending = asyncio.create_task(p.on_llm_request(event, req))
        await entered.wait()
        p._config["admin_enabled"] = False
        release.set()
        await pending
        check(
            "Q6 管理员总开关在 await 边界后关闭 → 不再追加",
            len(req.extra_user_content_parts) == 0,
            f"parts={len(req.extra_user_content_parts)}",
        )
        p._store.close()

        # ===== Q7 append 后、首次模型调用前 clear → 收尾钩子移除 =====
        p, ident = create_plugin()
        event = real_event(mid="late-hook")
        req = ProviderRequest(prompt="synthetic ordinary question")
        req.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0, cid="synthetic-cid")
        entered = asyncio.Event()
        release = asyncio.Event()
        other_part_sentinel = "OTHER_PLUGIN_PART_SENTINEL"

        async def later_hook(event, req):
            from astrbot.core.agent.message import TextPart

            req.extra_user_content_parts.append(TextPart(text=other_part_sentinel))
            entered.set()
            await release.wait()

        path = "r3_review_pipeline"
        event.plugins_name = [path]
        star_map[path] = StarMetadata(
            name=path, module_path=path, activated=True, reserved=False
        )
        metas = []
        for name, handler, priority in (
            ("production_preference", p.on_llm_request, 20),
            ("controlled_later_hook", later_hook, 0),
            ("production_finalize", p.finalize_request, -1000),
        ):
            meta = StarHandlerMetadata(
                event_type=EventType.OnLLMRequestEvent,
                handler_full_name=f"{path}_{name}",
                handler_name=name,
                handler_module_path=path,
                handler=handler,
                event_filters=[],
                extras_configs={"priority": priority},
            )
            star_handlers_registry.append(meta)
            metas.append(meta)
        provider = FakeProvider(["synthetic response"])
        pending = asyncio.create_task(call_event_hook(event, EventType.OnLLMRequestEvent, req))
        await asyncio.wait_for(entered.wait(), timeout=5)
        before_calls = len(provider.call_log)
        p._store.clear_user(ident.key)
        release.set()
        await pending
        for meta in metas:
            star_handlers_registry.remove(meta)
        star_map.pop(path, None)
        runner = ToolLoopAgentRunner()
        await runner.reset(
            provider=provider, request=req,
            run_context=ContextWrapper(context=SimpleNamespace(event=event)),
            tool_executor=FunctionToolExecutor(),
            agent_hooks=MAIN_AGENT_HOOKS, streaming=False,
        )
        async for _ in run_agent(runner, max_step=2, show_tool_use=False, show_tool_call_result=False):
            pass
        if len(provider.call_log) != 1:
            check("Q7 夹具必须恰好一次模型调用", False, f"calls={len(provider.call_log)}")
        else:
            sent = json.dumps(provider.call_log, ensure_ascii=False)
            leaked = "合成偏好" in sent
            remaining_texts = [
                getattr(x, "text", "") for x in req.extra_user_content_parts
            ]
            check(
                "Q7 clear 后首次模型调用不含已删除档案；其他插件块保留",
                before_calls == 0 and not leaked
                and other_part_sentinel in sent,
                f"before={before_calls}, leaked={leaked}, "
                f"remaining={remaining_texts}",
            )
        p._store.close()
    except Exception:
        traceback.print_exc()
        check("第三轮回归未预期异常", False, traceback.format_exc(limit=2))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
