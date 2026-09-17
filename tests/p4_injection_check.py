"""P4 真实请求链路注入验证（MIS-150）。

真实宿主组件：star_handlers_registry / call_event_hook / ProviderRequest /
ToolLoopAgentRunner.reset / run_agent / InternalAgentSubStage._save_to_history。
假端仅限事件、模型 Provider 与会话管理（不连接网络）。

  H1  真实钩子链：priority=20 注入器先于 priority=0 的模拟 uctx 捕获器，
      排除标志在捕获器可见。
  H2  注入产物：单个 TextPart、_no_save=True、prompt/system_prompt/
      contexts/func_tool/conversation 均未被改动（V11）。
  H3  同一事件重复走 call_event_hook 不双注（V15 重试）。
  H4  门禁矩阵：总开关关/群聊/本人关/无档案 → 无注入（V01/V08）。
  H5  off 后新轮次无注入（epoch 失效，V08）。
  H6  uctx 三态：no_bridge 注入；protocol_ok 注入+排除标志；
      protocol_missing 不注入（V16）。
  H7  真实 Runner：模型实际收到的请求含偏好块（V10），偏好块不进入
      宿主写回历史（V12 宿主侧），原 prompt 消息保留。
  H8  无偏好数据轮次：Runner 正常、无偏好块（默认零干预）。

运行：<venv>/Scripts/python.exe tests/p4_injection_check.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fakes import (  # noqa: E402
    FakeConversationManager,
    FakeEvent,
    FakeProvider,
    fake_conversation,
)

PASS: list[str] = []
FAIL: list[str] = []

TEST_MODULE = "tests.p4_injection_check"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


# 真实宿主组件
from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook  # noqa: E402
from astrbot.core.star.star import StarMetadata, star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
)
from astrbot.core.astr_agent_run_util import run_agent  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
)
from astrbot.core.astr_agent_hooks import MAIN_AGENT_HOOKS  # noqa: E402
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor  # noqa: E402
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402

from pref_profile import PROTOCOL_TURN_EXCLUSION  # noqa: E402
from pref_profile.bridge_guard import (  # noqa: E402
    EXCLUDE_EXTRA_KEY,
    BridgeGuard,
)
from pref_profile.injection import PreferenceInjector  # noqa: E402
from pref_profile.identity import build_identity  # noqa: E402
from pref_profile.store import PrefStore  # noqa: E402


class FakePersonaManager:
    async def resolve_selected_persona(self, **kw):
        return ("persona_A", {}, None, False)


def register_handler(full_name, handler, priority, event_type=EventType.OnLLMRequestEvent):
    module_path = TEST_MODULE
    star_map[module_path] = StarMetadata(
        name="p4_probe_plugin", activated=True, module_path=module_path, reserved=False
    )
    meta = StarHandlerMetadata(
        event_type=event_type,
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
    star_map.pop(TEST_MODULE, None)


IDENTITY = build_identity(
    platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A", sender_id="10001",
)
BOT_KEY = build_identity(
    platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
).key


def seed_prefs(store: PrefStore):
    store.set_user_enabled(IDENTITY.key, True)
    store.upsert_entry(
        owner_kind="user", identity_key=IDENTITY.key, tag_id="joke",
        tag_display="玩笑尺度", status="like", intensity=3, source="self_declared",
    )
    store.upsert_entry(
        owner_kind="bot", identity_key=BOT_KEY, tag_id="joke",
        tag_display="玩笑尺度", status="like", intensity=2, source="admin_template",
    )


def make_injector(store, config, bridge=None):
    return PreferenceInjector(
        store, config, lambda: FakePersonaManager(),
        bridge or BridgeGuard(star_map={}),
    )


async def run_hook(event, req):
    return await call_event_hook(event, EventType.OnLLMRequestEvent, req)


def pref_parts(req) -> list:
    return [
        p for p in req.extra_user_content_parts
        if getattr(p, "text", "").startswith("【互动边界参考")
    ]


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pref_p4_"))
    try:
        # -- H1/H2/H3：真实钩子链 ------------------------------------------
        store = PrefStore(tmp / "h1.db")
        seed_prefs(store)
        config = {"admin_enabled": True}
        injector = make_injector(store, config)

        seen_by_uctx: dict = {}

        async def fake_uctx_capture(event, req):
            seen_by_uctx["exclude_flag"] = event.get_extra(EXCLUDE_EXTRA_KEY)
            seen_by_uctx["parts_len"] = len(req.extra_user_content_parts)

        metas = []
        try:
            metas.append(register_handler("uctx_like", fake_uctx_capture, 0))
            metas.append(register_handler("pref_inject", injector.handle, 20))

            event = FakeEvent(sender_id="10001", self_id="bot1", message_str="随便聊聊")
            req = ProviderRequest(prompt="随便聊聊")
            req.conversation = fake_conversation(cid="c1")
            req.system_prompt = "原人格"
            req.contexts = [{"role": "user", "content": "旧历史"}]
            conv_obj = req.conversation
            await run_hook(event, req)

            parts = pref_parts(req)
            check(
                "H1 高优先级注入器先执行，uctx 捕获器可见排除标志缺失但已见注入",
                seen_by_uctx["exclude_flag"] is None and seen_by_uctx["parts_len"] == 1,
                f"seen={seen_by_uctx}",
            )
            # 注：本场景 bridge star_map 为空 → no_bridge，无需排除标志
            check("H2a 注入单个偏好块", len(parts) == 1)
            check(
                "H2b 偏好块 mark_as_temp",
                parts and parts[0]._no_save is True,
            )
            check(
                "H2c 不改 prompt/system_prompt/contexts/conversation",
                req.prompt == "随便聊聊" and req.system_prompt == "原人格"
                and req.contexts == [{"role": "user", "content": "旧历史"}]
                and req.conversation is conv_obj and req.func_tool is None,
            )
            check("H2d 偏好块含玩笑指导", "玩笑尺度" in (parts[0].text if parts else ""))

            # H3 同事件重试
            await run_hook(event, req)
            check(
                "H3 同一事件重复钩子不双注",
                len(pref_parts(req)) == 1,
                f"parts={len(pref_parts(req))}",
            )
        finally:
            cleanup(metas)

        # -- H4 门禁矩阵（直接调用 handle，等价真实链路） --------------------
        async def injected(store_x, config_x, event_x):
            inj = make_injector(store_x, config_x)
            req_x = ProviderRequest(prompt="hi")
            req_x.conversation = fake_conversation(cid="c")
            await inj.handle(event_x, req_x)
            return len(pref_parts(req_x))

        store_a = PrefStore(tmp / "h4a.db"); seed_prefs(store_a)
        check("H4a 总开关关 → 不注入", await injected(store_a, {"admin_enabled": False}, FakeEvent(sender_id="10001", self_id="bot1")) == 0)
        check("H4b 群聊 → 不注入", await injected(store_a, {"admin_enabled": True}, FakeEvent(sender_id="10001", self_id="bot1", group_id="g")) == 0)
        store_b = PrefStore(tmp / "h4b.db")
        store_b.set_user_enabled(IDENTITY.key, True)
        check("H4c 无档案 → 不注入", await injected(store_b, {"admin_enabled": True}, FakeEvent(sender_id="10001", self_id="bot1")) == 0)
        store_c = PrefStore(tmp / "h4c.db"); seed_prefs(store_c)
        store_c.set_user_enabled(IDENTITY.key, False)  # 本人关
        check("H4d 本人关 → 不注入", await injected(store_c, {"admin_enabled": True}, FakeEvent(sender_id="10001", self_id="bot1")) == 0)
        # 他人身份（不同 sender）不注入
        check("H4e 他人未开启 → 不注入", await injected(store_a, {"admin_enabled": True}, FakeEvent(sender_id="99999", self_id="bot1")) == 0)

        # -- H5 off 失效 ------------------------------------------------------
        store_d = PrefStore(tmp / "h5.db"); seed_prefs(store_d)
        inj_d = make_injector(store_d, {"admin_enabled": True})
        ev1 = FakeEvent(sender_id="10001", self_id="bot1", message_id="m1")
        r1 = ProviderRequest(prompt="a"); r1.conversation = fake_conversation()
        await inj_d.handle(ev1, r1)
        n_before = len(pref_parts(r1))
        store_d.set_user_enabled(IDENTITY.key, False)  # /xp off
        ev2 = FakeEvent(sender_id="10001", self_id="bot1", message_id="m2")
        r2 = ProviderRequest(prompt="b"); r2.conversation = fake_conversation()
        await inj_d.handle(ev2, r2)
        check(
            "H5 off 后新轮次不注入（旧轮次已注入不受影响）",
            n_before == 1 and len(pref_parts(r2)) == 0,
        )

        # -- H6 uctx 三态 -----------------------------------------------------
        def uctx_star_map(with_protocol: bool, activated: bool = True):
            module = SimpleNamespace()
            if with_protocol:
                module.UCTX_EXCLUDE_PROTOCOL = PROTOCOL_TURN_EXCLUSION
            return {
                "fake.uctx": StarMetadata(
                    name="astrbot_plugin_user_context_bridge",
                    activated=activated,
                    module=module,
                    version="0.1.0",
                )
            }

        bg_ok = BridgeGuard(star_map=uctx_star_map(True))
        check("H6a protocol_ok 探测", bg_ok.mode == "protocol_ok")
        inj_ok = make_injector(store_a, {"admin_enabled": True}, bg_ok)
        ev = FakeEvent(sender_id="10001", self_id="bot1", message_id="m3")
        r = ProviderRequest(prompt="c"); r.conversation = fake_conversation()
        await inj_ok.handle(ev, r)
        check(
            "H6b protocol_ok 注入 + 排除标志已打",
            len(pref_parts(r)) == 1
            and ev.get_extra(EXCLUDE_EXTRA_KEY) == {"protocol": PROTOCOL_TURN_EXCLUSION},
        )

        bg_miss = BridgeGuard(star_map=uctx_star_map(False))
        check("H6c protocol_missing 探测", bg_miss.mode == "protocol_missing")
        inj_miss = make_injector(store_a, {"admin_enabled": True}, bg_miss)
        ev2 = FakeEvent(sender_id="10001", self_id="bot1", message_id="m4")
        r2 = ProviderRequest(prompt="d"); r2.conversation = fake_conversation()
        await inj_miss.handle(ev2, r2)
        check(
            "H6d protocol_missing 不注入且无排除标志（V16 保守）",
            len(pref_parts(r2)) == 0 and ev2.get_extra(EXCLUDE_EXTRA_KEY) is None,
        )

        # 启用但无档案的用户在 protocol_ok 下仍打排除标志（启用即隔离意图）
        store_e = PrefStore(tmp / "h6.db")
        store_e.set_user_enabled(IDENTITY.key, True)
        inj_e = make_injector(store_e, {"admin_enabled": True}, bg_ok)
        ev3 = FakeEvent(sender_id="10001", self_id="bot1", message_id="m5")
        r3 = ProviderRequest(prompt="e"); r3.conversation = fake_conversation()
        await inj_e.handle(ev3, r3)
        check(
            "H6e 启用即排除（无论是否注入文本）",
            len(pref_parts(r3)) == 0 and ev3.get_extra(EXCLUDE_EXTRA_KEY) is not None,
        )

        # -- H7/H8 真实 Runner ------------------------------------------------
        def build_runner(req, provider, event):
            runner = ToolLoopAgentRunner()
            astr_ctx = SimpleNamespace(event=event)
            reset_coro = runner.reset(
                provider=provider,
                request=req,
                run_context=ContextWrapper(context=astr_ctx),
                tool_executor=FunctionToolExecutor(),
                agent_hooks=MAIN_AGENT_HOOKS,
                streaming=False,
            )
            return runner, reset_coro

        store_f = PrefStore(tmp / "h7.db"); seed_prefs(store_f)
        inj_f = make_injector(store_f, {"admin_enabled": True})
        ev7 = FakeEvent(sender_id="10001", self_id="bot1", message_id="m7")
        req7 = ProviderRequest(prompt="讲个玩笑")
        req7.system_prompt = "原人格说明"
        req7.contexts = [{"role": "user", "content": "旧问"}, {"role": "assistant", "content": "旧答"}]
        req7.conversation = fake_conversation(cid="c7")
        await inj_f.handle(ev7, req7)
        provider7 = FakeProvider(["好呀，轻松一点。"])
        runner7, reset7 = build_runner(req7, provider7, ev7)
        await reset7
        async for _ in run_agent(runner7, max_step=3, show_tool_use=False, show_tool_call_result=False):
            pass
        check(
            "H7a 真实 Runner：模型请求发生且为一次（无额外调用）",
            len(provider7.call_log) == 1,
            f"calls={len(provider7.call_log)}",
        )
        msgs = runner7.run_context.messages
        user_final = [m for m in msgs if m.role == "user"][-1]
        flat = "".join(
            p.text for p in (user_final.content if isinstance(user_final.content, list) else [])
            if hasattr(p, "text")
        )
        check(
            "H7b 模型收到的 user 消息含偏好块与原 prompt",
            "讲个玩笑" in flat and "互动边界参考" in flat and "玩笑尺度" in flat,
            f"flat={flat[:120]!r}",
        )
        # 宿主写回：真实 _save_to_history 后偏好块不落历史
        conv_mgr = FakeConversationManager()
        shell = type("Shell", (), {"conv_manager": conv_mgr})()
        from astrbot.core.provider.entities import LLMResponse

        final_resp = runner7.get_final_llm_resp() or LLMResponse(role="assistant", completion_text="好呀")
        await InternalAgentSubStage._save_to_history(shell, ev7, req7, final_resp, msgs, None)
        saved = conv_mgr.calls[0]["history"] if conv_mgr.calls else []
        saved_text = ""
        for item in saved:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for p in content:
                    if isinstance(p, dict):
                        saved_text += str(p.get("text", ""))
            elif isinstance(content, str):
                saved_text += content
        check(
            "H7c 偏好块不进宿主写回历史（原消息保留）",
            "互动边界参考" not in saved_text and "讲个玩笑" in saved_text,
            f"saved={saved_text[:120]!r}",
        )

        # H8 无偏好数据轮次
        store_g = PrefStore(tmp / "h8.db")
        inj_g = make_injector(store_g, {"admin_enabled": True})
        ev8 = FakeEvent(sender_id="10001", self_id="bot1", message_id="m8")
        req8 = ProviderRequest(prompt="普通聊天")
        req8.conversation = fake_conversation(cid="c8")
        await inj_g.handle(ev8, req8)
        provider8 = FakeProvider(["普通回复。"])
        runner8, reset8 = build_runner(req8, provider8, ev8)
        await reset8
        async for _ in run_agent(runner8, max_step=2, show_tool_use=False, show_tool_call_result=False):
            pass
        msgs8 = runner8.run_context.messages
        user8 = [m for m in msgs8 if m.role == "user"][-1]
        content8 = user8.content
        if isinstance(content8, list):
            flat8 = "".join(
                p.text for p in content8 if hasattr(p, "text")
            )
        else:
            flat8 = str(content8)
        check(
            "H8 无偏好轮次零干预",
            len(provider8.call_log) == 1 and "互动边界参考" not in flat8 and flat8.startswith("普通聊天"),
            f"flat8={flat8!r}",
        )

        for s in (store, store_a, store_b, store_c, store_d, store_e, store_f, store_g):
            s.close()
    except Exception:
        traceback.print_exc()
        check("P4 未预期异常", False, traceback.format_exc(limit=2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
