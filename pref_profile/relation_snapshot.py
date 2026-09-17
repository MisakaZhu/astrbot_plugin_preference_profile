"""Relation Arc 只读快照（ADR-005；二轮返工 R3 修复）。

- 只读 URI 打开其 SQLite（mode=ro），绝不写入；
- **读取真实生效 scope**：解析其 PluginConfigManager 同路径下的
  config.json（data_root/plugin_data/astrbot_plugin_relation_arc/
  config.json），按 is_global_relation 只选 global 或 session——
  不再合并两个 scope（未启用范围的旧记录不混入）；config_version
  超出已知版本（>7）或文件/字段异常 → 保守不可用；
- **schema 版本校验**：PRAGMA user_version 必须等于已知支持版本
  （Relation Arc SCHEMA_VERSION=12），否则按未知 schema 降级；
- 有效互动节奏对齐 effective_interaction_safety：base =
  state_json.interaction_safety 值（管理员 set_interaction_safety_admin
  写入处），timed = timed_safety 未过期行，取更高等级；过期判定用
  纯 SELECT + expires_at 比较（不调用带 DELETE 副作用的方法）；
- 身份映射：arc_identity = f"{platform_id}:{sender_id}"；
- 表缺失/任何异常 → RelationSnapshot.unavailable()（保守不生效）；
- state_labels 只取 state_json 键名（脱敏），不取中文值、不进日志。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .identity import PrefIdentity
from .policy import (
    RHYTHM_NORMAL,
    RHYTHM_PAUSE,
    RHYTHM_SLOW,
    RelationSnapshot,
)

ARC_PLUGIN_DIR = "astrbot_plugin_relation_arc"
ARC_DB_NAME = "relation_arc.sqlite3"
ARC_CONFIG_NAME = "config.json"

# 已知支持的协作版本（Relation Arc 913ca59）：
SCHEMA_VERSIONS = {12}          # relation_store.SCHEMA_VERSION
CONFIG_VERSION_MAX = 7          # config_manager.CONFIG_VERSION

_SAFETY_RANK = {RHYTHM_NORMAL: 0, RHYTHM_SLOW: 1, RHYTHM_PAUSE: 2}
_VALID_SAFETY = ("normal", "slow_down", "pause_intimacy")


def _rank(level: str) -> int:
    return _SAFETY_RANK.get(level, 0)


class RelationSnapshotReader:
    def __init__(self, data_root: Path):
        self._dir = Path(data_root) / "plugin_data" / ARC_PLUGIN_DIR
        self._db_path = self._dir / ARC_DB_NAME

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _resolve_scope(self, umo: str) -> tuple[str, str] | None:
        """读取真实生效 scope（session umo / global ""）。

        返回 None 表示配置无法确认（缺文件/解析失败/未知版本）→ 调用方
        按合同降级为不可用，不猜测 scope。
        """

        config_path = self._dir / ARC_CONFIG_NAME
        if not config_path.exists():
            return None
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            version = raw.get("config_version")
            if type(version) is not int or version > CONFIG_VERSION_MAX:
                return None
            if raw.get("is_global_relation", True) is True:
                return ("global", "")
            return ("session", str(umo))
        except Exception:  # noqa: BLE001 - 配置不可读 → 无法确认
            return None

    async def load(self, identity: PrefIdentity, umo: str) -> RelationSnapshot:
        try:
            return self._load_sync(identity, umo)
        except Exception:  # noqa: BLE001 - 任何读取异常均保守不可用
            return RelationSnapshot.unavailable()

    def _load_sync(self, identity: PrefIdentity, umo: str) -> RelationSnapshot:
        if not self._db_path.exists():
            return RelationSnapshot.unavailable()
        scope = self._resolve_scope(umo)
        if scope is None:
            return RelationSnapshot.unavailable()
        scope_kind, scope_id = scope
        uri = f"file:{self._db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "accounts" not in tables or "timed_safety" not in tables:
                return RelationSnapshot.unavailable()
            # schema 版本：Relation Arc 以 PRAGMA user_version 标记库版本
            row = conn.execute("PRAGMA user_version").fetchone()
            user_version = int(row[0]) if row else 0
            if user_version not in SCHEMA_VERSIONS:
                return RelationSnapshot.unavailable()

            arc_identity = f"{identity.platform_id}:{identity.sender_id}"
            account = conn.execute(
                "SELECT paused, state_json FROM accounts "
                "WHERE identity=? AND scope_kind=? AND scope_id=?",
                (arc_identity, scope_kind, scope_id),
            ).fetchone()
            if account is None:
                # 当前生效 scope 无该身份数据 → 不可用（保守不虚构）
                return RelationSnapshot.unavailable()
            paused = bool(account["paused"])
            effective = RHYTHM_NORMAL
            state_keys: list[str] = []
            try:
                state = json.loads(account["state_json"] or "{}")
                if isinstance(state, dict):
                    state_keys.extend(sorted(k for k in state.keys()))
                    base = state.get("interaction_safety")
                    if base in _VALID_SAFETY:
                        effective = base
            except (ValueError, TypeError):
                pass
            ts = conn.execute(
                "SELECT level, expires_at FROM timed_safety "
                "WHERE identity=? AND scope_kind=? AND scope_id=?",
                (arc_identity, scope_kind, scope_id),
            ).fetchone()
            now = time.time()
            if ts is not None and float(ts["expires_at"] or 0) > now:
                level = ts["level"]
                if level in _VALID_SAFETY and _rank(level) > _rank(effective):
                    effective = level
            if paused:
                effective = RHYTHM_PAUSE  # 账户级暂停按最严格节奏处理
            return RelationSnapshot(
                available=True,
                paused=paused,
                interaction_rhythm=effective,
                state_labels=tuple(dict.fromkeys(state_keys)),
            )
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
