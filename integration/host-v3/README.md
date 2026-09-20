# AstrBot 来源映射 v3 配套文件

这是偏好插件 0.1.12 完整验收组合所需的宿主修改，**不会随插件安装自动生效**。本目录从已验收交付 `da4e70a / v5-final` 原样导出，不包含任何真实数据。

## 文件选择

| 正在运行的 AstrBot | 对应目录 | 每版需要核对的文件 |
| --- | --- | --- |
| 4.28.0 | `patched_host_428/` | `core/provider/entities.py`、`core/agent/runners/tool_loop_agent_runner.py` |
| 4.26.0 | `patched_host_426/` | 同上 |

`host_patch_v3_hashes.txt` 列出原始及修改后文件哈希；`combo_manifest.json` 保留完整固定组合清单。`host_patch_v3_*.diff` 是冻结的审阅用差异，头部带有开发机路径且不是标准 Git 补丁格式，不应直接作为通用 `git apply` 安装器使用。

## 给环境维护者的安装顺序

1. 准备独立 AstrBot 测试实例，确认真正运行的版本与 Python 环境。使用该环境运行 `python -c "import astrbot; print(astrbot.__file__)"` 可确认 `astrbot/` 目录；源码运行或容器部署要核对实际挂载路径。
2. 停止测试实例，备份该实例的两份宿主原文件及配置/数据。按清单计算原文件 SHA-256；只有两份都与所选版本的 `original_sha256` 完全一致才继续。已修改或版本不同的文件不能直接覆盖。
3. 将对应 `patched_host_428` 或 `patched_host_426` 内的两份文件复制到该实例的 `astrbot/` 目录下的相同相对位置；不混用两版。复算并确认两份 `patched_sha256`。
4. 在该测试实例中安装偏好插件。若同时启用 Context Bridge，使用固定 `d8a7147` 与仓库 `patches/0001`；Relation Arc 联动固定为 `913ca59`。回到插件 README 执行开启和正常样例。
5. 按 `docs/FRIEND_TEST_A1.md` 做实机记录。补丁更新不由插件自动维护；升级 AstrBot 前应重新核对兼容性。

哈希检查示例：PowerShell 使用 `Get-FileHash -Algorithm SHA256 -LiteralPath <文件>`；Linux 使用 `sha256sum <文件>`。请替换为实际文件路径，不要把文档占位符直接执行。

回退时先停测试实例，恢复同版本原文件及相应备份，确认哈希后再启动；不要从不同版本目录拷贝文件覆盖。

本目录只提供已经验收的配套源码与清单；不代表已经安装到朋友设备。完整自动复核入口依赖维护者本地冻结探针，未将其误称为可在任意电脑直接运行的一键部署器。

来源与许可见 [NOTICE.md](NOTICE.md) 和 [LICENSE](LICENSE)。
