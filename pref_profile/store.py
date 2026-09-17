"""SQLite 持久化（ADR-006）。

- 插件数据目录单文件，WAL；autocommit + 显式 BEGIN IMMEDIATE 事务。
- 参数化查询；条目写入走乐观锁（revision 匹配才更新）。
- 日志不落档案原文（只记 tag_id / 长度 / 原因码）。
- epoch 语义：off/clear 递增，在途请求的 epoch 快照不匹配即失效。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .model import (
    Entry,
    ValidationError,
    validate_direction,
    validate_intensity,
    validate_owner_kind,
    validate_status,
)

SCHEMA_VERSION = 1


class StoreConflictError(RuntimeError):
    """并发写入冲突（revision 不匹配或条目已被并发删除）。"""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind  # "revision_mismatch" | "entry_deleted"


class PrefStore:
    def __init__(self, db_path: Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path), timeout=10, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    # -- schema -----------------------------------------------------------

    def _ensure_schema(self) -> None:
        # executescript 自带 BEGIN IMMEDIATE .. COMMIT，外层不再包事务
        with self._lock:
            self._conn.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('user','bot')),
                    identity_key TEXT NOT NULL,
                    tag_id TEXT NOT NULL,
                    tag_display TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('like','neutral','dislike','forbidden')),
                    direction TEXT NOT NULL DEFAULT 'both' CHECK(direction IN ('active','receptive','both')),
                    intensity INTEGER NOT NULL DEFAULT 3 CHECK(intensity BETWEEN 1 AND 5),
                    note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL CHECK(source IN ('self_declared','admin_template')),
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL,
                    UNIQUE(owner_kind, identity_key, tag_id)
                );
                CREATE TABLE IF NOT EXISTS user_state (
                    identity_key TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    epoch INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');
                COMMIT;
                """
            )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass

    # -- entries ----------------------------------------------------------

    def upsert_entry(
        self,
        *,
        owner_kind: str,
        identity_key: str,
        tag_id: str,
        tag_display: str,
        status: str,
        direction: str = "both",
        intensity: int = 3,
        note: str = "",
        source: str,
        expected_revision: int | None = None,
    ) -> Entry:
        """写入条目。

        expected_revision=None 仅用于新建（要求条目不存在，存在则冲突）；
        更新已有条目必须携带读取时的 revision，不匹配抛 StoreConflictError。
        """

        validate_owner_kind(owner_kind)
        validate_status(status)
        validate_direction(direction)
        validate_intensity(intensity)
        if not tag_id or len(tag_id) > 64:
            raise ValidationError(f"tag_id_invalid:{len(tag_id)}")
        if len(identity_key) > 512:
            raise ValidationError("identity_key_too_long")

        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT revision FROM entries WHERE owner_kind=? AND identity_key=? AND tag_id=?",
                    (owner_kind, identity_key, tag_id),
                ).fetchone()
                if row is None:
                    if expected_revision is not None:
                        self._conn.execute("ROLLBACK")
                        raise StoreConflictError("entry_deleted")
                    entry = Entry(
                        owner_kind=owner_kind,
                        identity_key=identity_key,
                        tag_id=tag_id,
                        tag_display=tag_display,
                        status=status,
                        direction=direction,
                        intensity=intensity,
                        note=note,
                        source=source,
                        revision=1,
                        updated_at=now,
                    )
                    self._conn.execute(
                        """INSERT INTO entries
                           (owner_kind, identity_key, tag_id, tag_display, status,
                            direction, intensity, note, source, revision, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            owner_kind, identity_key, tag_id, tag_display, status,
                            direction, intensity, note, source, 1, now,
                        ),
                    )
                else:
                    current = int(row["revision"])
                    if expected_revision is None or expected_revision != current:
                        self._conn.execute("ROLLBACK")
                        raise StoreConflictError("revision_mismatch")
                    self._conn.execute(
                        """UPDATE entries SET tag_display=?, status=?, direction=?,
                           intensity=?, note=?, source=?, revision=revision+1, updated_at=?
                           WHERE owner_kind=? AND identity_key=? AND tag_id=? AND revision=?""",
                        (
                            tag_display, status, direction, intensity, note, source,
                            now, owner_kind, identity_key, tag_id, current,
                        ),
                    )
                    entry = Entry(
                        owner_kind=owner_kind,
                        identity_key=identity_key,
                        tag_id=tag_id,
                        tag_display=tag_display,
                        status=status,
                        direction=direction,
                        intensity=intensity,
                        note=note,
                        source=source,
                        revision=current + 1,
                        updated_at=now,
                    )
                self._conn.execute("COMMIT")
                return entry
            except StoreConflictError:
                raise
            except sqlite3.IntegrityError as exc:
                self._conn.execute("ROLLBACK")
                raise ValidationError(f"constraint:{exc}") from exc
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
                raise

    def get_entry(
        self, owner_kind: str, identity_key: str, tag_id: str
    ) -> Optional[Entry]:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE owner_kind=? AND identity_key=? AND tag_id=?",
            (owner_kind, identity_key, tag_id),
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def list_entries(self, owner_kind: str, identity_key: str) -> list[Entry]:
        rows = self._conn.execute(
            "SELECT * FROM entries WHERE owner_kind=? AND identity_key=? ORDER BY tag_id",
            (owner_kind, identity_key),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count_entries(self, owner_kind: str, identity_key: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM entries WHERE owner_kind=? AND identity_key=?",
            (owner_kind, identity_key),
        ).fetchone()
        return int(row["c"])

    def remove_entry(
        self,
        owner_kind: str,
        identity_key: str,
        tag_id: str,
        expected_revision: int,
    ) -> bool:
        """删除条目；revision 不匹配抛 StoreConflictError，返回是否删除。"""

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "DELETE FROM entries WHERE owner_kind=? AND identity_key=? AND tag_id=? AND revision=?",
                    (owner_kind, identity_key, tag_id, expected_revision),
                )
                if cur.rowcount == 0:
                    existed = self._conn.execute(
                        "SELECT 1 FROM entries WHERE owner_kind=? AND identity_key=? AND tag_id=?",
                        (owner_kind, identity_key, tag_id),
                    ).fetchone()
                    self._conn.execute("ROLLBACK")
                    if existed:
                        raise StoreConflictError("revision_mismatch")
                    return False
                self._conn.execute("COMMIT")
                return True
            except StoreConflictError:
                raise
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
                raise

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> Entry:
        return Entry(
            owner_kind=row["owner_kind"],
            identity_key=row["identity_key"],
            tag_id=row["tag_id"],
            tag_display=row["tag_display"],
            status=row["status"],
            direction=row["direction"],
            intensity=int(row["intensity"]),
            note=row["note"],
            source=row["source"],
            revision=int(row["revision"]),
            updated_at=float(row["updated_at"]),
        )

    # -- user_state ---------------------------------------------------------

    def get_user_state(self, identity_key: str) -> tuple[bool, int]:
        row = self._conn.execute(
            "SELECT enabled, epoch FROM user_state WHERE identity_key=?",
            (identity_key,),
        ).fetchone()
        if row is None:
            return False, 0
        return bool(row["enabled"]), int(row["epoch"])

    def set_user_enabled(self, identity_key: str, enabled: bool) -> int:
        """设置本人开关；任何切换都递增 epoch（在途旧请求全部失效）。"""

        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT epoch FROM user_state WHERE identity_key=?", (identity_key,)
            ).fetchone()
            new_epoch = (int(row["epoch"]) + 1) if row else 1
            self._conn.execute(
                """INSERT INTO user_state(identity_key, enabled, epoch, updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(identity_key) DO UPDATE SET
                     enabled=excluded.enabled, epoch=excluded.epoch,
                     updated_at=excluded.updated_at""",
                (identity_key, 1 if enabled else 0, new_epoch, now),
            )
            self._conn.execute("COMMIT")
            return new_epoch

    def clear_user(self, identity_key: str) -> tuple[int, int]:
        """删除该身份全部条目并递增 epoch。返回 (删除条数, 新 epoch)。"""

        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            cur = self._conn.execute(
                "DELETE FROM entries WHERE owner_kind='user' AND identity_key=?",
                (identity_key,),
            )
            deleted = cur.rowcount
            row = self._conn.execute(
                "SELECT epoch FROM user_state WHERE identity_key=?", (identity_key,)
            ).fetchone()
            new_epoch = (int(row["epoch"]) + 1) if row else 1
            self._conn.execute(
                """INSERT INTO user_state(identity_key, enabled, epoch, updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(identity_key) DO UPDATE SET
                     enabled=0, epoch=excluded.epoch, updated_at=excluded.updated_at""",
                (identity_key, 0, new_epoch, now),
            )
            self._conn.execute("COMMIT")
            return deleted, new_epoch

    def clear_bot_template(self, persona_key: str) -> int:
        """删除某 Bot 人格模板全部条目（管理员）。"""

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            cur = self._conn.execute(
                "DELETE FROM entries WHERE owner_kind='bot' AND identity_key=?",
                (persona_key,),
            )
            self._conn.execute("COMMIT")
            return cur.rowcount

    # -- 统计（脱敏） --------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """脱敏统计：仅计数，不含任何标签/备注/身份原文。"""

        row = self._conn.execute(
            "SELECT COUNT(*) AS entries FROM entries"
        ).fetchone()
        state = self._conn.execute(
            "SELECT COUNT(*) AS users, COALESCE(SUM(enabled),0) AS enabled FROM user_state"
        ).fetchone()
        return {
            "entries": int(row["entries"]),
            "users": int(state["users"]),
            "enabled_users": int(state["enabled"]),
        }
