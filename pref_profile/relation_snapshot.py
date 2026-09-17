"""Relation Arc 只读快照（ADR-005）。

- 只读 URI 打开其 SQLite（mode=ro），绝不写入；
- 身份映射：arc_identity = f"{platform_id}:{sender_id}"（对齐其 _identity）；
- scope 双探测：session(umo) 与 global("")，按其 _effective_state 近似语义
  合并 paused 与 timed_safety 最高有效级；
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
            rhythm = RHYTHM_NORMAL
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
                except (ValueError, TypeError):
                    pass
                ts = conn.execute(
                    "SELECT level, expires_at FROM timed_safety "
                    "WHERE identity=? AND scope_kind=? AND scope_id=?",
                    (arc_identity, scope_kind, scope_id),
                ).fetchone()
                if ts is not None and float(ts["expires_at"] or 0) > now:
                    level = ts["level"]
                    if _SAFETY_RANK.get(level, 0) > _SAFETY_RANK.get(rhythm, 0):
                        rhythm = level
            if not found:
                # 无账户行：关系数据不存在 ≠ 关系正常；仅 timed_safety 也可能
                # 单独存在（理论上不会），这里以无数据 → 不可用（保守不生效）
                return RelationSnapshot.unavailable()
            if paused:
                rhythm = RHYTHM_PAUSE  # 账户级暂停按最严格节奏处理
            return RelationSnapshot(
                available=True,
                paused=paused,
                interaction_rhythm=rhythm,
                state_labels=tuple(dict.fromkeys(state_keys)),
            )
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
