"""R1–R6 返工回归 + 真实宿主入口测试（Codex 复核 0c32cee 后重做）。

六组反例源自独立复核 independent_repro.py，断言修复后的正确行为；
另含正式入口组（真实 AstrMessageEvent / CommandFilter / call_handler /
正式插件构造 / star_map 插件装卸）。脱网、合成数据、受控替身。

  RR1 群聊不再是私聊：真实事件+真实命令过滤分发，群聊只回通用引导，
      不读私人档案、不注入；私聊对照正常。
  RR2 正式入口接通共享守卫：真实 star_map 放入打补丁 uctx → protocol_ok
      → 排除标志已设、uctx 不接管；停用→no_bridge；无协议→不注入。
  RR3 管理员关系暂停被读取：真实 RelationStore.set_interaction_safety_admin
      → 快照 pause_intimacy；timed 过期/admin 与 timed 合并/global scope。
  RR4 命令与请求同一人格：真实解析算法 + 会话人格 persona_B → 两者一致；
      4.26 provider_settings 传入后默认人格可解析。
  RR5 提交前失效：受控 await 边界 clear 后不再注入；对照正常注入。
  RR6 方向有序：互换 user/bot 方向渲染不同；9 组合措辞唯一。

运行：<venv>/Scripts/python.exe tests/r_rework_check.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
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
# 隔离副本（带补丁的 uctx）必须优先于 ROOT 下的原 uctx 仓库被解析
UCTX_PARENT = ROOT / ".tmp_uctx_isolation"
if str(UCTX_PARENT) not in sys.path:
    sys.path.insert(0, str(UCTX_PARENT))

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
from astrbot.api.star import StarTools  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.pipeline.context_utils import call_handler  # noqa: E402
from astrbot.core.star.star import StarMetadata, star_map  # noqa: E402
from astrbot.core.star.star_handler import star_handlers_registry  # noqa: E402
from astrbot.core.star.filter.command import CommandFilter  # noqa: E402
from astrbot.core.star.filter.permission import PermissionTypeFilter  # noqa: E402

import astrbot_plugin_preference_profile.main as pm  # noqa: E402
from astrbot_plugin_preference_profile.pref_profile.identity import (  # noqa: E402
    build_identity,
    resolve_persona_scope,
)
from astrbot_plugin_preference_profile.pref_profile.bridge_guard import (  # noqa: E402
    EXCLUDE_EXTRA_KEY,
    BridgeGuard,
)
from astrbot_plugin_preference_profile.pref_profile.relation_snapshot import (  # noqa: E402
    RelationSnapshotReader,
)
from astrbot_plugin_preference_profile.pref_profile.policy import (  # noqa: E402
    RelationSnapshot,
    TurnInput,
    evaluate,
)
from astrbot_plugin_preference_profile.pref_profile.prompt_builder import render_decision  # noqa: E402
from astrbot_plugin_preference_profile.pref_profile.model import Entry  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="pref_rework_"))
RUN_ID = 0


def next_dir() -> Path:
    global RUN_ID
    RUN_ID += 1
    d = TMP / f"case_{RUN_ID}"
    d.mkdir(exist_ok=True)
    return d


class ControlledPersona:
    """受控人格依赖：默认返回 persona_A（R4 场景会替换为真实算法）。"""

    async def resolve_selected_persona(self, **kw):
        return ("persona_A", {}, None, False)


def real_event(group="", text="合成测试内容", mid="rework"):
    """真实宿主 AstrMessageEvent（不覆写任何接口形状）。"""

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


class ControlledConversationManager:
    """受控会话管理器：get_curr_conversation_id → get_conversation 同源。"""

    def __init__(self, persona_id=None):
        self._persona_id = persona_id

    async def get_curr_conversation_id(self, umo):
        return "cid-1" if self._persona_id is not None else None

    async def get_conversation(self, umo, cid):
        return SimpleNamespace(persona_id=self._persona_id, cid=cid)


def create_plugin(persona=None, config=None, conversation_manager=None):
    """正式插件构造（真实 __init__；仅隔离数据目录与外部依赖）。"""

    root = next_dir()
    context = SimpleNamespace(
        persona_manager=persona or ControlledPersona(),
        # 模拟宿主配置：4.26 的默认人格解析需要 provider_settings
        get_config=lambda: {
            "data": str(root / "data"),
            "provider_settings": {"default_personality": "persona_A"},
        },
        conversation_manager=conversation_manager,
    )
    with patch.object(StarTools, "get_data_dir", return_value=root / "profile"):
        p = pm.PreferenceProfilePlugin(
            context,
            config
            or {
                "admin_enabled": True,
                "relation_link_enabled": False,
                "max_inject_items": 6,
                "max_inject_chars": 600,
            },
        )
    ident = build_identity(
        platform_id="aiocqhttp", self_id="bot1",
        persona_scope="persona_A", sender_id="10001",
    )
    bot = build_identity(
        platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
    )
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


async def dispatch(p, handler_name: str, ev, **params):
    """真实命令过滤 + call_handler 分发到插件方法（异步主流程内使用）。"""

    md = next(
        h for h in star_handlers_registry
        if h.handler_module_path == pm.__name__ and h.handler_name == handler_name
    )
    filt = next(f for f in md.event_filters if isinstance(f, CommandFilter))
    matched = filt.filter(ev, {})
    if not matched:
        return None, False
    async for _ in call_handler(ev, getattr(p, handler_name), **params):
        pass
    result = ev.get_result()
    text = "".join(getattr(x, "text", "") for x in result.chain) if result else ""
    return text, True


def cleanup_uctx_registry():
    for h in [
        h for h in list(star_handlers_registry)
        if str(h.handler_module_path).startswith("astrbot_plugin_user_context_bridge")
    ]:
        star_handlers_registry.remove(h)
    for k in [
        k for k in list(star_map)
        if str(k).startswith("astrbot_plugin_user_context_bridge")
    ]:
        star_map.pop(k, None)


async def main() -> int:
    try:
        # ================= RR1 群聊隔离（R1） =================
        p, ident = create_plugin()
        ev_g = real_event("synthetic_group", "xp show", "rr1g")
        check("RR1a 真实事件 is_private_chat()=False（群）", ev_g.is_private_chat() is False)
        text, matched = await dispatch(p, "xp_show", ev_g, page=1)
        check(
            "RR1b 群聊命令分发只回通用引导，不泄露私人标签",
            matched and text == pm.GROUP_HINT and "合成偏好" not in text,
            f"matched={matched}, text={text[:60]!r}",
        )
        req_g = ProviderRequest(prompt="合成测试内容")
        req_g.conversation = SimpleNamespace(persona_id="persona_A")
        await p.on_llm_request(ev_g, req_g)
        check(
            "RR1c 群聊 LLM 请求零注入",
            len([x for x in req_g.extra_user_content_parts]) == 0,
            f"parts={len(req_g.extra_user_content_parts)}",
        )
        # 群聊 set/on/off/clear 同样只引导
        for name, params in (
            ("xp_set", dict(tag="玩笑", status="喜欢")),
            ("xp_on", {}),
            ("xp_off", {}),
            ("xp_clear", {}),
            ("xp_admin", dict(action="switch", sub="on")),
        ):
            ev_x = real_event("synthetic_group", "xp " + name, "rr1_" + name)
            t_x, m_x = await dispatch(p, name, ev_x, **params)
            check(
                f"RR1d 群聊 {name} 仅通用引导",
                (t_x == pm.GROUP_HINT or m_x is False),
                f"matched={m_x}, text={str(t_x)[:40]!r}",
            )
        # 私聊对照
        ev_p = real_event("", "xp show", "rr1p")
        text_p, matched_p = await dispatch(p, "xp_show", ev_p, page=1)
        check(
            "RR1e 私聊对照正常显示",
            matched_p and "合成偏好" in text_p and ev_p.is_private_chat() is True,
        )
        p._store.close()

        # ================= RR2 正式入口共享守卫（R2） =================
        import astrbot_plugin_user_context_bridge.main as uctx_main
        from astrbot_plugin_user_context_bridge.uctx_bridge.bridge import ContextBridge
        from astrbot_plugin_user_context_bridge.uctx_bridge.ledger import TurnLedger
        from astrbot_plugin_user_context_bridge.uctx_bridge.scope import (
            MembershipStore,
            ScopeConfig,
            ScopeResolver,
        )

        cleanup_uctx_registry()
        key = uctx_main.__name__
        star_map[key] = StarMetadata(
            name="astrbot_plugin_user_context_bridge",
            module=uctx_main, activated=True, version="0.2.0",
        )
        p, ident = create_plugin()
        check(
            "RR2a 正式构造守卫探测到打补丁 uctx（protocol_ok）",
            p._bridge_guard.mode == "protocol_ok",
            f"mode={p._bridge_guard.mode}",
        )
        ev = real_event(mid="rr2")
        req = ProviderRequest(prompt="合成私聊内容")
        req.conversation = SimpleNamespace(persona_id="persona_A")
        await p.on_llm_request(ev, req)
        check(
            "RR2b 排除标志已写入",
            ev.get_extra(EXCLUDE_EXTRA_KEY) is not None,
        )
        root = next_dir()
        ledger = TurnLedger(root / "ledger.db")
        ledger.open()
        bridge = ContextBridge(
            ledger=ledger,
            scope_resolver=ScopeResolver(
                ScopeConfig(enabled=True, include_private=True),
                MembershipStore(root / "membership.json"),
            ),
            persona_manager_getter=lambda: ControlledPersona(),
        )
        took = await bridge.handle_llm_request(ev, req)
        check(
            "RR2c 实际 uctx 不再接管该轮（无 pending）",
            took is False and bridge.pending_count == 0,
            f"took={took}, pending={bridge.pending_count}",
        )
        check(
            "RR2d 偏好块已注入（排除与注入并存）",
            len(req.extra_user_content_parts) == 1,
        )
        ledger.close()
        p._store.close()

        # 停用（重载/停用路径）：activated=False → no_bridge
        star_map[key].activated = False
        p2, _ = create_plugin()
        check(
            "RR2e uctx 停用后守卫视为不在场（no_bridge）",
            p2._bridge_guard.mode == "no_bridge",
            f"mode={p2._bridge_guard.mode}",
        )
        p2._store.close()
        # 无协议常量（未打补丁）→ protocol_missing → 私人注入禁用
        star_map[key].activated = True
        star_map[key].module = SimpleNamespace()  # 移除协议常量
        p3, _ = create_plugin()
        ev3 = real_event(mid="rr2c")
        req3 = ProviderRequest(prompt="x")
        req3.conversation = SimpleNamespace(persona_id="persona_A")
        await p3.on_llm_request(ev3, req3)
        check(
            "RR2f 未打补丁 uctx 在场 → protocol_missing → 不注入",
            p3._bridge_guard.mode == "protocol_missing"
            and len(req3.extra_user_content_parts) == 0,
        )
        p3._store.close()
        cleanup_uctx_registry()

        # ================= RR3 管理员关系暂停（R3） =================
        from astrbot_plugin_relation_arc.relation_store import RelationStore

        root = next_dir()
        rr_root = root / "plugin_data" / "astrbot_plugin_relation_arc"
        rs = RelationStore(rr_root)
        # 真实部署恒有的 config.json（session 模式）
        (rr_root / "config.json").write_text(
            json.dumps({"config_version": 7, "is_global_relation": False}),
            encoding="utf-8",
        )
        ev = real_event(mid="rr3")
        rs.set_interaction_safety_admin(
            "aiocqhttp:10001", "session", ev.unified_msg_origin, "pause_intimacy"
        )
        truth = rs.effective_interaction_safety(
            "aiocqhttp:10001", "session", ev.unified_msg_origin
        )
        ident3 = build_identity(
            platform_id="aiocqhttp", self_id="bot1",
            persona_scope="persona_A", sender_id="10001",
        )
        snapshot = await RelationSnapshotReader(root).load(ident3, ev.unified_msg_origin)
        check(
            "RR3a 管理员 pause_intimacy 被快照读取",
            truth == "pause_intimacy"
            and snapshot.available
            and snapshot.interaction_rhythm == "pause_intimacy",
            f"truth={truth}, snap={snapshot.interaction_rhythm}",
        )
        # admin normal + 有效 timed slow_down → 取高
        rs.set_interaction_safety_admin(
            "aiocqhttp:10001", "session", ev.unified_msg_origin, "normal"
        )
        # 合成 timed 行（宿主仅经 apply_turn_with_binding 的 llm_auto 路径
        # 写入；测试直接按其 schema 插入，source 约束 llm_auto）
        import sqlite3 as _sq
        import time as _t
        _db = root / "plugin_data" / "astrbot_plugin_relation_arc" / "relation_arc.sqlite3"
        _c = _sq.connect(_db)
        _c.execute(
            "INSERT OR REPLACE INTO timed_safety VALUES (?,?,?,?,?,?,?,?,?)",
            ("aiocqhttp:10001", "session", ev.unified_msg_origin,
             "slow_down", _t.time() + 600, 1, "llm_auto", _t.time(), _t.time()),
        )
        _c.commit(); _c.close()
        snap2 = await RelationSnapshotReader(root).load(ident3, ev.unified_msg_origin)
        check(
            "RR3b admin normal + 有效 timed slow_down → slow_down",
            snap2.interaction_rhythm == "slow_down",
            f"snap={snap2.interaction_rhythm}",
        )
        # timed 过期 → 回落 admin 值
        _c = _sq.connect(_db)
        _c.execute(
            "INSERT OR REPLACE INTO timed_safety VALUES (?,?,?,?,?,?,?,?,?)",
            ("aiocqhttp:10001", "session", ev.unified_msg_origin,
             "pause_intimacy", _t.time() - 1, 2, "llm_auto", _t.time(), _t.time()),
        )
        _c.commit(); _c.close()
        snap3 = await RelationSnapshotReader(root).load(ident3, ev.unified_msg_origin)
        check(
            "RR3c timed 过期 → 回落管理员 base（normal）",
            snap3.interaction_rhythm == "normal",
            f"snap={snap3.interaction_rhythm}",
        )
        # global scope 管理员暂停（切 global 模式 config）
        (rr_root / "config.json").write_text(
            json.dumps({"config_version": 7, "is_global_relation": True}),
            encoding="utf-8",
        )
        rs.set_interaction_safety_admin(
            "aiocqhttp:10001", "global", "", "pause_intimacy"
        )
        snap4 = await RelationSnapshotReader(root).load(ident3, ev.unified_msg_origin)
        check(
            "RR3d global scope 暂停同样生效",
            snap4.interaction_rhythm == "pause_intimacy",
            f"snap={snap4.interaction_rhythm}",
        )

        # ================= RR4 命令/请求同一人格（R4） =================
        import astrbot.core.persona_mgr as persona_mod
        from unittest.mock import AsyncMock

        manager = SimpleNamespace(
            acm=SimpleNamespace(get_conf=lambda umo: {"agent_runner": {
                "runner_type": "local", "config": {"persona": {"persona_id": "persona_A"}}
            }}),
            personas_v3=[{"name": "persona_A"}, {"name": "persona_B"}],
        )
        manager.resolve_selected_persona = (
            persona_mod.PersonaManager.resolve_selected_persona.__get__(
                manager, type(manager)
            )
        )
        p4, _ = create_plugin(
            manager,
            conversation_manager=ControlledConversationManager("persona_B"),
        )
        ev4 = real_event(mid="rr4")
        with patch.object(persona_mod.sp, "get_async", new=AsyncMock(return_value={})):
            cmd_identity = await p4._resolve_identity(ev4)
            llm_persona = await resolve_persona_scope(
                manager, ev4, SimpleNamespace(persona_id="persona_B"),
                provider_settings={"default_personality": "persona_A"},
            )
        check(
            "RR4a 会话选中 persona_B 时命令与请求同一人格",
            cmd_identity is not None
            and cmd_identity.persona_scope == llm_persona == "persona_B",
            f"cmd={getattr(cmd_identity, 'persona_scope', None)}, llm={llm_persona}",
        )
        # 无会话人格 → 走配置默认（provider_settings 供 4.26）
        p4b, _ = create_plugin(
            manager, conversation_manager=ControlledConversationManager(None)
        )
        with patch.object(persona_mod.sp, "get_async", new=AsyncMock(return_value={})):
            cmd_id2 = await p4b._resolve_identity(real_event(mid="rr4b"))
        p4b._store.close()
        check(
            "RR4b 无会话人格 → 配置默认（4.26 亦经 provider_settings 解析）",
            cmd_id2 is not None and cmd_id2.persona_scope == "persona_A",
            f"scope={getattr(cmd_id2, 'persona_scope', None)}",
        )
        p4._store.close()

        # ================= RR5 提交前失效（R5） =================
        p5, ident5 = create_plugin()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_relation(identity, ev):
            entered.set()
            await release.wait()
            return RelationSnapshot.unavailable()

        p5._config["relation_link_enabled"] = True
        p5._injector._relation_loader = delayed_relation
        ev5 = real_event(mid="rr5")
        req5 = ProviderRequest(prompt="合成测试内容")
        req5.conversation = SimpleNamespace(persona_id="persona_A")
        running = asyncio.create_task(p5.on_llm_request(ev5, req5))
        await entered.wait()
        deleted, new_epoch = p5._store.clear_user(ident5.key)
        release.set()
        await running
        enabled5, epoch5 = p5._store.get_user_state(ident5.key)
        check(
            "RR5a await 边界后 clear → 不再注入",
            (not enabled5) and len(req5.extra_user_content_parts) == 0,
            f"enabled={enabled5}, parts={len(req5.extra_user_content_parts)}",
        )
        # 对照：不 clear 则正常注入
        p6, ident6 = create_plugin()
        entered2, release2 = asyncio.Event(), asyncio.Event()

        async def delayed2(identity, ev):
            entered2.set()
            await release2.wait()
            return RelationSnapshot.unavailable()

        p6._config["relation_link_enabled"] = True
        p6._injector._relation_loader = delayed2
        ev6 = real_event(mid="rr5b")
        req6 = ProviderRequest(prompt="合成测试内容")
        req6.conversation = SimpleNamespace(persona_id="persona_A")
        running2 = asyncio.create_task(p6.on_llm_request(ev6, req6))
        await entered2.wait()
        release2.set()
        await running2
        check(
            "RR5b 对照：未失效则正常注入",
            len(req6.extra_user_content_parts) == 1,
        )
        p5._store.close()
        p6._store.close()

        # ================= RR6 有序方向（R6） =================
        def entry(kind, direction, status="like", intensity=4):
            return Entry(
                kind, "synthetic", "joke", "合成偏好", status, direction,
                intensity, "", "self_declared" if kind == "user" else "admin_template",
                1, 0.0,
            )

        a = evaluate(TurnInput(True, True, True, [entry("user", "active")], [entry("bot", "receptive")]))
        b = evaluate(TurnInput(True, True, True, [entry("user", "receptive")], [entry("bot", "active")]))
        ra, rb = render_decision(a), render_decision(b)
        check(
            "RR6a 互换方向产生不同指导",
            ra != rb and "用户主动" in ra and "你主动" in rb,
            f"a={ra!r}\nb={rb!r}",
        )
        phrases = set()
        for ud in ("active", "receptive", "both"):
            for bd in ("active", "receptive", "both"):
                d = evaluate(
                    TurnInput(True, True, True, [entry("user", ud)], [entry("bot", bd)])
                )
                phrases.add(d.items[0].direction_phrase)
        check(
            "RR6b 9 种方向组合措辞唯一",
            len(phrases) == 9,
            f"unique={len(phrases)}",
        )
    except Exception:
        traceback.print_exc()
        check("返工回归未预期异常", False, traceback.format_exc(limit=2))
    finally:
        cleanup_uctx_registry()
        shutil.rmtree(TMP, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
