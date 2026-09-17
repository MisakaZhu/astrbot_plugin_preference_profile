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

    # 注意：is_private_chat 不做任何覆写——真实宿主它是方法（基于
    # message_obj.type 判定）。曾经的 @property 替身掩盖了生产接口误用
    # （Codex 复核 R1），已删除；测试事件必须走宿主真实实现。

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
    return SimpleNamespace(cid=cid, persona_id=persona_id, token_usage=0)


# -- 可控假模型（照 uctx 项目已验证实现，自包含副本） ------------------------

from astrbot.core.provider.entities import LLMResponse  # noqa: E402
from astrbot.core.provider.provider import Provider, ProviderMeta  # noqa: E402


class FakeProvider(Provider):
    """可控假模型：记录每次请求入参，返回预设回复；不连接网络。"""

    def __init__(self, reply_script: list[str] | None = None) -> None:
        super().__init__(
            provider_config={
                "id": "fake_provider",
                "type": "openai",
                "name": "fake-provider",
                "model": "fake-model",
                "key": ["test-key"],
                "api_base": "http://127.0.0.1:0/v1",
                "max_context_tokens": 128000,
            },
            provider_settings={},
        )
        self.model_name = "fake-model"
        self.reply_script = list(reply_script or ["这是假模型的固定回复。"])
        self.call_log: list[dict[str, Any]] = []
        self.error_script: list[Exception | None] = []

    def meta(self) -> ProviderMeta:  # noqa: D102 - 绕过全局注册表
        return ProviderMeta(id="fake_provider", model=self.get_model(), type="openai")

    def get_current_key(self) -> str:
        return "test-key"

    def set_key(self, key: str) -> None:
        pass

    async def get_models(self) -> list[str]:
        return ["fake-model"]

    async def text_chat(
        self,
        prompt: str | None = None,
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        func_tool=None,
        contexts=None,
        system_prompt: str | None = None,
        tool_calls_result=None,
        model: str | None = None,
        extra_user_content_parts=None,
        tool_choice: str = "auto",
        request_max_retries: int | None = None,
        **kwargs,
    ) -> LLMResponse:
        self.call_log.append(
            {
                "prompt": prompt,
                "contexts": [
                    m if isinstance(m, dict) else m.model_dump() for m in (contexts or [])
                ],
                "system_prompt": system_prompt,
            }
        )
        if self.error_script:
            err = self.error_script.pop(0)
            if err is not None:
                raise err
        if len(self.reply_script) > 1:
            reply = self.reply_script.pop(0)
        else:
            reply = self.reply_script[0]
        return LLMResponse(role="assistant", completion_text=reply)

    async def text_chat_stream(self, **kwargs):
        resp = await self.text_chat(**kwargs)
        yield resp
