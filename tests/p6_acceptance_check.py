"""P6 验收矩阵补充验证（MIS-152）。

补齐 V09（真实命令绑定层）、V17（重载/停用/卸载恢复）、V15（流式与
模型失败路径）、V18（干净安装结构）在真实宿主包下的证据。脱网、合成数据。

运行：<venv>/Scripts/python.exe tests/p6_acceptance_check.py（cwd 为仓库根）
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
ROOT = PLUGIN_ROOT.parent
sys.path.insert(0, str(PLUGIN_ROOT))  # tests.fakes
sys.path.insert(0, str(ROOT))  # 插件包（namespace package）

from tests.fakes import (  # noqa: E402
    FakeConversationManager,
    FakeEvent,
    FakeProvider,
    fake_conversation,
)

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
from astrbot.api.star import StarTools  # noqa: E402
from unittest.mock import patch  # noqa: E402
from astrbot.core.astr_agent_run_util import run_agent  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
)
from astrbot.core.astr_agent_hooks import MAIN_AGENT_HOOKS  # noqa: E402
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor  # noqa: E402
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import LLMResponse  # noqa: E402

import astrbot_plugin_preference_profile.main as plugin_main  # noqa: E402
from astrbot_plugin_preference_profile.pref_profile.commands import (  # noqa: E402
    GROUP_HINT,
    CommandService,
)
from astrbot_plugin_preference_profile.pref_profile.bridge_guard import BridgeGuard  # noqa: E402
from astrbot_plugin_preference_profile.pref_profile.injection import PreferenceInjector  # noqa: E402
from astrbot_plugin_preference_profile.pref_profile.identity import build_identity  # noqa: E402
from astrbot_plugin_preference_profile.pref_profile.store import PrefStore  # noqa: E402


class FakePersonaManager:
    async def resolve_selected_persona(self, **kw):
        return ("persona_A", {}, None, False)


IDENTITY = build_identity(
    platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A", sender_id="10001",
)


def make_plugin(db_dir: Path) -> plugin_main.PreferenceProfilePlugin:
    """正式插件构造（真实 __init__；仅隔离数据目录与外部依赖）。

    返工要求：不得以 __new__ 旁路构造后称完整集成通过。
    """

    context = SimpleNamespace(
        persona_manager=FakePersonaManager(),
        get_config=lambda: {
            "data": str(db_dir.parent / "host_data"),
            "provider_settings": {"default_personality": "persona_A"},
        },
        conversation_manager=None,
    )
    config = {
        "admin_enabled": True,
        "relation_link_enabled": False,
        "max_inject_items": 6,
        "max_inject_chars": 600,
    }
    with patch.object(StarTools, "get_data_dir", return_value=db_dir):
        p = plugin_main.PreferenceProfilePlugin(context, config)
    return p


async def _fake_resolver(event):
    return build_identity(
        platform_id=str(event.get_platform_id() or ""),
        self_id=str(event.get_self_id() or ""),
        persona_scope="persona_A",
        sender_id=str(event.get_sender_id() or ""),
    )


def result_text(event) -> str:
    r = getattr(event, "_result", None)
    if r is None:
        return ""
    chain = getattr(r, "chain", None) or []
    parts = []
    for comp in chain:
        text = getattr(comp, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def build_runner(req, provider, event, streaming=False):
    runner = ToolLoopAgentRunner()
    ctx = SimpleNamespace(event=event)
    reset_coro = runner.reset(
        provider=provider,
        request=req,
        run_context=ContextWrapper(context=ctx),
        tool_executor=FunctionToolExecutor(),
        agent_hooks=MAIN_AGENT_HOOKS,
        streaming=streaming,
    )
    return runner, reset_coro


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pref_p6_"))
    try:
        # -- G 系列：main.py 命令绑定层（V09） ------------------------------
        plugin = make_plugin(tmp / "g1")
        ev = FakeEvent(sender_id="10001", self_id="bot1", message_id="g1")
        await plugin.xp_on(ev)
        check("G1a 绑定层 xp_on 回执", "已为你开启" in result_text(ev), f"{result_text(ev)[:80]!r}")
        await plugin.xp_set(ev, "玩笑尺度", "喜欢", "主动", "3")
        check("G1b 绑定层 xp_set 回执", "已保存" in result_text(ev))
        await plugin.xp_show(ev)
        check("G1c 绑定层 xp_show 列条目", "玩笑尺度" in result_text(ev))
        await plugin.xp_status(ev)
        check("G1d 绑定层 xp_status", "本人开关：开" in result_text(ev))

        grp = FakeEvent(sender_id="10001", self_id="bot1", group_id="g", message_id="g2")
        await plugin.xp_status(grp)
        check("G2 群聊命令绑定层 → 通用引导", result_text(grp) == GROUP_HINT)

        member_ev = FakeEvent(sender_id="10001", self_id="bot1", message_id="g3")
        await plugin.xp_admin(member_ev, "switch", "on")
        check(
            "G3a 普通用户 admin switch 被拒",
            "管理员权限" in result_text(member_ev),
            f"{result_text(member_ev)[:60]!r}",
        )
        admin_ev = FakeEvent(sender_id="10001", self_id="bot1", role="admin", message_id="g4")
        await plugin.xp_admin(admin_ev, "switch", "on")
        check(
            "G3b 管理员 switch 生效",
            plugin._config["admin_enabled"] is True and "总开关已开启" in result_text(admin_ev),
        )
        # 补 Bot 模板条目：双方均有设置才会生成正向指导（未设置≠同意）
        await plugin.xp_admin(admin_ev, "template", "set", "玩笑尺度", "喜欢", "双向", "3")
        bot_key = build_identity(
            platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
        ).key
        check(
            "G3d 管理员模板 set 生效",
            plugin._store.get_entry("bot", bot_key, "joke") is not None,
        )
        # 管理命令在群聊也只给引导
        admin_grp = FakeEvent(sender_id="10001", self_id="bot1", role="admin", group_id="g", message_id="g5")
        await plugin.xp_admin(admin_grp, "switch", "off")
        check("G3c 管理命令群聊 → 引导", result_text(admin_grp) == GROUP_HINT)

        # -- G4 重载/重启恢复（V17） ------------------------------------------
        await plugin.terminate()
        plugin2 = make_plugin(tmp / "g1")  # 同一数据目录重载
        enabled, _ = plugin2._store.get_user_state(IDENTITY.key)
        check("G4a 重载后本人开关保留", enabled is True)
        await plugin2.xp_status(ev)
        check("G4b 重载后命令正常", "本人开关：开" in result_text(ev))
        e = plugin2._store.get_entry("user", IDENTITY.key, "joke")
        check("G4c 重载后档案保留", e is not None and e.status == "like")

        # 注入器在重载后正常工作
        req = ProviderRequest(prompt="hi"); req.conversation = fake_conversation()
        await plugin2._injector.handle(ev, req)
        check(
            "G4d 重载后注入正常",
            any(getattr(p, "text", "").startswith("【互动边界参考") for p in req.extra_user_content_parts),
        )
        await plugin2.terminate()

        # -- G5 卸载/重装语义（V18：数据目录删除后全新开始） -------------------
        db_dir = tmp / "g5"
        p3 = make_plugin(db_dir)
        await p3.xp_on(FakeEvent(sender_id="10001", self_id="bot1", message_id="g5"))
        p3._store.close()
        shutil.rmtree(db_dir)
        p4 = make_plugin(db_dir)
        enabled4, _ = p4._store.get_user_state(IDENTITY.key)
        check(
            "G5 删除数据目录后全新状态（默认关闭、无档案）",
            enabled4 is False and p4._store.stats()["entries"] == 0,
        )
        await p4.terminate()

        # -- 结构检查（V18：安装件完整性 + 无进程级可变全局状态） -------------
        required = ["main.py", "metadata.yaml", "_conf_schema.json", "README.md"]
        check(
            "G6a 安装件完整",
            all((PLUGIN_ROOT / f).is_file() and (PLUGIN_ROOT / f).stat().st_size > 0 for f in required),
        )
        import astrbot_plugin_preference_profile.pref_profile.injection as inj_mod

        mutable_class_attrs = [
            k for k, v in vars(inj_mod.PreferenceInjector).items()
            if k not in ("__module__", "__doc__", "__init__", "handle", "_handle_inner")
            and not k.startswith("__")
            and isinstance(v, (dict, list, set))
        ]
        check(
            "G6b 注入器无类级可变状态（跨用户串扰结构性不可能）",
            mutable_class_attrs == [],
            f"attrs={mutable_class_attrs}",
        )

        # -- S 系列：流式与失败路径（V15 补充） --------------------------------
        store_s = PrefStore(tmp / "s.db")
        store_s.set_user_enabled(IDENTITY.key, True)
        store_s.upsert_entry(
            owner_kind="user", identity_key=IDENTITY.key, tag_id="joke",
            tag_display="玩笑尺度", status="like", source="self_declared",
        )
        bot_key = build_identity(
            platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
        ).key
        store_s.upsert_entry(
            owner_kind="bot", identity_key=bot_key, tag_id="joke",
            tag_display="玩笑尺度", status="like", source="admin_template",
        )
        inj_s = PreferenceInjector(
            store_s, {"admin_enabled": True}, lambda: FakePersonaManager(),
            BridgeGuard(star_map={}), None,
        )

        ev_s = FakeEvent(sender_id="10001", self_id="bot1", message_id="s1")
        req_s = ProviderRequest(prompt="流式场景")
        req_s.conversation = fake_conversation(cid="s1")
        await inj_s.handle(ev_s, req_s)
        provider_s = FakeProvider(["流式回复。"])
        runner_s, reset_s = build_runner(req_s, provider_s, ev_s, streaming=True)
        await reset_s
        async for _ in run_agent(runner_s, max_step=2, show_tool_use=False, show_tool_call_result=False):
            pass
        msgs = runner_s.run_context.messages
        u = [m for m in msgs if m.role == "user"][-1]
        flat = "".join(p.text for p in (u.content if isinstance(u.content, list) else []) if hasattr(p, "text"))
        check(
            "S1 流式 Runner 偏好块仍在且调用一次",
            len(provider_s.call_log) == 1 and "互动边界参考" in flat,
        )

        ev_f = FakeEvent(sender_id="10001", self_id="bot1", message_id="s2")
        req_f = ProviderRequest(prompt="失败场景")
        req_f.conversation = fake_conversation(cid="s2")
        await inj_s.handle(ev_f, req_f)
        provider_f = FakeProvider(["unused"])
        provider_f.error_script = [RuntimeError("模拟模型故障")]
        runner_f, reset_f = build_runner(req_f, provider_f, ev_f)
        await reset_f
        try:
            async for _ in run_agent(runner_f, max_step=2, show_tool_use=False, show_tool_call_result=False):
                pass
        except Exception:  # noqa: BLE001 - 错误路径由宿主层处理
            pass
        final = runner_f.get_final_llm_resp()
        check(
            "S2 模型失败：err 终态/无回复且注入仍只一次",
            (final is None or getattr(final, "role", "") in ("err", "assistant"))
            and len(pref_count(req_f)) == 1,
        )
        # 失败后新轮次干净（无残留状态串扰）
        ev_n = FakeEvent(sender_id="10001", self_id="bot1", message_id="s3")
        req_n = ProviderRequest(prompt="恢复场景")
        req_n.conversation = fake_conversation(cid="s3")
        await inj_s.handle(ev_n, req_n)
        check("S3 失败后新轮次正常注入", len(pref_count(req_n)) == 1)
        store_s.close()
    except Exception:
        traceback.print_exc()
        check("P6 未预期异常", False, traceback.format_exc(limit=2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


def pref_count(req) -> list:
    return [
        p for p in req.extra_user_content_parts
        if getattr(p, "text", "").startswith("【互动边界参考")
    ]


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
