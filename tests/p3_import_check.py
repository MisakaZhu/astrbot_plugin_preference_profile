"""P3 宿主导入冒烟（MIS-149）：真实宿主包下加载插件模块并核对注册。

验证装饰器用法（command_group alias / 子命令 alias / permission_type）
在真实 AstrBot 包中成立；不实例化插件（无宿主运行时上下文）。
运行：<venv>/Scripts/python.exe tests/p3_import_check.py（须以父目录为 cwd
或 PYTHONPATH 指向父目录）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from astrbot.core.star.star import star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    star_handlers_registry,
)

EXPECTED_HANDLERS = {
    "xp_group", "xp_help", "xp_status", "xp_on", "xp_off",
    "xp_show", "xp_set", "xp_remove", "xp_clear", "xp_admin",
}

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"[PASS] {name}")
    else:
        FAIL.append(name)
        print(f"[FAIL] {name} {detail}")


def main() -> int:
    import astrbot_plugin_preference_profile.main as pm  # noqa: F401

    mod = pm.__name__
    handlers = {h.handler_name for h in star_handlers_registry if h.handler_module_path == mod}
    check("命令处理器全部注册", EXPECTED_HANDLERS <= handlers, f"{sorted(handlers)}")

    admin = next(h for h in star_handlers_registry
                 if h.handler_module_path == mod and h.handler_name == "xp_admin")
    perm_filters = [f for f in admin.event_filters if type(f).__name__ == "PermissionTypeFilter"]
    check("admin 命令绑定 ADMIN 权限过滤器", len(perm_filters) == 1)

    llm_hooks = [h for h in star_handlers_registry
                 if h.handler_module_path == mod and h.event_type == EventType.OnLLMRequestEvent]
    check("P3 阶段无 LLM 钩子（P4 加入）", len(llm_hooks) == 0)

    meta = star_map.get(mod)
    check("Star 元数据注册且激活", meta is not None and meta.activated)

    print("=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
