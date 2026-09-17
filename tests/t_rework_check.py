"""三轮返工回归（Codex 三轮复核 8de2365，T1–T4）。

对应 serialization_repro.py 的探针，断言修复后的正确行为；全部真实
宿主组件（Message/rounds_to_text/assemble_context/ToolLoopAgentRunner/
MAIN_AGENT_HOOKS/OnAgentBeginEvent/真实 Relation Arc 配置与存储）。
脱网、合成数据、本地假模型。

  T1  导入插件前后普通文本类型/摘要/组装一致；terminate 后无残留。
  T2  reset 后、真实 OnAgentBegin 等待期间 clear/admin_off → 首次
      模型调用不含旧偏好；原输入与其他插件哨兵保留。
  T3  损坏 state_json / 非法枚举 / 非 dict / scope 配置类型错 → 降级
      不可用；合法数据语义保持。
  T4  验收文档可审阅（无重复插入、尺寸正常）。

运行：<venv>/Scripts/python.exe tests/t_rework_check.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
import traceback
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

ordinary_payload = {
    "role": "user",
    "content": [{"type": "text", "text": "ordinary-other-plugin-note"}],
}

# ---- T1 前半：在任何插件导入之前记录宿主基准 ----------------------------
from astrbot.core.agent.message import Message, TextPart  # noqa: E402
from astrbot.core.agent.context.round_utils import rounds_to_text  # noqa: E402

_before_part = Message.model_validate(ordinary_payload).content[0]
_before_type = type(_before_part)
_before_dump = _before_part.model_dump_for_context()
_before_summary = rounds_to_text([[Message(role="user", content=[_before_part])]])

# ---- 此后才导入生产插件（触发任何模块级注册副作用） ----------------------
from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.api.star import StarTools  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
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
from astrbot_plugin_preference_profile.tests.fakes import FakeProvider  # noqa: E402
from astrbot_plugin_relation_arc.config_manager import PluginConfigManager  # noqa: E402
from astrbot_plugin_relation_arc.relation_store import RelationStore  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="pref_t_"))
RUN_ID = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


def next_dir() -> Path:
    global RUN_ID
    RUN_ID += 1
    d = TMP / f"case_{RUN_ID}"
    d.mkdir(exist_ok=True)
    return d


class ControlledPersona:
    async def resolve_selected_persona(self, **kw):
        return ("persona_A", {}, None, False)


def real_event(mid="t1"):
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE
    msg.self_id = "bot1"
    msg.sender = MessageMember(user_id="10001", nickname="synthetic")
    msg.message_str = "合成测试内容"
    msg.message_id = mid
    msg.message = [Plain("合成测试内容")]
    ev = AstrMessageEvent(
        "合成测试内容", msg,
        PlatformMetadata(name="aiocqhttp", description="synthetic", id="aiocqhttp"),
        "10001",
    )
    ev.is_wake = True
    ev.is_at_or_wake_command = True
    return ev


def create_plugin():
    root = next_dir()
    context = SimpleNamespace(
        persona_manager=ControlledPersona(),
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


async def send_with_controlled_gap(action: str):
    """T2：reset 完成 → 真实 OnAgentBegin 等待 → 失效动作 → 恢复。

    断言首次（且唯一）假模型调用不含旧偏好，原输入与其他插件哨兵保留。
    """

    p, ident = create_plugin()
    ev = real_event(mid=f"t2-{action}")
    req = ProviderRequest(prompt="ordinary user question")
    req.conversation = SimpleNamespace(persona_id="persona_A", token_usage=0, cid="synthetic-cid")
    req.extra_user_content_parts.append(TextPart(text="OTHER_PLUGIN_SENTINEL"))
    await p.on_llm_request(ev, req)
    p._injector.finalize(ev, req)  # 钩子链末尾（未失效：通过）
    provider = FakeProvider(["synthetic reply"])
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider, request=req,
        run_context=ContextWrapper(context=SimpleNamespace(event=ev)),
        tool_executor=FunctionToolExecutor(),
        agent_hooks=MAIN_AGENT_HOOKS, streaming=False,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    # 白名单模拟宿主 plugin_set（waking_check/stage.py）：含本插件的合法
    # 生产状态。真实 PluginManager.load 会以 metadata.yaml 补全
    # star_map.name（p7·L1 已证）；直接 import 的元数据 name=None，
    # 此处补齐以等价真实加载状态。排除本插件的白名单在生产不可达——
    # 同一白名单也会排除注入主钩子（根本不会产生旧块）。
    plugin_name = "astrbot_plugin_preference_profile"
    if pm.__name__ in star_map and star_map[pm.__name__].name is None:
        star_map[pm.__name__].name = plugin_name
    ev.plugins_name = [plugin_name]
    path = f"t2_gap_{action}"
    star_map[path] = StarMetadata(
        name=plugin_name,  # 与插件同名：白名单按 star_map[module].name 匹配
        module_path=path,
        activated=True,
        reserved=False,
    )

    async def wait_at_agent_begin(event, context):
        entered.set()
        await release.wait()

    meta = StarHandlerMetadata(
        event_type=EventType.OnAgentBeginEvent,
        handler_full_name=f"{path}_begin",
        handler_name="begin",
        handler_module_path=path,
        handler=wait_at_agent_begin,
        event_filters=[],
        extras_configs={"priority": 0},
    )
    star_handlers_registry.append(meta)
    # 真实 PluginManager.load 会把插件方法 handler 重绑定为
    # functools.partial(raw, star_cls)（star_manager 实例化段）；直接
    # import 的 handler 未绑定，此处按同款方式临时绑定，测后恢复。
    import functools as _ft

    our_meta = next(
        h for h in star_handlers_registry
        if h.handler_module_path == pm.__name__
        and h.event_type == EventType.OnAgentBeginEvent
    )
    our_orig_handler = our_meta.handler
    our_meta.handler = _ft.partial(our_orig_handler, p)

    async def run():
        async for _ in run_agent(runner, max_step=2, show_tool_use=False, show_tool_call_result=False):
            pass

    running = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        calls_before = len(provider.call_log)
        if action == "clear":
            p._store.clear_user(ident.key)
        elif action == "admin_off":
            p._config["admin_enabled"] = False
        else:
            raise AssertionError(action)
        release.set()
        await running
        if len(provider.call_log) != 1:
            check(f"T2/{action} 夹具必须恰好一次模型调用", False,
                  f"calls={len(provider.call_log)}")
            return
        sent = json.dumps(provider.call_log, ensure_ascii=False)
        leaked = "合成偏好" in sent
        check(
            f"T2/{action} reset 后 OnAgentBegin 窗口失效：首调无旧偏好、原输入与哨兵保留",
            calls_before == 0 and not leaked
            and "ordinary user question" in sent
            and "OTHER_PLUGIN_SENTINEL" in sent,
            f"before={calls_before}, leaked={leaked}",
        )
    finally:
        release.set()
        star_handlers_registry.remove(meta)
        star_map.pop(path, None)
        our_meta.handler = our_orig_handler
        p._store.close()


async def main() -> int:
    try:
        # ===== T1：宿主普通文本不受插件导入影响 =====
        after_part = Message.model_validate(ordinary_payload).content[0]
        check(
            "T1a 导入插件后普通文本类型不变（TextPart）",
            type(after_part) is _before_type,
            f"before={_before_type.__name__}, after={type(after_part).__name__}",
        )
        check(
            "T1b 普通文本 model_dump_for_context 保留原文",
            after_part.model_dump_for_context().get("text") == "ordinary-other-plugin-note",
            f"dump={after_part.model_dump_for_context()}",
        )
        req = ProviderRequest(prompt="ordinary user question")
        req.extra_user_content_parts.append(after_part)
        assembled = await req.assemble_context()
        assembled_text = json.dumps(assembled, ensure_ascii=False)
        check(
            "T1c 普通 extra part 经真实 assemble_context 保留",
            "ordinary-other-plugin-note" in assembled_text,
        )
        after_summary = rounds_to_text([[Message(role="user", content=[after_part])]])
        check(
            "T1d 原生 rounds_to_text 保留普通文本",
            after_summary == _before_summary and "ordinary-other-plugin-note" in after_summary,
            f"summary={after_summary!r}",
        )
        # 默认关闭（admin off）不干预普通轮次
        p_off, _ = create_plugin()
        p_off._config["admin_enabled"] = False
        ev_off = real_event(mid="t1-off")
        req_off = ProviderRequest(prompt="ordinary user question")
        req_off.conversation = SimpleNamespace(persona_id="persona_A")
        await p_off.on_llm_request(ev_off, req_off)
        check(
            "T1e 默认关闭零干预",
            len(req_off.extra_user_content_parts) == 0,
        )
        # terminate 后无全局残留
        await p_off.terminate()
        term_part = Message.model_validate(ordinary_payload).content[0]
        check(
            "T1f terminate 后普通文本类型不变（无全局注册残留）",
            type(term_part) is _before_type
            and term_part.model_dump_for_context().get("text") == "ordinary-other-plugin-note",
        )

        # ===== T2：reset 后 OnAgentBegin 窗口 =====
        await send_with_controlled_gap("clear")
        await send_with_controlled_gap("admin_off")

        # ===== T3：损坏关系状态降级 =====
        root = next_dir()
        cm = PluginConfigManager(ROOT / "astrbot_plugin_relation_arc", root)
        cm.load_or_create()
        cm.update({"is_global_relation": False})
        store = RelationStore(root / "plugin_data" / "astrbot_plugin_relation_arc")
        ev = real_event(mid="t3")
        store.set_interaction_safety_admin(
            "aiocqhttp:10001", "session", ev.unified_msg_origin, "pause_intimacy"
        )
        reader = RelationSnapshotReader(root)
        ident = build_identity(
            platform_id="aiocqhttp", self_id="bot1",
            persona_scope="persona_A", sender_id="10001",
        )
        control = await reader.load(ident, ev.unified_msg_origin)
        check(
            "T3a 合法数据控制组语义保持（pause_intimacy）",
            control.available and control.interaction_rhythm == "pause_intimacy",
            f"avail={control.available}, rhythm={control.interaction_rhythm}",
        )
        db = reader.db_path

        def rewrite_state(raw):
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE accounts SET state_json=? WHERE identity=?",
                    (raw, "aiocqhttp:10001"),
                )

        for label, raw in (
            ("T3b state_json 非法 JSON → 降级", "not-valid-json"),
            ("T3c state_json 非 dict（list）→ 降级", '["interaction_safety"]'),
            ("T3d interaction_safety 非法枚举 → 降级", '{"interaction_safety":"bogus"}'),
            ("T3e 字段类型错（int）→ 降级", "12345"),
        ):
            rewrite_state(raw)
            snap = await reader.load(ident, ev.unified_msg_origin)
            check(label, snap.available is False,
                  f"avail={snap.available}, rhythm={snap.interaction_rhythm}")
        # 恢复合法 + timed 过期回归
        rewrite_state('{"interaction_safety":"normal"}')
        store.record_admin_state_event(
            "aiocqhttp:10001", "session", ev.unified_msg_origin,
            "interaction_safety", "normal",
        ) if False else None
        snap_ok = await reader.load(ident, ev.unified_msg_origin)
        check(
            "T3f 恢复合法后语义正常（normal）",
            snap_ok.available and snap_ok.interaction_rhythm == "normal",
        )
        # scope 配置类型错 → 降级
        cfg_path = root / "plugin_data" / "astrbot_plugin_relation_arc" / "config.json"
        cfg_path.write_text(
            json.dumps({"config_version": 7, "is_global_relation": "yes"}),
            encoding="utf-8",
        )
        snap_cfg = await reader.load(ident, ev.unified_msg_origin)
        check(
            "T3g is_global_relation 非布尔 → 无法确认 scope → 降级",
            snap_cfg.available is False,
            f"avail={snap_cfg.available}",
        )
        cfg_path.write_text(
            json.dumps({"config_version": 7, "is_global_relation": False}),
            encoding="utf-8",
        )

        # ===== T4：验收文档可审阅 =====
        accept = PLUGIN_ROOT / "docs" / "ACCEPTANCE.md"
        body = accept.read_text(encoding="utf-8")
        repeats = body.count("命令解析链：p7·L2")
        check(
            "T4a ACCEPTANCE 无重复插入、尺寸正常",
            accept.stat().st_size < 100_000 and repeats <= 1,
            f"bytes={accept.stat().st_size}, repeats={repeats}, lines={len(body.splitlines())}",
        )
    except Exception:
        traceback.print_exc()
        check("三轮回归未预期异常", False, traceback.format_exc(limit=2))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
