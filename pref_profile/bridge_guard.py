"""Context Bridge 探测与轮次排除守卫（ADR-004）。

探测 star_map 中是否存在 astrbot_plugin_user_context_bridge：
- 不在场 → 无需隔离（no_bridge），注入允许；
- 在场且支持排除协议（模块导出 UCTX_EXCLUDE_PROTOCOL 与本插件协议名
  一致）→ protocol_ok，启用偏好私聊轮次时打排除标志；
- 在场但不支持 → protocol_missing：禁止私人偏好注入（V16 保守降级），
  命令层可向用户说明原因。

探测结果缓存；uctx 重载/卸载后由 refresh() 重估（保守起见每次
refresh 重新读取 star_map）。
"""

from __future__ import annotations

from typing import Any

from . import PROTOCOL_TURN_EXCLUSION

UCTX_PLUGIN_NAME = "astrbot_plugin_user_context_bridge"
EXCLUDE_EXTRA_KEY = "uctx_bridge_turn_excluded"

MODE_NO_BRIDGE = "no_bridge"
MODE_PROTOCOL_OK = "protocol_ok"
MODE_PROTOCOL_MISSING = "protocol_missing"


class BridgeGuard:
    def __init__(self, star_map: dict | None = None):
        self._star_map = star_map
        # 探测结果不缓存：宿主插件装载/卸载/重载会改变 star_map，
        # 每次取 mode 实时重估（开销为一次字典遍历）。返工 R2：
        # 无参构造（_star_map=None）曾导致恒为 no_bridge；生产代码必须
        # 传入真实宿主注册表 astrbot.core.star.star.star_map。
        self._detected_version: str | None = None

    @property
    def mode(self) -> str:
        return self.refresh()

    @property
    def detected_version(self) -> str | None:
        return self._detected_version

    def refresh(self) -> str:
        """实时探测（每次遍历 star_map；uctx 装卸后状态随之正确）。"""

        mode = MODE_NO_BRIDGE
        self._detected_version = None
        star_map = self._star_map
        if not star_map:
            return mode
        meta = None
        for m in star_map.values():
            if getattr(m, "name", None) == UCTX_PLUGIN_NAME:
                meta = m
                break
        if meta is None:
            return mode
        self._detected_version = getattr(meta, "version", None)
        activated = bool(getattr(meta, "activated", False))
        module = getattr(meta, "module", None)
        supported = getattr(module, "UCTX_EXCLUDE_PROTOCOL", None) if module else None
        if not activated:
            # 目标插件已停用/未激活：视为不在场（其钩子不会执行）
            return mode
        if supported == PROTOCOL_TURN_EXCLUSION:
            mode = MODE_PROTOCOL_OK
        else:
            mode = MODE_PROTOCOL_MISSING
        return mode

    def injection_allowed(self) -> bool:
        """protocol_missing 时禁止私人偏好注入（V16）。"""

        return self.mode in (MODE_NO_BRIDGE, MODE_PROTOCOL_OK)

    def mark_excluded(self, event: Any) -> bool:
        """为已启用偏好的私聊轮次打排除标志。

        只有 protocol_ok 才真正打标志（未打补丁的 uctx 不认识它）。
        返回是否已打标志。
        """

        if self.mode != MODE_PROTOCOL_OK:
            return False
        try:
            event.set_extra(
                EXCLUDE_EXTRA_KEY,
                {"protocol": PROTOCOL_TURN_EXCLUSION},
            )
            return True
        except Exception:  # noqa: BLE001 - 打标志失败按未打处理
            return False
