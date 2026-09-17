"""决策引擎（ADR-007）：确定性、可解释、冲突取更严格。

输入为已完成门禁校验的合成数据（身份/epoch 校验在钩子层完成），
输出受限结构（原因码 + 少量指导项），不输出自由规则、不执行任何
用户文本。性别不是输入（结构上不存在该字段）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .model import Entry

RHYTHM_NORMAL = "normal"
RHYTHM_SLOW = "slow_down"
RHYTHM_PAUSE = "pause_intimacy"


@dataclass(frozen=True)
class RelationSnapshot:
    """Relation Arc 只读快照的消费形态（ADR-005）。available=False 时
    关系限制一律不生效（保守），独立偏好不受影响。"""

    available: bool = False
    paused: bool = False
    interaction_rhythm: str = RHYTHM_NORMAL
    state_labels: tuple = ()

    @classmethod
    def unavailable(cls) -> "RelationSnapshot":
        return cls(available=False)


@dataclass
class TurnInput:
    admin_enabled: bool
    is_private: bool
    user_enabled: bool
    user_entries: list[Entry] = field(default_factory=list)
    bot_entries: list[Entry] = field(default_factory=list)
    relation: Optional[RelationSnapshot] = None


@dataclass(frozen=True)
class GuidanceItem:
    kind: str  # "avoid" | "positive"
    tag_display: str
    cap_intensity: int
    direction_phrase: str


@dataclass(frozen=True)
class Decision:
    action: str  # "inject" | "skip"
    reason_code: str
    items: tuple = ()  # tuple[GuidanceItem, ...]
    rhythm_note: Optional[str] = None
    detail: tuple = ()  # 脱敏细节（原因码片段），供权限内解释


_SLOW_CAP = 2
_NEUTRAL_BASE = 1  # neutral 参与方贡献的强度基线（不抬高上限）


def _pair_by_tag(user_entries: list[Entry], bot_entries: list[Entry]):
    pairs: dict[str, tuple[Optional[Entry], Optional[Entry]]] = {}
    for e in user_entries:
        pairs.setdefault(e.tag_id, (None, None))
        u, b = pairs[e.tag_id]
        pairs[e.tag_id] = (e, b)
    for e in bot_entries:
        pairs.setdefault(e.tag_id, (None, None))
        u, b = pairs[e.tag_id]
        pairs[e.tag_id] = (u, e)
    return pairs


def _direction_phrase(user_dir: str, bot_dir: str) -> str:
    """确定性方向措辞：互补→回应；任一双向→灵活；同向→自然。"""

    pair = {user_dir, bot_dir}
    if "both" in pair:
        return "灵活"
    if pair == {"active", "receptive"}:
        return "按对方节奏回应"
    return "自然"


def _intensity_word(cap: int) -> str:
    return {1: "很轻", 2: "轻", 3: "适中", 4: "较强", 5: "较高"}.get(cap, "适中")


def intensity_word(cap: int) -> str:
    """供 prompt_builder 复用的强度档位措辞。"""

    return _intensity_word(cap)


def evaluate(turn: TurnInput, max_items: int = 6) -> Decision:
    """按 ADR-007 顺序决策。max_items 为单轮条目预算（含 avoid）。"""

    if not turn.admin_enabled:
        return Decision("skip", "admin_disabled")
    if not turn.is_private:
        return Decision("skip", "not_private_chat")
    if not turn.user_enabled:
        return Decision("skip", "user_disabled")

    relation = turn.relation if turn.relation is not None else RelationSnapshot.unavailable()
    rhythm = RHYTHM_NORMAL
    if relation.available and relation.paused:
        rhythm = RHYTHM_PAUSE
    elif relation.available and relation.interaction_rhythm == RHYTHM_PAUSE:
        rhythm = RHYTHM_PAUSE
    elif relation.available and relation.interaction_rhythm == RHYTHM_SLOW:
        rhythm = RHYTHM_SLOW

    pairs = _pair_by_tag(turn.user_entries, turn.bot_entries)
    avoid: list[GuidanceItem] = []
    positive: list[GuidanceItem] = []
    for tag_id, (ue, be) in pairs.items():
        display = (ue or be).tag_display or tag_id
        statuses = {e.status for e in (ue, be) if e is not None}
        if "forbidden" in statuses:
            avoid.append(GuidanceItem("avoid", display, 0, ""))
            continue
        if "dislike" in statuses:
            avoid.append(GuidanceItem("avoid", display, 0, ""))
            continue
        # 正向指导：双方都有条目，且至少一方 like（另一方 like/neutral）。
        # 未设置（无条目）不作为同意，不生成。
        if ue is None or be is None:
            continue
        if "like" not in statuses:
            continue  # 双方 neutral：无信息
        # 强度上限只由明确 like 的一方决定：中立=可接受但不特别偏好，
        # 既不抬升也不压制对方强度；未设置方不参与（前面已 continue）。
        caps = []
        if ue.status == "like":
            caps.append(ue.intensity)
        if be.status == "like":
            caps.append(be.intensity)
        cap = min(caps) if caps else _NEUTRAL_BASE
        positive.append(
            GuidanceItem(
                "positive", display, cap,
                _direction_phrase(ue.direction, be.direction),
            )
        )

    # 关系节奏压制（更严格限制优先；禁忌回避属于安全提示，保留）
    rhythm_note = None
    if rhythm == RHYTHM_PAUSE:
        positive = []
        rhythm_note = "当前关系互动节奏处于暂停：保持普通友好交流，不进行亲密向互动。"
    elif rhythm == RHYTHM_SLOW:
        positive = [p for p in positive if True]
        positive = [
            GuidanceItem(p.kind, p.tag_display, min(p.cap_intensity, _SLOW_CAP), p.direction_phrase)
            for p in positive
        ]
        rhythm_note = "当前关系互动节奏放缓：整体表达保持克制。"

    # 数量预算：avoid（安全信息）优先保留，positive 按强度降序截断
    positive.sort(key=lambda g: (-g.cap_intensity, g.tag_display))
    budget_left = max(0, max_items - len(avoid))
    positive = positive[:budget_left]

    items = [*avoid, *positive]
    if not items and rhythm_note is None:
        return Decision("skip", "no_effective_data")
    reason = "ok"
    if rhythm_note is not None:
        reason = "ok_rhythm_paused" if rhythm == RHYTHM_PAUSE else "ok_rhythm_capped"
    detail = tuple(f"{g.kind}:{g.tag_display}" for g in items)
    return Decision("inject", reason, tuple(items), rhythm_note, detail)
