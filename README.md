# 角色偏好与互动边界管理 · AstrBot

私聊中分别维护用户本人的偏好档案与 Bot 当前人格模板，将双方明确设置的边界转换成本轮回复的临时指导。命令组为 `/xp`，中文别名为 `/偏好`。

**当前版本：0.1.12。指定补丁组合的本地独立验收（A0）已通过；真实 QQ / 模型试用（A1）待执行。**

## 下载与安装前必读

- [下载源码 ZIP](https://github.com/MisakaZhu/astrbot_plugin_preference_profile/archive/refs/heads/main.zip)，或使用 `git clone https://github.com/MisakaZhu/astrbot_plugin_preference_profile.git`。
- [测试发行版与原始插件安装包](https://github.com/MisakaZhu/astrbot_plugin_preference_profile/releases/tag/v0.1.12)。原始 ZIP 保留已验收字节，SHA-256 见发行版校验清单。
- **完整通过的组合需要 AstrBot 4.28.0 或 4.26.0 对应的宿主来源映射 v3 补丁。只安装插件 ZIP 或只用 WebUI 拉取插件，不会自动修改宿主。**
- 配套文件已放入 [integration/host-v3](integration/host-v3/README.md)。先由环境维护者核对版本、哈希和备份，在独立测试实例准备宿主，再安装本插件。
- 本仓库 `main` 在已验收插件提交 `7c99f5a` 上仅增加下载说明、仓库地址、验收摘要与配套资料；Python 运行时代码未变。`a0-accepted-0.1.12` 标签保留原始插件基线。

不带 v3 的原生宿主仍有已知来源归属限制，不能套用完整组合的通过结论。其他宿主版本、第三方 Agent 和协作插件新版本需要另行验证。

## 第一次使用

安装或重载后默认关闭；所有管理操作在私聊中执行，管理员与用户应处于同一测试人格。

管理员先设置：

```text
/xp admin switch on
/xp admin template set 语气风格 喜欢 双向 2
```

用户再设置：

```text
/xp help
/xp on
/xp set 语气风格 喜欢 双向 2
/xp show
/xp status
```

用同一问题对照不同方向与强度。提示指导不保证模型每次表现一致，请按照 [朋友测试清单](docs/FRIEND_TEST_A1.md) 记录实际结果。

## 命令速查

| 命令 | 用途 |
| --- | --- |
| `/xp help`、`/xp status` | 帮助、自己的状态 |
| `/xp on`、`/xp off` | 本人开启/关闭；受管理员总开关约束 |
| `/xp show [页码]` | 查看本人条目 |
| `/xp set <标签> <状态> [方向] [强度]` | 增改本人条目 |
| `/xp remove <标签>` | 删除一条 |
| `/xp clear [确认码]` | 先获取一次性确认码，再按实际返回的命令确认 |
| `/xp admin switch on\|off` | 管理员总开关 |
| `/xp admin stats` | 脱敏统计 |
| `/xp admin template show\|set\|remove\|clear` | 当前人格的 Bot 模板 |

状态：喜欢 / 中立 / 不喜欢 / 禁止；方向：主动 / 接受 / 双向；强度：1-5。内置标签包括话题偏好、称呼方式、玩笑尺度、亲密度表达、互动节奏、语气风格，也支持 2-16 字自定义标签。

## 规则与隐私

- 未设置不等于同意：单方喜欢且另一方未设置时不生成正向指导；任一方不喜欢或禁止时仍生成回避指导。
- 中立表示可接受但不特别偏好，不抬升也不压制对方已声明的强度；双方喜欢时取较低强度。性别不决定方向。
- 私人档案只由本人通过私聊维护；管理员维护 Bot 模板和总开关，普通管理命令不提供读取他人档案的入口。
- 临时偏好块不写入宿主历史。关闭、删除、管理员关停会停止后续读取/注入，并清理仍可控制的本插件在途内容。
- 已发送给模型的内容无法撤回；本插件不会代删宿主旧聊天、其他插件历史或备份。数据库维护者仍可能接触落盘数据。

## 可选协作

| 组件 | 已验证基线 | 行为 |
| --- | --- | --- |
| [Relation Arc](https://github.com/MisakaZhu/astrbot_plugin_relation_arc/tree/913ca59036267de2bcc481b8910b5f6fc8044f91) | `913ca59` | 只读关系快照；有效暂停/放缓约束正向指导。缺失或异常时关系联动不可用，独立偏好仍可运行。 |
| [Context Bridge](https://github.com/MisakaZhu/astrbot_plugin_user_context_bridge/tree/d8a7147e2a43c37b83781b92774afae2a254be60) | `d8a7147` + [0001](patches/README.md) | 偏好启用的私聊轮次排除跨会话共享。若插件在场而排除协议不可用，则禁用私人偏好注入。 |

协作补丁应应用于固定基线；不能将“其他版本安装成功”等同于通过此组合验收。

## 文档

- [当前验收与发行说明](docs/RELEASE_STATUS.md)
- [朋友实机测试清单](docs/FRIEND_TEST_A1.md)
- [完整项目报告 PDF](docs/PROJECT_REPORT.pdf)
- [宿主 v3 配套文件与使用说明](integration/host-v3/README.md)
- [设计记录](docs/ADR.md) / [历史验收矩阵](docs/ACCEPTANCE.md) / [变更记录](CHANGELOG.md)

历史文档保留各轮当时的状态；当前结论以 `docs/RELEASE_STATUS.md` 为准。仓库不包含真实档案数据库、聊天日志、账号凭据或虚拟环境。

## 许可

插件本体尚未另行声明开源许可证；公开下载不代表授予未声明的额外授权。`integration/host-v3` 中的 AstrBot 衍生源码及补丁沿用其 AGPL-3.0-or-later 许可，附完整许可与来源说明。双方范围分别说明，未替项目另选许可。
