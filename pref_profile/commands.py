"""命令服务层（ADR-008）。

纯逻辑实现：event 为 duck-typed（is_private_chat / get_sender_id /
get_platform_id / get_self_id / unified_msg_origin / role），返回回复文本，
由 main.py 薄层绑定宿主装饰器。便于脱网测试权限与生命周期语义。

群聊纪律：私人子命令在群聊一律只返回通用引导，不暴露标签、
开启状态或任何值。管理命令要求宿主管理员（装饰器 + 双重校验）。
"""

from __future__ import annotations

import secrets
import time
from typing import Any, Awaitable, Callable, Optional

from .identity import PrefIdentity, host_is_private_chat
from .model import (
    BUILTIN_TAG_LABELS,
    MAX_CUSTOM_TAGS,
    STATUS_LABELS,
    Entry,
    ValidationError,
    make_tag_id,
    normalize_note,
    normalize_tag_display,
)
from .store import PrefStore, StoreConflictError

IdentityResolver = Callable[[Any], Awaitable[Optional[PrefIdentity]]]

GROUP_HINT = (
    "偏好与互动边界的设置仅在私聊中可用。请私聊我发送 /xp help 查看用法。"
    "（出于隐私考虑，这里不显示任何人的偏好状态。）"
)

CLEAR_TOKEN_TTL = 300  # 秒

DELETION_BOUNDARY = (
    "删除边界说明：本次删除仅清理本插件保存的该身份偏好档案与缓存，"
    "立即停止后续偏好读取与注入；已经发送给模型的内容无法撤回，"
    "宿主自身聊天记录、其他插件的共享历史以及既有备份不受影响、"
    "也不会被本插件代为删除，请分别使用对应功能清理。"
)

_STATUS_WORDS = {
    "like": "like", "喜欢": "like", "中立": "neutral", "neutral": "neutral",
    "不喜欢": "dislike", "dislike": "dislike",
    "禁止": "forbidden", "forbid": "forbidden", "明确禁止": "forbidden",
    "forbidden": "forbidden",
}
_DIR_WORDS = {
    "active": "active", "主动": "active",
    "receptive": "receptive", "接受": "receptive",
    "both": "both", "双向": "both",
}


def parse_status(word: str) -> Optional[str]:
    return _STATUS_WORDS.get((word or "").strip().lower())


def parse_direction(word: str) -> Optional[str]:
    return _DIR_WORDS.get((word or "").strip().lower())


class CommandError(Exception):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class CommandService:
    def __init__(
        self,
        store: PrefStore,
        config: dict,
        identity_resolver: IdentityResolver,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._store = store
        self._config = config
        self._resolve_identity = identity_resolver
        self._clock = clock
        # identity_key -> (token, expires_at)；仅内存，重启即失效（安全方向）
        self._clear_tokens: dict[str, tuple[str, float]] = {}

    # -- 门禁 ------------------------------------------------------------

    def _require_private(self, event: Any) -> None:
        # 真实宿主 is_private_chat 是方法；统一走 host_is_private_chat（R1）
        if not host_is_private_chat(event):
            raise _GroupOnly()

    async def _require_user_identity(self, event: Any) -> PrefIdentity:
        identity = await self._resolve_identity(event)
        if identity is None or not identity.sender_id:
            raise CommandError(
                "当前无法确认本轮生效人格，为避免档案串档已暂停偏好操作，请稍后再试。"
            )
        return identity

    def _require_admin(self, event: Any) -> None:
        if str(getattr(event, "role", "member")) != "admin":
            raise CommandError("该操作需要宿主管理员权限。")

    # -- 用户子命令 --------------------------------------------------------

    async def handle_help(self, event: Any) -> str:
        if not host_is_private_chat(event):
            return GROUP_HINT
        return (
            "【角色偏好与互动边界（XP 管理）】\n"
            "本插件分别保存 Bot 人格模板与你本人明确设置的偏好，"
            "仅在私聊、双方都启用且不越界时影响互动方式。\n"
            "命令：\n"
            "/xp status — 查看自己的开启状态与档案概览\n"
            "/xp on / /xp off — 本人开启/关闭（关闭立即停止后续注入）\n"
            "/xp show [页码] — 查看自己的条目\n"
            "/xp set <标签> <状态> [方向] [强度] — 设置条目\n"
            "  状态：喜欢/中立/不喜欢/禁止；方向：主动/接受/双向；强度：1-5\n"
            "  内置标签：话题偏好、称呼方式、玩笑尺度、亲密度表达、互动节奏、语气风格；"
            "也可用 2-16 字自定义标签\n"
            "/xp remove <标签> — 删除单条\n"
            "/xp clear — 一次性确认后删除自己的全部档案\n"
            "管理员命令：/xp admin ...\n"
            "说明：性别等属性与偏好方向无关；保存偏好不会建立或改变关系。"
        )

    async def handle_status(self, event: Any) -> str:
        self._require_private(event)
        identity = await self._require_user_identity(event)
        enabled, epoch = self._store.get_user_state(identity.key)
        entries = self._store.list_entries("user", identity.key)
        admin_on = bool(self._config.get("admin_enabled", False))
        lines = ["【偏好功能状态】"]
        lines.append(f"管理员总开关：{'开' if admin_on else '关'}")
        lines.append(f"本人开关：{'开' if enabled else '关'}")
        lines.append(f"档案条目：{len(entries)} 条")
        if not admin_on:
            lines.append("当前管理员总开关未开，任何偏好都不会注入回复。")
        elif not enabled:
            lines.append("你尚未开启（/xp on），不会注入。")
        elif not entries:
            lines.append("档案为空（未设置），不会注入。")
        else:
            lines.append("（条目明细请用 /xp show 查看）")
        return "\n".join(lines)

    async def handle_on(self, event: Any) -> str:
        self._require_private(event)
        identity = await self._require_user_identity(event)
        epoch = self._store.set_user_enabled(identity.key, True)
        if not bool(self._config.get("admin_enabled", False)):
            return (
                "已记录你的本人开关为开启。\n"
                "注意：当前管理员总开关为关闭，偏好暂时不会注入回复；"
                "总开关开启后即自动生效。"
            )
        return (
            "已为你开启偏好功能（仅影响你与我的私聊互动方式）。\n"
            f"生效条件：双方在对应维度均有设置且不越界。\n"
            f"（epoch={epoch}）如需停止：/xp off"
        )

    async def handle_off(self, event: Any) -> str:
        self._require_private(event)
        identity = await self._require_user_identity(event)
        epoch = self._store.set_user_enabled(identity.key, False)
        return (
            "已关闭你的偏好功能：从下一轮起停止读取与注入，"
            "在途请求也会因版本失效不会注入。\n"
            f"（epoch={epoch}）档案仍保留，可随时 /xp on 重新开启，"
            "或 /xp clear 彻底删除。"
        )

    async def handle_show(self, event: Any, page: int = 1) -> str:
        self._require_private(event)
        identity = await self._require_user_identity(event)
        entries = self._store.list_entries("user", identity.key)
        if not entries:
            return (
                "你的偏好档案为空（未设置）。\n"
                "注意：「未设置」与「中立」不同——未设置不作为任何同意，"
                "仅表示该维度不参与。"
            )
        page_size = 8
        page = max(1, int(page))
        total_pages = (len(entries) + page_size - 1) // page_size
        page = min(page, total_pages)
        chunk = entries[(page - 1) * page_size : page * page_size]
        lines = [f"【我的偏好条目】第 {page}/{total_pages} 页"]
        for e in chunk:
            lines.append(
                f"- {e.tag_display or e.tag_id}：{STATUS_LABELS.get(e.status, e.status)}"
                f"｜方向 {_DIR_LABELS.get(e.direction, e.direction)}｜强度 {e.intensity}"
                f"｜rev {e.revision}"
            )
        return "\n".join(lines)

    async def handle_set(
        self,
        event: Any,
        tag: str,
        status: str = "",
        direction: str = "",
        intensity: str = "",
    ) -> str:
        self._require_private(event)
        identity = await self._require_user_identity(event)
        tag_id, tag_display = self._resolve_tag(tag)
        st = parse_status(status)
        if st is None:
            raise CommandError(
                f"无法识别的状态「{status}」。可用：喜欢/中立/不喜欢/禁止。"
            )
        dr = "both"
        if direction:
            dr = parse_direction(direction)
            if dr is None:
                raise CommandError(
                    f"无法识别的方向「{direction}」。可用：主动/接受/双向。"
                )
        inten = 3
        if intensity:
            try:
                inten = int(intensity)
            except ValueError as exc:
                raise CommandError("强度必须是 1-5 的整数。") from exc
        # 自定义标签数量上限（按显示名去重计数）
        if tag_id.startswith("custom_"):
            existing = self._store.list_entries("user", identity.key)
            customs = {
                e.tag_id for e in existing if e.tag_id.startswith("custom_")
            }
            if tag_id not in customs and len(customs) >= MAX_CUSTOM_TAGS:
                raise CommandError(f"自定义标签数量已达上限（{MAX_CUSTOM_TAGS}）。")
        existing = self._store.get_entry("user", identity.key, tag_id)
        try:
            entry = self._store.upsert_entry(
                owner_kind="user",
                identity_key=identity.key,
                tag_id=tag_id,
                tag_display=tag_display,
                status=st,
                direction=dr,
                intensity=inten,
                note=existing.note if existing else "",
                source="self_declared",
                expected_revision=existing.revision if existing else None,
            )
        except StoreConflictError:
            return (
                "保存冲突：档案刚被其他操作修改，请先用 /xp show 查看最新状态后再试。"
            )
        except ValidationError as exc:
            return f"保存被拒绝（{exc}）。请检查取值范围。"
        return (
            f"已保存：{tag_display} → {STATUS_LABELS[st]}，"
            f"方向 {_DIR_LABELS[dr]}，强度 {inten}（rev {entry.revision}）。"
        )

    async def handle_remove(self, event: Any, tag: str) -> str:
        self._require_private(event)
        identity = await self._require_user_identity(event)
        tag_id, tag_display = self._resolve_tag(tag, create=False)
        existing = self._store.get_entry("user", identity.key, tag_id)
        if existing is None:
            return f"未找到条目「{tag_display or tag}」（未设置）。"
        try:
            removed = self._store.remove_entry(
                "user", identity.key, tag_id, expected_revision=existing.revision
            )
        except StoreConflictError:
            return "删除冲突：条目刚被修改，请重试。"
        return f"已删除条目「{existing.tag_display}」。" if removed else "未找到条目。"

    async def handle_clear(self, event: Any, code: str = "") -> str:
        self._require_private(event)
        identity = await self._require_user_identity(event)
        now = self._clock()
        pending = self._clear_tokens.get(identity.key)
        if not code:
            token = secrets.token_hex(3)  # 6 位
            self._clear_tokens[identity.key] = (token, now + CLEAR_TOKEN_TTL)
            return (
                "即将删除你在本插件中的全部偏好档案与缓存。\n"
                f"确认请发送：/xp clear {token}\n"
                f"（5 分钟内有效；仅删除你自己的数据）\n\n{CLEAR_TOKEN_TTL} 秒后失效。"
            )
        if pending is None or now > pending[1]:
            self._clear_tokens.pop(identity.key, None)
            return "没有待确认的删除请求或确认码已过期。请重新执行 /xp clear。"
        if not secrets.compare_digest(code.strip(), pending[0]):
            return "确认码不匹配。请重新执行 /xp clear 获取新码。"
        self._clear_tokens.pop(identity.key, None)
        deleted, epoch = self._store.clear_user(identity.key)
        return (
            f"已完成删除：清理了 {deleted} 条档案并停用注入（epoch={epoch}）。\n"
            f"{DELETION_BOUNDARY}"
        )

    # -- 管理员子命令 ------------------------------------------------------

    async def handle_admin_switch(self, event: Any, mode: str = "") -> str:
        self._require_admin(event)
        word = (mode or "").strip().lower()
        if word in ("on", "开", "开启"):
            self._config["admin_enabled"] = True
            return "管理员总开关已开启（用户仍需各自 /xp on）。"
        if word in ("off", "关", "关闭"):
            self._config["admin_enabled"] = False
            return "管理员总开关已关闭：所有用户的偏好读取与注入立即停止。"
        return "用法：/xp admin switch on|off"

    async def handle_admin_stats(self, event: Any) -> str:
        self._require_admin(event)
        return f"【脱敏统计】{self._store.stats()}"

    async def _require_bot_identity(self, event: Any) -> PrefIdentity:
        """管理员模板身份：当前机器人 + 当前人格（剥离 sender，bot 三元组）。"""

        identity = await self._resolve_identity(event)
        if identity is None:
            raise CommandError(
                "当前无法确认本轮生效人格，为避免模板串档已暂停操作，请稍后再试。"
            )
        from .identity import PrefIdentity as _PI

        return _PI(
            platform_id=identity.platform_id,
            self_id=identity.self_id,
            persona_scope=identity.persona_scope,
            sender_id="",
        )

    async def handle_admin_template_show(self, event: Any) -> str:
        self._require_admin(event)
        identity = await self._require_bot_identity(event)
        entries = self._store.list_entries("bot", identity.key)
        if not entries:
            return "当前人格的 Bot 模板为空。"
        lines = ["【Bot 人格模板（当前人格）】"]
        for e in entries:
            lines.append(
                f"- {e.tag_display or e.tag_id}：{STATUS_LABELS.get(e.status, e.status)}"
                f"｜强度 {e.intensity}｜rev {e.revision}"
            )
        return "\n".join(lines)

    async def handle_admin_template_set(
        self,
        event: Any,
        tag: str,
        status: str = "",
        direction: str = "",
        intensity: str = "",
    ) -> str:
        self._require_admin(event)
        identity = await self._require_bot_identity(event)
        tag_id, tag_display = self._resolve_tag(tag)
        st = parse_status(status)
        if st is None:
            raise CommandError(f"无法识别的状态「{status}」。")
        dr = parse_direction(direction) if direction else "both"
        if dr is None:
            raise CommandError(f"无法识别的方向「{direction}」。")
        inten = 3
        if intensity:
            try:
                inten = int(intensity)
            except ValueError as exc:
                raise CommandError("强度必须是 1-5 的整数。") from exc
        existing = self._store.get_entry("bot", identity.key, tag_id)
        try:
            entry = self._store.upsert_entry(
                owner_kind="bot",
                identity_key=identity.key,
                tag_id=tag_id,
                tag_display=tag_display,
                status=st,
                direction=dr,
                intensity=inten,
                note=existing.note if existing else "",
                source="admin_template",
                expected_revision=existing.revision if existing else None,
            )
        except StoreConflictError:
            return "保存冲突：模板刚被修改，请重试。"
        except ValidationError as exc:
            return f"保存被拒绝（{exc}）。"
        return f"模板已保存：{tag_display} → {STATUS_LABELS[st]}（rev {entry.revision}）。"

    async def handle_admin_template_remove(self, event: Any, tag: str) -> str:
        self._require_admin(event)
        identity = await self._require_bot_identity(event)
        tag_id, tag_display = self._resolve_tag(tag, create=False)
        existing = self._store.get_entry("bot", identity.key, tag_id)
        if existing is None:
            return "未找到该模板条目。"
        try:
            self._store.remove_entry(
                "bot", identity.key, tag_id, expected_revision=existing.revision
            )
        except StoreConflictError:
            return "删除冲突：模板刚被修改，请重试。"
        return f"已删除模板条目「{existing.tag_display}」。"

    async def handle_admin_template_clear(self, event: Any) -> str:
        self._require_admin(event)
        identity = await self._require_bot_identity(event)
        deleted = self._store.clear_bot_template(identity.key)
        return f"已清空当前人格模板（{deleted} 条）。用户档案不受影响。"

    # -- 标签解析 ------------------------------------------------------------

    def _resolve_tag(self, tag: str, create: bool = True) -> tuple[str, str]:
        """标签 → (tag_id, display)。内置中文名/英文 ID 均可；其余按自定义。"""

        raw = (tag or "").strip()
        if not raw:
            raise CommandError("标签不能为空。")
        # 内置（按中文显示名或英文 tag_id）
        for tid, display in BUILTIN_TAG_LABELS.items():
            if raw == display or raw.lower() == tid:
                return tid, display
        if not create:
            return raw, raw
        try:
            display = normalize_tag_display(raw)
        except ValidationError:
            raise CommandError(
                "自定义标签需为 2-16 个字符，且不含控制字符。"
            ) from None
        return make_tag_id(display), display


class _GroupOnly(Exception):
    """群聊调用私人命令：调用方应返回通用引导。"""


_DIR_LABELS = {"active": "主动", "receptive": "接受", "both": "双向"}
