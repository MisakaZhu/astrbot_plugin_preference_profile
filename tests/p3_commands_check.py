"""P3 命令、权限与关闭/删除生命周期验证（MIS-149）。

duck-typed 事件脱网测试命令层语义（真实宿主命令解析链在 P6 组合回归）。
覆盖群聊纪律、权限边界、clear 确认流、epoch 失效、模板/用户分离、
身份解析失败保守路径、删除边界如实告知。
运行：python tests/p3_commands_check.py
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

from pref_profile.commands import GROUP_HINT, CommandService  # noqa: E402
from pref_profile.identity import build_identity  # noqa: E402
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


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_event(sender="10001", group="", role="member"):
    return SimpleNamespace(
        is_private_chat=not bool(group),
        get_sender_id=lambda: sender,
        get_platform_id=lambda: "aiocqhttp",
        get_self_id=lambda: "bot1",
        get_group_id=lambda: group,
        unified_msg_origin=f"aiocqhttp:{'GroupMessage' if group else 'FriendMessage'}:{group or sender}",
        role=role,
    )


IDENTITY = build_identity(
    platform_id="aiocqhttp", self_id="bot1",
    persona_scope="persona_A", sender_id="10001",
)


async def resolver(event):
    # 模拟宿主成功解析：同 sender → 同身份
    return build_identity(
        platform_id="aiocqhttp", self_id="bot1",
        persona_scope="persona_A", sender_id=str(event.get_sender_id()),
    )


async def null_resolver(event):
    return None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pref_p3_"))
    loop = asyncio.new_event_loop()
    try:
        store = PrefStore(tmp / "p3.db")
        config = {"admin_enabled": False}
        clock = FakeClock()
        svc = CommandService(store, config, resolver, clock=clock)
        ev = make_event()
        grp = make_event(group="g1")

        # -- 群聊纪律 -------------------------------------------------------
        def run(coro):
            return loop.run_until_complete(coro)

        def run_group(coro):
            """群聊调用私人命令：_GroupOnly 等价于 main.py 层的 GROUP_HINT。"""
            try:
                return run(coro)
            except Exception as exc:  # noqa: BLE001
                return type(exc).__name__

        group_results = {
            "status": run_group(svc.handle_status(grp)),
            "on": run_group(svc.handle_on(grp)),
            "show": run_group(svc.handle_show(grp)),
            "set": run_group(svc.handle_set(grp, "玩笑尺度", "禁止")),
            "clear": run_group(svc.handle_clear(grp)),
        }
        check(
            "C01 群聊私人命令一律拒绝（_GroupOnly → 通用引导）",
            all(r == "_GroupOnly" for r in group_results.values()),
            f"{group_results}",
        )
        group_help = run_group(svc.handle_help(grp))
        check(
            "C02 群聊 help 也只给通用引导",
            group_help == GROUP_HINT,
        )

        # -- 默认关闭 -------------------------------------------------------
        text = run(svc.handle_status(ev))
        check(
            "C03 默认状态：总开关关+本人关",
            "总开关：关" in text and "本人开关：关" in text,
            f"{text!r}",
        )

        # -- on/off 与 epoch ------------------------------------------------
        text = run(svc.handle_on(ev))
        enabled, ep1 = store.get_user_state(IDENTITY.key)
        check(
            "C04 on 记录开关并提示总开关未开",
            enabled is True and ep1 == 1 and "总开关为关闭" in text,
        )
        config["admin_enabled"] = True
        text = run(svc.handle_on(ev))
        _, ep2 = store.get_user_state(IDENTITY.key)
        check("C05 每次 on 递增 epoch", ep2 == 2 and "已为你开启" in text)
        text = run(svc.handle_off(ev))
        enabled3, ep3 = store.get_user_state(IDENTITY.key)
        check(
            "C06 off 立即禁用+epoch 递增+提示在途失效",
            enabled3 is False and ep3 == 3 and "在途请求" in text,
        )
        run(svc.handle_on(ev))  # 恢复开启用于后续

        # -- set/show 流程 ----------------------------------------------------
        text = run(svc.handle_set(ev, "玩笑尺度", "喜欢", "主动", "4"))
        e = store.get_entry("user", IDENTITY.key, "joke")
        check(
            "C07 set 内置标签中文全流程",
            "已保存" in text and e is not None and e.status == "like"
            and e.direction == "active" and e.intensity == 4,
        )
        text = run(svc.handle_set(ev, "深夜话题", "禁止"))
        e2 = store.get_entry("user", IDENTITY.key, store is not None and _find_custom(store, IDENTITY, "深夜话题"))
        check("C08 set 自定义标签禁止", "已保存" in text and e2 is not None and e2.status == "forbidden")

        try:
            run(svc.handle_set(ev, "玩笑尺度", "超爱"))
            check("C09 非法状态被拒", False)
        except Exception as exc:  # noqa: BLE001
            check("C09 非法状态被拒", "无法识别的状态" in str(exc))
        text = run(svc.handle_set(ev, "玩笑尺度", "喜欢", "双向", "9"))
        check("C10 非法强度被拒", "保存被拒绝" in text, f"{text!r}")

        text = run(svc.handle_show(ev))
        check(
            "C11 show 区分条目与状态",
            "玩笑尺度" in text and "深夜话题" in text and "明确禁止" in text,
        )
        text = run(svc.handle_show(make_event(sender="99999")))
        check(
            "C12 show 只看自己的档案（他人为空=未设置说明）",
            "档案为空" in text and "未设置" in text,
        )

        # -- clear 确认流 -----------------------------------------------------
        text = run(svc.handle_clear(ev))
        check("C13 clear 发码且 5 分钟有效提示", "/xp clear " in text)
        token = text.split("/xp clear ")[1].split("\n")[0].strip()
        text = run(svc.handle_clear(ev, "wrongcode"))
        check("C14 错码拒绝", "不匹配" in text)
        clock.now += 301
        text = run(svc.handle_clear(ev, token))
        check("C15 过期拒绝", "过期" in text)
        text = run(svc.handle_clear(ev))
        token = text.split("/xp clear ")[1].split("\n")[0].strip()
        clock.now += 10
        text = run(svc.handle_clear(ev, token))
        enabled_c, ep_c = store.get_user_state(IDENTITY.key)
        check(
            "C16 正确码删除+禁用+如实边界",
            "已完成删除" in text and "无法撤回" in text and "不会被本插件代为删除" in text
            and enabled_c is False and store.list_entries("user", IDENTITY.key) == [],
        )

        # -- 权限与模板 -------------------------------------------------------
        admin_ev = make_event(role="admin")
        try:
            run(svc.handle_admin_switch(ev, "on"))
            check("C17 普通用户不能动总开关", False)
        except Exception as exc:  # noqa: BLE001
            check("C17 普通用户不能动总开关", "管理员权限" in str(exc))
        text = run(svc.handle_admin_switch(admin_ev, "on"))
        check("C18 管理员开总开关（写 config）", config["admin_enabled"] is True)
        text = run(svc.handle_admin_switch(admin_ev, "off"))
        check("C19 管理员关总开关", config["admin_enabled"] is False)

        # 模板与用户分离
        run(svc.handle_admin_switch(admin_ev, "on"))
        text = run(svc.handle_admin_template_set(admin_ev, "语气风格", "喜欢", "双向", "3"))
        bot_key = build_identity(
            platform_id="aiocqhttp", self_id="bot1", persona_scope="persona_A"
        ).key
        t = store.get_entry("bot", bot_key, "tone")
        check(
            "C20 管理员模板 set（bot 档案）",
            "模板已保存" in text and t is not None and t.status == "like",
        )
        run(svc.handle_set(ev, "语气风格", "禁止"))
        u = store.get_entry("user", IDENTITY.key, "tone")
        check(
            "C21 同标签模板/用户互不覆盖",
            u is not None and u.status == "forbidden" and t.status == "like",
        )
        text = run(svc.handle_admin_template_show(admin_ev))
        check("C22 模板 show 只列 bot 条目", "语气风格" in text and "禁止" not in text.split("rev")[0])
        text = run(svc.handle_admin_template_remove(admin_ev, "语气风格"))
        check("C23 模板 remove", store.get_entry("bot", bot_key, "tone") is None)
        try:
            run(svc.handle_admin_template_set(ev, "语气风格", "喜欢"))
            check("C24 普通用户不能改模板", False)
        except Exception as exc:  # noqa: BLE001
            check("C24 普通用户不能改模板", "管理员权限" in str(exc))

        # -- 身份解析失败保守 -------------------------------------------------
        svc_null = CommandService(store, config, null_resolver, clock=clock)
        try:
            run(svc_null.handle_on(ev))
            check("C25 解析失败保守拒绝", False)
        except Exception as exc:  # noqa: BLE001
            check(
                "C25 解析失败保守拒绝",
                "无法确认本轮生效人格" in str(exc)
                and store.get_user_state("不存在\x1f身份")[0] is False,
            )
        store.close()
    except Exception:
        traceback.print_exc()
        check("P3 未预期异常", False, traceback.format_exc(limit=2))
    finally:
        loop.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


def _find_custom(store, identity, display):
    from pref_profile.model import make_tag_id

    return make_tag_id(display)


if __name__ == "__main__":
    sys.exit(main())
