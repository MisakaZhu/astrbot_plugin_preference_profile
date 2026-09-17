"""身份模型（ADR-002）。

用户身份四元组 (platform_id, self_id, persona_scope, sender_id)；
Bot 人格模板身份三元组（无 sender）。分隔符用控制字符，避免与
QQ 号 / 平台 ID / 人格名冲突。persona_scope 解析失败返回 None，
调用方必须跳过本轮，绝不落默认人格。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KEY_SEP = "\x1f"

BOT_KIND = "bot"
USER_KIND = "user"


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
) -> str | None:
    """与宿主 build 阶段同参解析最终人格。

    返回 None 表示宿主解析异常——本轮必须跳过（不注入、不隔离、不落库），
    绝不使用默认人格兜底，避免串档。
    """

    try:
        conversation_persona_id = getattr(conversation, "persona_id", None)
        result = await persona_manager.resolve_selected_persona(
            umo=event.unified_msg_origin,
            conversation_persona_id=conversation_persona_id,
            platform_name=event.get_platform_name(),
        )
        persona_id = result[0] if result else None
    except Exception:  # noqa: BLE001 - 宿主异常保守跳过
        return None
    return (persona_id or "").strip() or None
