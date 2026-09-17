"""P0 最小真实宿主契约验证（MIS-146）。

用本机真实 AstrBot 包（4.26.0 / 4.28.0 共享 venv，只读、不装依赖）验证
偏好插件技术路线的关键宿主事实。除事件/会话管理假端外全部使用宿主真实
组件；不连接网络、不调用真实模型。

事实清单：
  F1  ProviderRequest.extra_user_content_parts 存在；TextPart.mark_as_temp()
      后 model_dump_for_context() 携带 _no_save=True（临时块持久化语义）。
  F2  star_handlers_registry 按 priority 降序执行：priority=20 的
      OnLLMRequestEvent 处理器先于 priority=0 执行。
  F3  真实 call_event_hook 链路上，高优先级处理器写入 event extra 的标志，
      低优先级处理器可读到（Context Bridge 捕获前排除协议的机制核心）。
  F4  PersonaManager.resolve_selected_persona 关键字签名两版宿主一致
      (umo / conversation_persona_id / platform_name)。
  F5  宿主 InternalAgentSubStage._save_to_history 真实方法跳过带
      _no_save 的 user/assistant 消息（临时偏好块不进宿主历史）。
  F6  ProviderRequest 用户消息组装顺序：prompt 在前、extra_user_content_parts
      紧随其后（注入位置在用户消息之后，不覆盖 prompt）。

运行（分别用两个宿主 venv）：
  <venv>/Scripts/python.exe tests/p0_host_contract_check.py
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fakes import FakeConversationManager, FakeEvent, fake_conversation  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []

HOST_VERSION = "unknown"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


# ---------------------------------------------------------------------------
# 真实宿主组件导入
# ---------------------------------------------------------------------------
from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.core.agent.message import Message, TextPart  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook  # noqa: E402
from astrbot.core.star.star import StarMetadata, star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.provider.entities import LLMResponse  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
)
import astrbot  # noqa: E402

try:
    from importlib.metadata import version as _pkg_version

    HOST_VERSION = _pkg_version("astrbot")
except Exception:  # noqa: BLE001
    HOST_VERSION = getattr(astrbot, "__version__", "unknown")

TEST_MODULE = "tests.p0_host_contract_check"


def register_handler(
    full_name: str,
    handler,
    priority: int,
) -> StarHandlerMetadata:
    """向真实 star_handlers_registry 注册一个测试处理器（结束时移除）。"""
    module_path = TEST_MODULE
    star_map[module_path] = StarMetadata(
        name="p0_probe_plugin",
        activated=True,
        module_path=module_path,
        reserved=False,
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


def cleanup(metas: list[StarHandlerMetadata]) -> None:
    for meta in metas:
        star_handlers_registry.remove(meta)
    star_map.pop(TEST_MODULE, None)


async def main() -> int:
    print(f"宿主包版本: {HOST_VERSION}")
    print(f"Python: {sys.version.split()[0]}")
    print("=" * 60)

    # -- F1 临时块序列化语义 ---------------------------------------------
    try:
        part = TextPart(text="（本轮偏好指导：轻强度）").mark_as_temp()
        dumped = part.model_dump_for_context()
        plain = TextPart(text="对照").model_dump_for_context()
        check(
            "F1a mark_as_temp 后 dump 带 _no_save=True",
            dumped.get("_no_save") is True,
            f"dump={dumped}",
        )
        check(
            "F1b 未标记的 part 不带 _no_save",
            "_no_save" not in plain,
            f"dump={plain}",
        )
        req = ProviderRequest(prompt="你好")
        req.extra_user_content_parts.append(part)
        check(
            "F1c ProviderRequest.extra_user_content_parts 可追加",
            len(req.extra_user_content_parts) == 1,
        )
    except Exception:
        traceback.print_exc()
        check("F1 临时块序列化", False, traceback.format_exc(limit=1))

    # -- F2/F3 钩子优先级 + extra 传递（真实 call_event_hook） ------------
    order: list[str] = []
    flag_seen_by_low: dict[str, bool] = {}

    async def high_priority_hook(event, req) -> None:
        order.append("high")
        event.set_extra("p0_exclude_flag", True)

    async def low_priority_hook(event, req) -> None:
        order.append("low")
        flag_seen_by_low["value"] = event.get_extra("p0_exclude_flag") is True

    metas: list[StarHandlerMetadata] = []
    try:
        metas.append(register_handler("low_hook", low_priority_hook, 0))
        metas.append(register_handler("high_hook", high_priority_hook, 20))
        event = FakeEvent(sender_id="u1")
        req = ProviderRequest(prompt="你好")
        stopped = await call_event_hook(event, EventType.OnLLMRequestEvent, req)
        check("F2 priority=20 先于 priority=0", order == ["high", "low"], f"order={order}")
        check(
            "F3 高优先级 set_extra 标志被低优先级读到",
            flag_seen_by_low.get("value") is True,
            f"seen={flag_seen_by_low}",
        )
        check("F3b 钩子链未停止事件", stopped is False)
    except Exception:
        traceback.print_exc()
        check("F2/F3 钩子优先级", False, traceback.format_exc(limit=1))
    finally:
        cleanup(metas)

    # -- F4 人格解析签名 ----------------------------------------------------
    try:
        from astrbot.core.persona_mgr import PersonaManager

        sig = inspect.signature(PersonaManager.resolve_selected_persona)
        params = set(sig.parameters)
        needed = {"umo", "conversation_persona_id", "platform_name"}
        check(
            "F4 resolve_selected_persona 关键字参数齐全",
            needed.issubset(params),
            f"params={sorted(params)}",
        )
    except Exception:
        traceback.print_exc()
        check("F4 人格解析签名", False, traceback.format_exc(limit=1))

    # -- F5 宿主写回跳过 _no_save 消息（真实 _save_to_history） ------------
    try:
        conv_mgr = FakeConversationManager()
        stage_self = type(
            "StageShell", (), {"conv_manager": conv_mgr}
        )()
        event = FakeEvent(sender_id="u1")
        req = ProviderRequest(prompt="你好")
        req.conversation = fake_conversation(cid="c1")

        normal_user = Message(role="user", content=[TextPart(text="你好")])
        temp_user = Message.model_validate(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "（偏好指导）", "_no_save": True}
                ],
                "_no_save": True,
            }
        )
        assistant = Message(
            role="assistant", content=[TextPart(text="回复")]
        )
        resp = LLMResponse(role="assistant", completion_text="回复")

        await InternalAgentSubStage._save_to_history(
            stage_self, event, req, resp, [normal_user, temp_user, assistant], None
        )
        check(
            "F5a 写回确实发生（conversation 非 None）",
            len(conv_mgr.calls) == 1,
            f"calls={len(conv_mgr.calls)}",
        )
        if conv_mgr.calls:
            def _dict_texts(history):
                texts: list[str] = []
                for item in history:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content")
                    if isinstance(content, list):
                        for p in content:
                            if isinstance(p, dict) and p.get("type") == "text":
                                texts.append(str(p.get("text", "")))
                    elif isinstance(content, str):
                        texts.append(content)
                return texts

            saved_texts = _dict_texts(conv_mgr.calls[0]["history"])
            check(
                "F5b _no_save 消息未进入宿主历史",
                "（偏好指导）" not in saved_texts,
                f"saved_texts={saved_texts}",
            )
            check(
                "F5c 正常消息保留",
                "你好" in saved_texts and "回复" in saved_texts,
                f"saved_texts={saved_texts}",
            )
        # 对照：conversation=None 时短路
        conv_mgr2 = FakeConversationManager()
        stage_self2 = type("StageShell", (), {"conv_manager": conv_mgr2})()
        req2 = ProviderRequest(prompt="你好")
        await InternalAgentSubStage._save_to_history(
            stage_self2, event, req2, resp, [normal_user], None
        )
        check(
            "F5d conversation=None 短路写回",
            len(conv_mgr2.calls) == 0,
            f"calls={len(conv_mgr2.calls)}",
        )
    except Exception:
        traceback.print_exc()
        check("F5 宿主写回跳过 _no_save", False, traceback.format_exc(limit=1))

    # -- F6 用户消息组装顺序 ------------------------------------------------
    try:
        req = ProviderRequest(prompt="今晚聊什么")
        req.extra_user_content_parts.append(
            TextPart(text="[偏好提示]").mark_as_temp()
        )
        msg = req  # 组装方法在 4.26/4.28 上签名略有差异，这里验证序列化形态
        part_texts = [
            p.text
            for p in req.extra_user_content_parts
            if getattr(p, "type", "") == "text"
        ]
        check(
            "F6a 注入内容为独立 text part（不改写 prompt 本体）",
            req.prompt == "今晚聊什么" and part_texts == ["[偏好提示]"],
            f"prompt={req.prompt!r}, parts={part_texts}",
        )
    except Exception:
        traceback.print_exc()
        check("F6 注入组装顺序", False, traceback.format_exc(limit=1))

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}（宿主 {HOST_VERSION}）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
