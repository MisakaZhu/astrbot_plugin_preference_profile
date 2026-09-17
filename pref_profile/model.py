"""偏好条目数据模型与输入校验（ADR-001/006/008）。

校验原则：标签显示文本和备注只是数据——限制长度/数量/枚举，
任何自由文本不提升为指令、不在任何执行路径中被求值。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

STATUS_VALUES = ("like", "neutral", "dislike", "forbidden")
DIRECTION_VALUES = ("active", "receptive", "both")
SOURCE_VALUES = ("self_declared", "admin_template")
OWNER_KINDS = ("user", "bot")

STATUS_LABELS = {
    "like": "喜欢",
    "neutral": "中立",
    "dislike": "不喜欢",
    "forbidden": "明确禁止",
}
DIRECTION_LABELS = {"active": "主动", "receptive": "接受", "both": "双向"}

# 中性维度的内置标签（tag_id 形态：ASCII 稳定 ID）
BUILTIN_TAGS = (
    "topic",  # 话题偏好
    "address",  # 称呼方式
    "joke",  # 玩笑尺度
    "closeness",  # 亲密度表达
    "rhythm",  # 互动节奏
    "tone",  # 语气风格
)
BUILTIN_TAG_LABELS = {
    "topic": "话题偏好",
    "address": "称呼方式",
    "joke": "玩笑尺度",
    "closeness": "亲密度表达",
    "rhythm": "互动节奏",
    "tone": "语气风格",
}

MAX_CUSTOM_TAGS = 20
MAX_TAG_DISPLAY_LEN = 16
MIN_TAG_DISPLAY_LEN = 2
MAX_NOTE_LEN = 200
MAX_INTENSITY = 5
MIN_INTENSITY = 1

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class ValidationError(ValueError):
    """输入校验失败（原因码见 args[0]）。"""


@dataclass(frozen=True)
class Entry:
    owner_kind: str
    identity_key: str
    tag_id: str
    tag_display: str
    status: str
    direction: str
    intensity: int
    note: str
    source: str
    revision: int
    updated_at: float

    def to_public_dict(self) -> dict:
        """面向用户展示/命令层的脱敏视图（不含 identity_key 原文）。"""

        return {
            "tag_id": self.tag_id,
            "tag_display": self.tag_display,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "direction": self.direction,
            "direction_label": DIRECTION_LABELS.get(self.direction, self.direction),
            "intensity": self.intensity,
            "note_len": len(self.note),
            "source": self.source,
            "revision": self.revision,
        }


def _strip_ctrl(text: str) -> str:
    # 去控制字符；\x1f 是身份分隔符，绝不允许进入标签
    return _CTRL_RE.sub("", unicodedata.normalize("NFC", text or ""))


def normalize_tag_display(display: str) -> str:
    """自定义标签显示名：NFC、去控制字符、限长。"""

    cleaned = _strip_ctrl(display).strip()
    if not (MIN_TAG_DISPLAY_LEN <= len(cleaned) <= MAX_TAG_DISPLAY_LEN):
        raise ValidationError(
            f"tag_display_length:{len(cleaned)}"
        )
    return cleaned


def normalize_note(note: str) -> str:
    cleaned = _strip_ctrl(note).strip()
    if len(cleaned) > MAX_NOTE_LEN:
        raise ValidationError(f"note_length:{len(cleaned)}")
    return cleaned


def validate_status(status: str) -> str:
    if status not in STATUS_VALUES:
        raise ValidationError(f"status_invalid:{status!r}")
    return status


def validate_direction(direction: str) -> str:
    if direction not in DIRECTION_VALUES:
        raise ValidationError(f"direction_invalid:{direction!r}")
    return direction


def validate_intensity(intensity: int) -> int:
    if not isinstance(intensity, int) or isinstance(intensity, bool):
        raise ValidationError(f"intensity_type:{type(intensity).__name__}")
    if not (MIN_INTENSITY <= intensity <= MAX_INTENSITY):
        raise ValidationError(f"intensity_range:{intensity}")
    return intensity


def validate_owner_kind(owner_kind: str) -> str:
    if owner_kind not in OWNER_KINDS:
        raise ValidationError(f"owner_kind_invalid:{owner_kind!r}")
    return owner_kind


def make_tag_id(display: str) -> str:
    """自定义标签的稳定 tag_id：内容寻址（同显示名同 ID）。"""

    import hashlib

    normalized = normalize_tag_display(display)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"custom_{digest}"
