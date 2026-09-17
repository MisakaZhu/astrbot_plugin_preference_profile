"""P5 关系只读联动 + uctx 排除协议组合验证（MIS-151）。

真实组件：本插件注入器/决策引擎 + 隔离副本中的 uctx TurnLedger /
ScopeResolver / ContextBridge（打补丁后）+ 真实 call_event_hook 链。
脱网合成数据；Relation Arc 使用按其 schema 构造的合成 SQLite。

Part A：Relation Arc 只读快照（RA1-RA8，V13 部分）
Part B：uctx 排除协议组合（B1-B6，V12/V14/V15/V16 部分）

运行：<venv>/Scripts/python.exe tests/p5_integration_check.py
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
sys.path.insert(0, str(PLUGIN_ROOT))
UCTX_ISOLATION = PLUGIN_ROOT.parent / ".tmp_uctx_isolation" / "astrbot_plugin_user_context_bridge"
# uctx 为 namespace package：父目录入 path 末尾（本仓库 tests/ 优先不冲突）
UCTX_PARENT = PLUGIN_ROOT.parent / ".tmp_uctx_isolation"
if str(UCTX_PARENT) not in sys.path:
    sys.path.append(str(UCTX_PARENT))

from tests.fakes import FakeEvent, fake_conversation  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook  # noqa: E402
from astrbot.core.star.star import StarMetadata, star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)

from pref_profile import PROTOCOL_TURN_EXCLUSION  # noqa: E402
from pref_profile.bridge_guard import EXCLUDE_EXTRA_KEY, BridgeGuard  # noqa: E402
from pref_profile.identity import build_identity  # noqa: E402
from pref_profile.injection import PreferenceInjector  # noqa: E402
from pref_profile.policy import RHYTHM_PAUSE  # noqa: E402
from pref_profile.relation_snapshot import RelationSnapshotReader  # noqa: E402
from pref_profile.store import PrefStore  # noqa: E402

IDENTITY = build_identity(
    platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A", sender_id="10001",
)
BOT_KEY = build_identity(
    platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
).key


class FakePersonaManager:
    async def resolve_selected_persona(self, **kw):
        return ("persona_A", {"prompt": "人格"}, None, False)


def make_arc_db(root: Path, *, with_account=True, paused=0, timed=None):
    d = root / "plugin_data" / "astrbot_plugin_relation_arc"
    d.mkdir(parents=True, exist_ok=True)
    db = d / "relation_arc.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (identity TEXT NOT NULL, scope_kind TEXT NOT NULL,
            scope_id TEXT NOT NULL DEFAULT '', values_json TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, state_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(identity,scope_kind,scope_id));
        CREATE TABLE IF NOT EXISTS timed_safety (identity TEXT NOT NULL, scope_kind TEXT NOT NULL
            CHECK(scope_kind IN ('global','session')), scope_id TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL CHECK(level IN ('slow_down','pause_intimacy')), expires_at REAL NOT NULL,
            generation INTEGER NOT NULL, source TEXT NOT NULL CHECK(source='llm_auto'),
            created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(identity,scope_kind,scope_id));
        """
    )
    if with_account:
        conn.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)",
            ("aiocqhttp:10001", "session", "aiocqhttp:FriendMessage:10001",
             "{}", paused, 1, 0.0, '{"interaction_safety":"normal","mood":"playful"}'),
        )
    if timed is not None:
        level, expires = timed
        conn.execute(
            "INSERT INTO timed_safety VALUES (?,?,?,?,?,?,?,?,?)",
            ("aiocqhttp:10001", "session", "aiocqhttp:FriendMessage:10001",
             level, expires, 1, "llm_auto", 0.0, 0.0),
        )
    conn.commit()
    conn.close()
    return db


def make_injector(store, config, bridge, relation_loader=None):
    return PreferenceInjector(
        store, config, lambda: FakePersonaManager(), bridge, relation_loader
    )


def seed_prefs(store: PrefStore):
    store.set_user_enabled(IDENTITY.key, True)
    store.upsert_entry(
        owner_kind="user", identity_key=IDENTITY.key, tag_id="closeness",
        tag_display="亲密度表达", status="like", intensity=4, source="self_declared",
    )
    store.upsert_entry(
        owner_kind="bot", identity_key=BOT_KEY, tag_id="closeness",
        tag_display="亲密度表达", status="like", intensity=4, source="admin_template",
    )


def register_handler(full_name, handler, priority):
    module_path = "tests.p5_integration_check"
    star_map[module_path] = StarMetadata(
        name="p5_probe", activated=True, module_path=module_path, reserved=False
    )
    meta = StarHandlerMetadata(
        event_type=EventType.OnLLMRequestEvent,
        handler_full_name=f"{module_path}_{full_name}",
        handler_name=full_name,
        handler_module_path=module_path,
        handler=handler,
        event_filters=[],
        extras_configs={"priority": priority},
    )
    star_handlers_registry.append(meta)
    return meta


def cleanup(metas):
    for meta in metas:
        star_handlers_registry.remove(meta)
    star_map.pop("tests.p5_integration_check", None)


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pref_p5_"))
    try:
        # ================= Part A：Relation 只读快照 =====================
        root_a = tmp / "arc_root"
        umo = "aiocqhttp:FriendMessage:10001"

        reader = RelationSnapshotReader(root_a)
        snap = await reader.load(IDENTITY, umo)
        check("RA1 缺库 → 不可用（且不创建文件）", snap.available is False and not reader.db_path.exists())

        make_arc_db(root_a)
        snap = await reader.load(IDENTITY, umo)
        check(
            "RA2 正常账户 → 可用、正常节奏、键名脱敏",
            snap.available and snap.interaction_rhythm == "normal"
            and "interaction_safety" in snap.state_labels and "playful" not in snap.state_labels,
        )

        root_b = tmp / "arc_pause"
        make_arc_db(root_b, timed=("pause_intimacy", 9e18))
        snap = await RelationSnapshotReader(root_b).load(IDENTITY, umo)
        check("RA3 timed pause_intimacy 有效 → 暂停", snap.available and snap.interaction_rhythm == RHYTHM_PAUSE)

        root_c = tmp / "arc_expired"
        make_arc_db(root_c, timed=("pause_intimacy", 1.0))  # 已过期
        snap = await RelationSnapshotReader(root_c).load(IDENTITY, umo)
        check("RA4 timed 过期 → 正常节奏", snap.available and snap.interaction_rhythm == "normal")

        root_d = tmp / "arc_paused_acct"
        make_arc_db(root_d, paused=1)
        snap = await RelationSnapshotReader(root_d).load(IDENTITY, umo)
        check("RA5 账户 paused → 暂停", snap.available and snap.paused and snap.interaction_rhythm == RHYTHM_PAUSE)

        root_e = tmp / "arc_noacct"
        make_arc_db(root_e, with_account=False)
        snap = await RelationSnapshotReader(root_e).load(IDENTITY, umo)
        check("RA6 无该身份账户 → 不可用（保守）", snap.available is False)

        # 只读验证：读取后库文件未变（mtime 与字节）
        db_a = RelationSnapshotReader(root_a)
        before = (reader.db_path if False else db_a.db_path).read_bytes()
        await db_a.load(IDENTITY, umo)
        check("RA7 读取不修改其库", before == db_a.db_path.read_bytes())

        # 注入器集成：暂停压制正向（V06 联动）
        store_a = PrefStore(tmp / "ia.db"); seed_prefs(store_a)
        async def loader_pause(ident, ev):
            return await RelationSnapshotReader(root_b).load(ident, ev.unified_msg_origin)

        bg = BridgeGuard(star_map={})
        inj = make_injector(store_a, {"admin_enabled": True}, bg, loader_pause)
        ev = FakeEvent(sender_id="10001", self_id="bot1", message_id="ia1")
        req = ProviderRequest(prompt="聊聊天"); req.conversation = fake_conversation()
        await inj.handle(ev, req)
        text = req.extra_user_content_parts[0].text if req.extra_user_content_parts else ""
        check(
            "RA8 关系暂停压制正向指导并附节奏提示",
            "互动边界参考" in text and "亲密度表达：仅在对方自然提起" not in text
            and "暂停" in text,
            f"text={text[:160]!r}",
        )
        store_a.close()

        # ================= Part B：uctx 补丁组合 ==========================
        from astrbot_plugin_user_context_bridge.uctx_bridge.bridge import ContextBridge
        from astrbot_plugin_user_context_bridge.uctx_bridge.ledger import TurnLedger
        from astrbot_plugin_user_context_bridge.uctx_bridge.scope import (
            MembershipStore,
            ScopeConfig,
            ScopeResolver,
        )

        # 补丁常量（隔离副本 main 模块属性）。import main 会触发其装饰器
        # 向全局 registry 注册插件钩子——清理之，仅保留本测试显式注册的
        # handler（bridge 实例方法）。
        import astrbot_plugin_user_context_bridge.main as uctx_main_mod

        _stray = [
            h for h in list(star_handlers_registry)
            if str(h.handler_module_path).startswith("astrbot_plugin_user_context_bridge")
        ]
        for h in _stray:
            star_handlers_registry.remove(h)
        for k in [k for k in list(star_map) if str(k).startswith("astrbot_plugin_user_context_bridge")]:
            star_map.pop(k, None)

        check(
            "B0a 补丁暴露协议常量",
            uctx_main_mod.UCTX_EXCLUDE_PROTOCOL == PROTOCOL_TURN_EXCLUSION,
        )

        ledger_dir = tmp / "uctx"
        ledger_dir.mkdir()
        ledger = TurnLedger(ledger_dir / "uctx_ledger.db")
        ledger.open()
        scope_cfg = ScopeConfig(enabled=True, include_private=True)
        resolver = ScopeResolver(scope_cfg, MembershipStore(ledger_dir / "membership.json"))
        bridge_uctx = ContextBridge(
            ledger=ledger,
            scope_resolver=resolver,
            persona_manager_getter=lambda: FakePersonaManager(),
        )

        # 探测（模拟宿主 star_map 含打补丁的 uctx）
        module_ns = SimpleNamespace(
            UCTX_EXCLUDE_PROTOCOL=uctx_main_mod.UCTX_EXCLUDE_PROTOCOL
        )
        uctx_starmap = {
            "iso.uctx": StarMetadata(
                name="astrbot_plugin_user_context_bridge",
                activated=True,
                module=module_ns,
                version="0.2.0",
            )
        }
        guard = BridgeGuard(star_map=uctx_starmap)
        check("B0b 探测 protocol_ok", guard.mode == "protocol_ok")

        store_b = PrefStore(tmp / "ib.db"); seed_prefs(store_b)
        inj_b = make_injector(store_b, {"admin_enabled": True}, guard)

        # B1 偏好启用私聊轮次：注入器先跑（真实 call_event_hook 链）
        metas = []
        try:
            metas.append(register_handler("uctx_captured", bridge_uctx.handle_llm_request, 0))
            metas.append(register_handler("pref_inject", inj_b.handle, 20))

            ev1 = FakeEvent(sender_id="10001", self_id="bot1", message_id="ub1", message_str="今晚聊什么")
            req1 = ProviderRequest(prompt="今晚聊什么")
            req1.conversation = fake_conversation(cid="u1")
            conv_ref = req1.conversation
            await call_event_hook(ev1, EventType.OnLLMRequestEvent, req1)
            check(
                "B1a 偏好启用私聊轮次：偏好块已注入",
                any(getattr(p, "text", "").startswith("【互动边界参考") for p in req1.extra_user_content_parts),
            )
            check(
                "B1b uctx 未接管（conversation 保持、contexts 未替换）",
                req1.conversation is conv_ref and req1.contexts == [],
            )
            check("B1c 无 PendingTurn（后续钩子自然短路）", bridge_uctx.pending_count == 0)
            # 终态化路径对无 pending 是 no-op
            await bridge_uctx.handle_decorating_result(ev1)
            ident_key = await _identity_key_async(bridge_uctx, ev1)
            check(
                "B1d 账本无该轮记录",
                ledger.load_history(ident_key or "x", max_turns=10) == [],
            )

            # B2 正常共享轮次（偏好未启用用户）→ uctx 接管 + 提交
            ev2 = FakeEvent(sender_id="10001", self_id="bot1", message_id="ub2", message_str="帮我记一下这件事")
            req2 = ProviderRequest(prompt="帮我记一下这件事")
            req2.conversation = fake_conversation(cid="u2")
            took = await bridge_uctx.handle_llm_request(ev2, req2)
            check("B2a 普通轮次 uctx 正常接管", took is True)
            check("B2b 接管后 conversation=None（短路宿主写回）", req2.conversation is None)
            check("B2c pending 存在", bridge_uctx.pending_count == 1)
            # agent_done + decorating_result 终态化
            run_ctx = SimpleNamespace(messages=[])
            from astrbot.core.agent.message import Message, TextPart
            run_ctx.messages = [
                Message(role="system", content=[TextPart(text="人格")]),
                Message(role="user", content=[TextPart(text="帮我记一下这件事")]),
                Message(role="assistant", content=[TextPart(text="好的，记下了。")]),
            ]
            from astrbot.core.provider.entities import LLMResponse
            resp = LLMResponse(role="assistant", completion_text="好的，记下了。")
            await bridge_uctx.handle_agent_done(ev2, run_ctx, resp)
            await bridge_uctx.handle_decorating_result(ev2)
            check(
                "B2d 正常轮次提交完成",
                bridge_uctx.pending_count == 0
                and bridge_uctx.stats.committed_completed == 1,
                f"stats={bridge_uctx.stats.__dict__}",
            )

            # B3 私聊→群聊隔离：同人随后（任意 scope）读不到偏好轮次，但能看到普通轮次
            ident_key = await _identity_key_async(bridge_uctx, ev1)
            history = ledger.load_history(ident_key, max_turns=10)
            hist_text = str(history)
            check(
                "B3a 偏好私聊轮次未入共享账本",
                "今晚聊什么" not in hist_text and "互动边界参考" not in hist_text,
                f"hist={hist_text[:200]!r}",
            )
            check(
                "B3b 普通轮次已入共享账本（共享功能不受影响）",
                "帮我记一下这件事" in hist_text,
            )

            # B4 关闭偏好后：不再排除，uctx 恢复接管
            store_b.set_user_enabled(IDENTITY.key, False)
            ev3 = FakeEvent(sender_id="10001", self_id="bot1", message_id="ub3", message_str="恢复共享")
            req3 = ProviderRequest(prompt="恢复共享")
            req3.conversation = fake_conversation(cid="u3")
            await call_event_hook(ev3, EventType.OnLLMRequestEvent, req3)
            check(
                "B4 off 后排除解除、uctx 恢复接管、无偏好注入",
                req3.conversation is None
                and not any(getattr(p, "text", "").startswith("【互动边界参考") for p in req3.extra_user_content_parts),
            )
            # 终态化，避免身份锁悬挂
            rc3 = SimpleNamespace(messages=[
                Message(role="user", content=[TextPart(text="恢复共享")]),
                Message(role="assistant", content=[TextPart(text="好")]),
            ])
            await bridge_uctx.handle_agent_done(
                ev3, rc3, LLMResponse(role="assistant", completion_text="好")
            )
            await bridge_uctx.handle_decorating_result(ev3)

            # B5 重试链：同事件重复钩子仍不双注且不误接管（先恢复启用，
            # B4 的 off 已使该用户失效）
            store_b.set_user_enabled(IDENTITY.key, True)
            ev1b = FakeEvent(sender_id="10001", self_id="bot1", message_id="ub1", message_str="今晚聊什么")
            req1b = ProviderRequest(prompt="今晚聊什么")
            req1b.conversation = fake_conversation(cid="u1b")
            await call_event_hook(ev1b, EventType.OnLLMRequestEvent, req1b)
            await call_event_hook(ev1b, EventType.OnLLMRequestEvent, req1b)
            pref_cnt = sum(
                1 for p in req1b.extra_user_content_parts
                if getattr(p, "text", "").startswith("【互动边界参考")
            )
            check("B5 重试不双注且持续排除", pref_cnt == 1 and req1b.conversation is not None)
        finally:
            cleanup(metas)

        # B6 未打补丁的 uctx（无常量）→ protocol_missing 保守（P4 已测，此处组合复核）
        unpatched_map = {
            "iso.uctx2": StarMetadata(
                name="astrbot_plugin_user_context_bridge",
                activated=True,
                module=SimpleNamespace(),  # 无 UCTX_EXCLUDE_PROTOCOL
                version="0.2.0",
            )
        }
        guard2 = BridgeGuard(star_map=unpatched_map)
        store_c = PrefStore(tmp / "ic.db"); seed_prefs(store_c)
        inj_c = make_injector(store_c, {"admin_enabled": True}, guard2)
        ev6 = FakeEvent(sender_id="10001", self_id="bot1", message_id="ub6")
        req6 = ProviderRequest(prompt="x"); req6.conversation = fake_conversation()
        await inj_c.handle(ev6, req6)
        check(
            "B6 未打补丁 uctx 在场 → 私人注入禁用（V16）",
            len(req6.extra_user_content_parts) == 0 and ev6.get_extra(EXCLUDE_EXTRA_KEY) is None,
        )
        store_b.close(); store_c.close(); ledger.close()
    except Exception:
        traceback.print_exc()
        check("P5 未预期异常", False, traceback.format_exc(limit=2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


async def _identity_key_async(bridge, event):
    """从 uctx scope 逻辑复算身份键（测试辅助，读侧不写）。"""

    from astrbot_plugin_user_context_bridge.uctx_bridge.identity import (
        resolve_persona_scope,
    )

    scope = await resolve_persona_scope(FakePersonaManager(), event, None)
    decision = bridge._scope.evaluate(event, scope)
    return decision.identity.key if decision.identity else None


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
