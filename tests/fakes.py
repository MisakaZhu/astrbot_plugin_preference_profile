"""可控假端：仅替换宿主外围（事件/平台/会话管理），核心组件全部用真实宿主包。

设计参照 user_context_bridge 项目已验证的 fakes 范式，但保持本仓库自包含。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.astr_message_event import MessageType
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.platform_metadata import PlatformMetadata


class _NullTrace:
    def record(self, *args: Any, **kwargs: Any) -> None:
        return None


def make_platform_meta(platform_id: str = "aiocqhttp") -> PlatformMetadata:
    return PlatformMetadata(
        name="aiocqhttp",
        description="fake platform for tests",
        id=platform_id,
    )


def make_message_obj(
    *,
    sender_id: str,
    group_id: str = "",
    message_str: str = "你好",
    self_id: str = "bot_001",
    message_id: str = "m_0001",
) -> AstrBotMessage:
    abm = AstrBotMessage()
    abm.type = (
        MessageType.GROUP_MESSAGE if group_id else MessageType.FRIEND_MESSAGE
    )
    abm.self_id = self_id
    abm.sender = MessageMember(user_id=sender_id, nickname=f"测试用户{sender_id[-2:]}")
    abm.group_id = group_id
    abm.message_str = message_str
    abm.message_id = message_id
    abm.raw_message = message_str
    return abm


class FakeEvent(AstrMessageEvent):
    """最小事件：记录 send 内容与 extras，供断言。"""

    def __init__(
        self,
        *,
        sender_id: str,
        group_id: str = "",
        message_str: str = "你好",
        platform_id: str = "aiocqhttp",
        self_id: str = "bot_001",
        role: str = "member",
        message_id: str = "m_0001",
    ) -> None:
        self.message_str = message_str
        self.message_obj = make_message_obj(
            sender_id=sender_id,
            group_id=group_id,
            message_str=message_str,
            self_id=self_id,
            message_id=message_id,
        )
        self.platform_meta = make_platform_meta(platform_id)
        message_type = (
            MessageType.GROUP_MESSAGE if group_id else MessageType.FRIEND_MESSAGE
        )
        self.session = MessageSession(
            platform_name=platform_id,
            message_type=message_type,
            session_id=group_id or sender_id,
        )
        self.role = role
        self.is_wake = True
        self.is_at_or_wake_command = not bool(group_id)
        self._extras: dict[str, Any] = {}
        self._force_stopped = False
        self._result = None
        self.sent_chains: list[MessageChain] = []
        self._has_send_oper = False
        self.created_at = 0.0
        self.trace = _NullTrace()
        self.plugins_name = None
        self.platform = self.platform_meta

    @property
    def unified_msg_origin(self) -> str:
        return str(self.session)

    @unified_msg_origin.setter
    def unified_msg_origin(self, value: str) -> None:
        raise NotImplementedError("测试事件不允许改路由")

    # AstrMessageEvent 基类要求的抽象成员 -------------------------------------
    def get_sender_id(self) -> str:
        return str(self.message_obj.sender.user_id)

    def get_self_id(self) -> str:
        return str(self.message_obj.self_id)

    def get_group_id(self) -> str:
        return str(self.message_obj.group_id or "")

    async def send(self, message_chain: MessageChain) -> None:
        self.sent_chains.append(message_chain)


@dataclass
class FakeConversationManager:
    """记录 update_conversation 调用，供写回断言。"""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def update_conversation(
        self, umo: str, cid: str, history: list[Any], token_usage: Any = None
    ) -> None:
        self.calls.append(
            {
                "umo": umo,
                "cid": cid,
                "history": list(history),
                "token_usage": token_usage,
            }
        )


def fake_conversation(cid: str = "c1", persona_id: str | None = None):
    return SimpleNamespace(cid=cid, persona_id=persona_id)
