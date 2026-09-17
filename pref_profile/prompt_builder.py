"""受限指导文本渲染（ADR-003/007）。

只渲染固定句式与白名单字段（tag_display、档位、方向短语）；
note 等自由文本字段永不进入输出。固定头部约束"仅调整表达方式、
不发起/升级话题、任一方示意停止立即停止"。
"""

from __future__ import annotations

from .policy import Decision, intensity_word

HEADER = (
    "【互动边界参考（系统，仅本轮）】"
    "以下内容仅用于调整表达方式与强度：不主动发起或升级相关话题，"
    "仅在对话自然涉及该维度时参考；任何一方明确示意停止时立即停止并回到普通话题。"
)

MAX_ITEM_CHARS = 120


def render_item(item) -> str:
    if item.kind == "avoid":
        text = f"回避「{item.tag_display}」相关内容（存在明确禁止或不喜欢）"
    else:
        text = (
            f"「{item.tag_display}」：仅在对方自然提起时以不超过"
            f"「{intensity_word(item.cap_intensity)}」的程度参与，{item.direction_phrase}"
        )
    if len(text) > MAX_ITEM_CHARS:
        text = text[: MAX_ITEM_CHARS - 1] + "…"
    return text


def render_decision(
    decision: Decision,
    max_chars: int = 600,
) -> str | None:
    """渲染最终注入文本；预算不足时丢尾部条目；返回 None 表示无可注入。"""

    if decision.action != "inject":
        return None
    lines: list[str] = [HEADER]
    if decision.rhythm_note:
        lines.append(decision.rhythm_note)
    for item in decision.items:
        lines.append(render_item(item))

    def _join(parts: list[str]) -> str:
        return "\n".join(parts)

    # 超预算时从尾部丢条目（avoid 在前，天然最后被丢）
    while lines and len(_join(lines)) > max_chars:
        if len(lines) <= 1:
            return None  # 连头部都放不下：不注入
        lines.pop()
    if len(lines) <= 1:
        return None
    return _join(lines)
