"""P1 身份/档案/持久化验证（MIS-147）。

纯存储层与身份层断言（脱网、合成数据、临时目录）。覆盖：
  I1  身份四元组隔离：同名异人 / 同号异机器人 / 人格切换 / 跨平台，
      键互不相同且互不可见对方条目。
  I2  persona_scope 解析：宿主异常 → None（跳过）；正常返回透传；
      build_identity 拒绝空人格；键 round-trip。
  E1  未设置(None) 与显式 neutral 严格区分。
  E2  输入边界：非法 status/direction/intensity、note 超长、tag_display
      含控制字符（含 \x1f 身份分隔符）或超长 → ValidationError。
  E3  user/bot 档案分离；互不串。
  E4  重启持久化：重开 PrefStore 数据仍在。
  E5  并发：双连接乐观锁，旧 revision 更新/删除 → StoreConflictError，
      无静默覆盖；重复新建 → 冲突。
  E6  epoch 语义：on/off 递增 epoch；clear 删除条目、置 disabled、递增。
  E7  脱敏统计不含原文。

运行：python tests/p1_store_check.py（任一本机 Python ≥3.10）
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pref_profile.identity import (  # noqa: E402
    build_identity,
    identity_from_key,
    resolve_persona_scope,
)
from pref_profile.model import (  # noqa: E402
    ValidationError,
    make_tag_id,
    normalize_note,
    normalize_tag_display,
)
from pref_profile.store import PrefStore, StoreConflictError  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


def expect_error(name: str, fn, err_type, contains: str = "") -> None:
    try:
        fn()
    except err_type as e:
        ok = (not contains) or contains in str(e)
        check(name, ok, f"err={e!r}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"wrong error type: {e!r}")
    else:
        check(name, False, "no error raised")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pref_p1_"))
    db = tmp / "pref.db"
    try:
        store = PrefStore(db)

        # -- I1 身份隔离 ---------------------------------------------------
        u_same_sender_p1 = build_identity(
            platform_id="aiocqhttp", self_id="bot1",
            persona_scope="persona_A", sender_id="10001",
        )
        u_same_sender_p2 = build_identity(
            platform_id="aiocqhttp", self_id="bot1",
            persona_scope="persona_B", sender_id="10001",
        )
        u_other_self = build_identity(
            platform_id="aiocqhttp", self_id="bot2",
            persona_scope="persona_A", sender_id="10001",
        )
        u_other_platform = build_identity(
            platform_id="telegram", self_id="bot1",
            persona_scope="persona_A", sender_id="10001",
        )
        u_other_sender = build_identity(
            platform_id="aiocqhttp", self_id="bot1",
            persona_scope="persona_A", sender_id="10002",
        )
        keys = {
            u_same_sender_p1.key, u_same_sender_p2.key, u_other_self.key,
            u_other_platform.key, u_other_sender.key,
        }
        check("I1a 五种身份键互不相同", len(keys) == 5, f"keys={len(keys)}")

        store.upsert_entry(
            owner_kind="user", identity_key=u_same_sender_p1.key,
            tag_id="topic", tag_display="话题偏好", status="like",
            source="self_declared",
        )
        check(
            "I1b 人格切换后读不到另一人格条目",
            store.get_entry("user", u_same_sender_p2.key, "topic") is None
            and store.get_entry("user", u_other_self.key, "topic") is None
            and store.get_entry("user", u_other_platform.key, "topic") is None
            and store.get_entry("user", u_other_sender.key, "topic") is None,
        )
        check(
            "I1c 原身份可读到",
            store.get_entry("user", u_same_sender_p1.key, "topic") is not None,
        )

        # -- I2 persona 解析 ------------------------------------------------
        async def _resolve(mgr, conversation=None):
            event = SimpleNamespace(
                unified_msg_origin="aiocqhttp:FriendMessage:10001",
                get_platform_name=lambda: "aiocqhttp",
            )
            return await resolve_persona_scope(mgr, event, conversation)

        async def ok_resolve(**kw):
            return ("persona_A", {}, None, False)

        async def bad_resolve(**kw):
            raise RuntimeError("host down")

        async def empty_resolve(**kw):
            return (None, None, None, False)

        ok_mgr = SimpleNamespace(resolve_selected_persona=ok_resolve)
        bad_mgr = SimpleNamespace(resolve_selected_persona=bad_resolve)
        empty_mgr = SimpleNamespace(resolve_selected_persona=empty_resolve)
        check(
            "I2a 正常解析透传",
            asyncio.run(_resolve(ok_mgr)) == "persona_A",
        )
        check(
            "I2b 宿主异常返回 None",
            asyncio.run(_resolve(bad_mgr)) is None,
        )
        check(
            "I2c 空人格返回 None（不落默认）",
            asyncio.run(_resolve(empty_mgr)) is None,
        )
        expect_error(
            "I2d build_identity 拒绝空 persona",
            lambda: build_identity(
                platform_id="p", self_id="b", persona_scope="", sender_id="u"
            ),
            ValueError,
        )
        check(
            "I2e 键 round-trip",
            identity_from_key(u_same_sender_p1.key) == u_same_sender_p1,
        )

        # -- E1 未设置 vs 中立 ----------------------------------------------
        check("E1a 无条目=未设置(None)", store.get_entry("user", u_other_sender.key, "topic") is None)
        store.upsert_entry(
            owner_kind="user", identity_key=u_other_sender.key,
            tag_id="topic", tag_display="话题偏好", status="neutral",
            source="self_declared",
        )
        neutral = store.get_entry("user", u_other_sender.key, "topic")
        check("E1b 显式 neutral 是独立条目", neutral is not None and neutral.status == "neutral")

        # -- E2 输入边界 -----------------------------------------------------
        expect_error(
            "E2a 非法 status 被拒",
            lambda: store.upsert_entry(
                owner_kind="user", identity_key=u_other_sender.key,
                tag_id="t1", tag_display="测试", status="super_like",
                source="self_declared",
            ),
            ValidationError, "status_invalid",
        )
        expect_error(
            "E2b 非法 direction 被拒",
            lambda: store.upsert_entry(
                owner_kind="user", identity_key=u_other_sender.key,
                tag_id="t1", tag_display="测试", status="like",
                direction="both_ways", source="self_declared",
            ),
            ValidationError, "direction_invalid",
        )
        expect_error(
            "E2c 强度越界被拒（6）",
            lambda: store.upsert_entry(
                owner_kind="user", identity_key=u_other_sender.key,
                tag_id="t1", tag_display="测试", status="like", intensity=6,
                source="self_declared",
            ),
            ValidationError, "intensity_range",
        )
        expect_error(
            "E2d 强度越界被拒（0）",
            lambda: store.upsert_entry(
                owner_kind="user", identity_key=u_other_sender.key,
                tag_id="t1", tag_display="测试", status="like", intensity=0,
                source="self_declared",
            ),
            ValidationError, "intensity_range",
        )
        expect_error(
            "E2e note 超长被拒",
            lambda: normalize_note("x" * 201),
            ValidationError, "note_length",
        )
        stripped = normalize_tag_display("a\x1fb")
        check(
            "E2f tag_display 的 \\x1f 身份分隔符被剥离",
            stripped == "ab" and "\x1f" not in stripped,
            f"{stripped!r}",
        )
        expect_error(
            "E2g tag_display 超长被拒",
            lambda: normalize_tag_display("标" * 17),
            ValidationError, "tag_display_length",
        )
        cleaned = normalize_tag_display("  正常标签\x00去控  ")
        check("E2h 控制字符被剥离", "\x00" not in cleaned and cleaned == "正常标签去控", f"{cleaned!r}")
        tid = make_tag_id("自定义维度")
        check("E2i 自定义 tag_id 稳定", tid.startswith("custom_") and make_tag_id("自定义维度") == tid)

        # -- E3 user/bot 分离 ------------------------------------------------
        bot_key = build_identity(
            platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
        ).key
        check("E3a bot 身份 owner_kind", build_identity(
            platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
        ).owner_kind == "bot")
        store.upsert_entry(
            owner_kind="bot", identity_key=bot_key, tag_id="tone",
            tag_display="语气风格", status="like", source="admin_template",
        )
        check(
            "E3b 模板与用户档案互不可见",
            store.get_entry("bot", u_same_sender_p1.key, "tone") is None
            and store.get_entry("user", bot_key, "topic") is None
            and store.list_entries("user", bot_key) == [],
        )
        expect_error(
            "E3c 非法 owner_kind 被拒",
            lambda: store.upsert_entry(
                owner_kind="admin", identity_key=bot_key, tag_id="tone",
                tag_display="语气", status="like", source="admin_template",
            ),
            ValidationError, "owner_kind_invalid",
        )

        # -- E5 并发乐观锁（先于重启测试，双连接） ----------------------------
        e1 = store.upsert_entry(
            owner_kind="user", identity_key=u_same_sender_p1.key,
            tag_id="joke", tag_display="玩笑尺度", status="dislike",
            source="self_declared",
        )
        store2 = PrefStore(db)  # 第二连接
        store2.upsert_entry(
            owner_kind="user", identity_key=u_same_sender_p1.key,
            tag_id="joke", tag_display="玩笑尺度", status="neutral",
            expected_revision=e1.revision, source="self_declared",
        )
        try:
            store.upsert_entry(
                owner_kind="user", identity_key=u_same_sender_p1.key,
                tag_id="joke", tag_display="玩笑尺度", status="forbidden",
                expected_revision=e1.revision, source="self_declared",
            )
            check("E5a 旧 revision 更新被拒", False, "no conflict raised")
        except StoreConflictError as exc:
            check("E5a 旧 revision 更新被拒", exc.kind == "revision_mismatch", f"kind={exc.kind}")
        cur = store.get_entry("user", u_same_sender_p1.key, "joke")
        check("E5b 无静默覆盖（仍是 neutral，revision=2）",
              cur.status == "neutral" and cur.revision == 2,
              f"status={cur.status}, rev={cur.revision}")
        try:
            store.remove_entry("user", u_same_sender_p1.key, "joke", expected_revision=e1.revision)
            check("E5c 旧 revision 删除被拒", False)
        except StoreConflictError as exc:
            check("E5c 旧 revision 删除被拒", exc.kind == "revision_mismatch")
        try:
            store.upsert_entry(
                owner_kind="user", identity_key=u_same_sender_p1.key,
                tag_id="joke", tag_display="玩笑尺度", status="like",
                source="self_declared",
            )
            check("E5d 已存在条目重复新建被拒", False)
        except StoreConflictError as exc:
            check("E5d 已存在条目重复新建被拒", exc.kind == "revision_mismatch")
        check(
            "E5e 正确 revision 删除成功",
            store.remove_entry("user", u_same_sender_p1.key, "joke",
                               expected_revision=cur.revision) is True,
        )

        # -- E6 epoch ---------------------------------------------------------
        _, ep0 = store.get_user_state(u_same_sender_p1.key)
        check("E6a 未开启时 epoch=0", ep0 == 0)
        ep_on = store.set_user_enabled(u_same_sender_p1.key, True)
        ep_off = store.set_user_enabled(u_same_sender_p1.key, False)
        check("E6b on/off 各递增", ep_on == 1 and ep_off == 2, f"{ep_on},{ep_off}")
        store.set_user_enabled(u_same_sender_p1.key, True)
        deleted, ep_clear = store.clear_user(u_same_sender_p1.key)
        enabled_after, _ = store.get_user_state(u_same_sender_p1.key)
        check(
            "E6c clear 删除条目+禁用+递增",
            deleted == 1 and ep_clear == 4 and enabled_after is False
            and store.list_entries("user", u_same_sender_p1.key) == [],
            f"deleted={deleted}, ep={ep_clear}, enabled={enabled_after}",
        )

        # -- E4 重启持久化 ----------------------------------------------------
        store.close()
        store2.close()
        store3 = PrefStore(db)
        check(
            "E4a 重启后条目仍在",
            store3.get_entry("user", u_other_sender.key, "topic") is not None
            and store3.get_entry("bot", bot_key, "tone") is not None,
        )
        _, ep_persist = store3.get_user_state(u_same_sender_p1.key)
        check("E4b 重启后 epoch 保留", ep_persist == ep_clear, f"{ep_persist}")

        # -- E7 脱敏统计 ------------------------------------------------------
        st = store3.stats()
        check(
            "E7a 统计仅计数",
            set(st.keys()) == {"entries", "users", "enabled_users"}
            and st["entries"] == 2,
            f"st={st}",
        )

        store3.close()
    except Exception:
        traceback.print_exc()
        check("P1 未预期异常", False, traceback.format_exc(limit=2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
