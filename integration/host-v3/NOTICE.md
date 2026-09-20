# 第三方来源与修改说明

本目录包含 AstrBot 的修改后源码与差异文件，来源为 [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 4.28.0 / 4.26.0 分发源码。上游分发元数据声明 AGPL-3.0-or-later；许可全文保存在本目录 LICENSE。

项目维护者于 2026-09-19 修改了 `core/provider/entities.py` 与 `core/agent/runners/tool_loop_agent_runner.py`：增加请求内容源对象与运行时最终实例的弱引用映射、失效补偿和通道标记；v3 不共享源实例，不改变原序列化快照语义。两版完整修改文件及差异均在本目录，原始/修改后的 SHA-256 见清单。

这些文件从交付提交 da4e70a 原样导出，保留全部源码内容。diff 头中的开发机路径仅为历史来源信息，不是安装位置；运行时不依赖这些路径。补丁内部分注释记录早期原型方案，最终 v3 行为以实际实现与验收说明为准。配套修改未被上游 AstrBot 官方合并或背书。
