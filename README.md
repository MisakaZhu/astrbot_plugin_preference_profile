# astrbot_plugin_preference_profile

AstrBot 独立插件：**角色偏好与互动边界（XP 管理）首版 v0.1.0**。
在私聊中分别管理 Bot 人格模板与用户本人明确提交的偏好档案，在符合
双方边界与当前关系状态的前提下，以临时提示影响回复的互动方式、强度
与节奏。

## 安装

1. 解压 ZIP 到 AstrBot 数据目录旁的插件目录（如
   `addons/plugins/astrbot_plugin_preference_profile/`），或通过 WebUI
   离线安装该 ZIP。
2. 重载插件。默认**关闭**：管理员需私聊执行 `/xp admin switch on`，
   每个用户再私聊 `/xp on` 才为自己的轮次启用。
3. 零第三方依赖；要求 AstrBot 4.26.0 或 4.28.0（已用真实包验证，
   其他版本未测试不宣称支持）。

## 使用（私聊）

```
/xp help                 说明
/xp status               自己的状态概览
/xp on  /  /xp off       本人开启/关闭（关闭立即停止后续注入）
/xp show [页码]          查看自己的条目
/xp set <标签> <状态> [方向] [强度]
                         状态：喜欢/中立/不喜欢/禁止
                         方向：主动/接受/双向；强度：1-5
/xp remove <标签>        删除单条
/xp clear                一次性确认后删除自己的全部档案
/xp admin switch on|off  管理员总开关（私聊）
/xp admin stats          脱敏统计
/xp admin template show|set|remove|clear   Bot 人格模板维护
```

`/偏好` 为中文别名组；内置标签：话题偏好、称呼方式、玩笑尺度、
亲密度表达、互动节奏、语气风格（也可 2-16 字自定义）。

## 功能语义（要点）

- **未设置 ≠ 同意**：单方设置不产生任何指导；喜欢不覆盖另一方禁止；
  冲突取更严格限制。
- 中立 = 可接受但不特别偏好：不抬升也不压制对方强度。
- 性别等属性与方向/强度无关；保存偏好不建立或改变关系、不加分。
- 注入为单轮临时块（`mark_as_temp`）：不进宿主历史、不改人格/
  contexts/工具/其他插件提示、不改消息路由；普通轮次零额外模型调用。
- 关闭/删除即时生效（epoch 失效），慢请求不能复活。

## 与其他插件的协作

| 场景 | 状态 | 说明 |
| --- | --- | --- |
| 未装协作插件 | 完整独立可用 | 偏好管理与注入不依赖任何第三方插件 |
| Relation Arc 在场 | 只读联动已验证 | 关系暂停/放缓压制偏好强度；只读其数据库，缺失/异常自动保守失效 |
| Context Bridge 在场 + 补丁 | 隐私隔离已验证 | 偏好启用私聊轮次（输入/回复/工具轨迹）不进入跨会话共享；应用 `patches/0001-uctx-turn-exclusion-protocol.patch`（基线 d8a7147） |
| Context Bridge 在场但未打补丁 | 保守降级 | 私人偏好注入禁用（防止泄漏），命令层向用户说明；其余功能正常 |

### 应用补丁（需要跨会话隐私隔离时）

```bash
cd astrbot_plugin_user_context_bridge   # 须为 d8a7147 基线
git apply --check <本插件目录>/patches/0001-uctx-turn-exclusion-protocol.patch
git apply      <本插件目录>/patches/0001-uctx-turn-exclusion-protocol.patch
# 回滚：git apply -R <同一补丁>，或 git checkout -- main.py uctx_bridge/bridge.py
```

补丁仅 15 行新增；哈希与应用/回滚见 `patches/README.md`。

## 删除与隐私边界（如实告知）

- `/xp off` / `/xp clear` 只清理**本插件**保存的档案与缓存，立即停止
  后续读取与注入。
- **已经发送给模型的内容无法撤回**；宿主自身的聊天记录、其他插件
  （如上下文共享）的历史、既有备份**不受影响也不会被本插件代为删除**，
  请分别使用对应功能清理。
- 服务器/数据库运维人员仍可能读取落盘数据；不宣称端到端保密。

## 文档

- 设计决策：[docs/ADR.md](docs/ADR.md)
- 验收矩阵与证据：[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)
- 兼容矩阵：[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)
- 变更：[CHANGELOG.md](CHANGELOG.md)
- 交接：[HANDOFF.md](HANDOFF.md)

## 许可

未指定（发布前决定）；引用的 AstrBot 接口遵循 AstrBot 项目许可。
