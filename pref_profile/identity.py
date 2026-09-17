"""身份模型（ADR-002）与宿主接口兼容层。

用户身份四元组 (platform_id, self_id, persona_scope, sender_id)；
Bot 人格模板身份三元组（无 sender）。分隔符用控制字符，避免与
QQ 号 / 平台 ID / 人格名冲突。persona_scope 解析失败返回 None，
调用方必须跳过本轮，绝不落默认人格。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

KEY_SEP = "\x1f"

BOT_KIND = "bot"
USER_KIND = "user"


def host_is_private_chat(event: Any) -> bool:
    """真实宿主的私聊判定（is_private_chat 是**方法**，Codex 复核 R1）。

    统一入口：取属性 → 可调用则调用 → bool。任何异常一律返回 False
    （拒绝私人操作，保守方向）。禁止在生产代码里直接
    ``bool(getattr(event, "is_private_chat", False))``——那是绑定方法的
    真假值，恒为 True。
    """

    try:
        attr = getattr(event, "is_private_chat", None)
        if attr is None:
            return False
        value = attr() if callable(attr) else attr
        return bool(value)
    except Exception:  # noqa: BLE001 - 判定异常按非私聊拒绝
        return False


@dataclass(frozen=True)
class PrefIdentity:
    """偏好档案归属主体。sender_id 为空表示 Bot 人格模板。"""

    platform_id: str
    self_id: str
    persona_scope: str
    sender_id: str

    @property
    def owner_kind(self) -> str:
        return BOT_KIND if not self.sender_id else USER_KIND

    @property
    def key(self) -> str:
        return KEY_SEP.join(
            (self.platform_id, self.self_id, self.persona_scope, self.sender_id)
        )


def build_identity(
    *,
    platform_id: str,
    self_id: str,
    persona_scope: str,
    sender_id: str = "",
) -> PrefIdentity:
    """构建身份；persona_scope 不允许为空（调用方须先完成解析）。"""

    scope = (persona_scope or "").strip()
    if not scope:
        raise ValueError("persona_scope 为空：身份未解析，禁止构造")
    return PrefIdentity(
        platform_id=str(platform_id or ""),
        self_id=str(self_id or ""),
        persona_scope=scope,
        sender_id=str(sender_id or ""),
    )


def identity_from_key(key: str) -> PrefIdentity:
    parts = key.split(KEY_SEP)
    if len(parts) != 4:
        raise ValueError(f"非法身份键：{key!r}")
    return PrefIdentity(*parts)


async def resolve_persona_scope(
    persona_manager: Any,
    event: Any,
    conversation: Any,
    provider_settings: dict | None = None,
) -> str | None:
    """与宿主 build 阶段同参解析最终人格（Codex 复核 R4）。

    - 4.26.0 的 ``resolve_selected_persona`` 额外接受 ``provider_settings``
      （默认人格取 default_personality）；4.28.0 无该参数。按签名适配，
      不写死默认人格。
    - 返回 None 表示宿主解析异常——本轮必须跳过（不注入、不隔离、
      不落库），绝不使用默认人格兜底，避免串档。
    """

    try:
        conversation_persona_id = getattr(conversation, "persona_id", None)
        kwargs: dict[str, Any] = {
            "umo": event.unified_msg_origin,
            "conversation_persona_id": conversation_persona_id,
            "platform_name": event.get_platform_name(),
        }
        try:
            sig = inspect.signature(persona_manager.resolve_selected_persona)
            if "provider_settings" in sig.parameters:
                kwargs["provider_settings"] = provider_settings
        except (TypeError, ValueError):
            pass
        result = await persona_manager.resolve_selected_persona(**kwargs)
        persona_id = result[0] if result else None
    except Exception:  # noqa: BLE001 - 宿主异常保守跳过
        return None
    return (persona_id or "").strip() or None
