"""Relation Arc 只读快照（ADR-005；返工 R3 修复）。

- 只读 URI 打开其 SQLite（mode=ro），绝不写入；
- **有效互动节奏对齐 Relation Arc 的 effective_interaction_safety 语义**：
  base = 该 scope 账户 state_json.interaction_safety 的值（管理员
  set_interaction_safety_admin 写入处），timed = timed_safety 未过期行，
  取两者中更高等级（normal < slow_down < pause_intimacy）。不调用其带
  过期清理副作用的方法，过期判定用纯 SELECT + expires_at 比较；
- 身份映射：arc_identity = f"{platform_id}:{sender_id}"（对齐其 _identity）；
- scope 双探测：session(umo) 与 global("") 各自计算有效节奏后取全局
  更严格值；accounts.paused 任一命中即按暂停处理；
- 表缺失/版本不明/任何异常 → RelationSnapshot.unavailable()（保守不生效）；
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

_SAFETY_RANK = {RHYTHM_NORMAL: 0, RHYTHM_SLOW: 1, RHYTHM_PAUSE: 2}
_VALID_SAFETY = ("normal", "slow_down", "pause_intimacy")


def _rank(level: str) -> int:
    return _SAFETY_RANK.get(level, 0)


class RelationSnapshotReader:
    def __init__(self, data_root: Path):
        self._db_path = Path(data_root) / "plugin_data" / ARC_PLUGIN_DIR / ARC_DB_NAME

    @property
    def db_path(self) -> Path:
        return self._db_path

    async def load(self, identity: PrefIdentity, umo: str) -> RelationSnapshot:
        try:
            return self._load_sync(identity, umo)
        except Exception:  # noqa: BLE001 - 任何读取异常均保守不可用
            return RelationSnapshot.unavailable()

    def _load_sync(self, identity: PrefIdentity, umo: str) -> RelationSnapshot:
        if not self._db_path.exists():
            return RelationSnapshot.unavailable()
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

            arc_identity = f"{identity.platform_id}:{identity.sender_id}"
            scopes = [("session", str(umo)), ("global", "")]
            paused = False
            effective = RHYTHM_NORMAL
            state_keys: list[str] = []
            found = False
            now = time.time()
            for scope_kind, scope_id in scopes:
                row = conn.execute(
                    "SELECT paused, state_json FROM accounts "
                    "WHERE identity=? AND scope_kind=? AND scope_id=?",
                    (arc_identity, scope_kind, scope_id),
                ).fetchone()
                if row is None:
                    continue
                found = True
                paused = paused or bool(row["paused"])
                try:
                    state = json.loads(row["state_json"] or "{}")
                    if isinstance(state, dict):
                        state_keys.extend(sorted(k for k in state.keys()))
                        # R3：读取管理员设置的 interaction_safety 实际值
                        base = state.get("interaction_safety")
                        if base in _VALID_SAFETY and _rank(base) > _rank(effective):
                            effective = base
                except (ValueError, TypeError):
                    pass
                # timed_safety：纯 SELECT 过期判定（不调用带 DELETE 副作用的
                # active_timed_safety；等级仅可为 slow_down/pause_intimacy）
                ts = conn.execute(
                    "SELECT level, expires_at FROM timed_safety "
                    "WHERE identity=? AND scope_kind=? AND scope_id=?",
                    (arc_identity, scope_kind, scope_id),
                ).fetchone()
                if ts is not None and float(ts["expires_at"] or 0) > now:
                    level = ts["level"]
                    if level in _VALID_SAFETY and _rank(level) > _rank(effective):
                        effective = level
            if not found:
                # 无账户行：关系数据不存在 → 不可用（保守不生效，不虚构）
                return RelationSnapshot.unavailable()
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
