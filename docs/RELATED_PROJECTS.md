# 类似项目对照

检索日期：2026-10-25。以下结论来自各项目公开 README／文档，未在本仓库设备上安装或验证其代码；功能描述不等于稳定性承诺。

| 项目 | 公开描述的路线 | 与本项目的关系 |
| --- | --- | --- |
| [marcodiniz/ag-local-bridge](https://github.com/marcodiniz/ag-local-bridge) | VS Code 扩展 → Antigravity sidecar ConnectRPC → OpenAI／Anthropic／Gemini 接口 | 本地 RPC 桥接思路接近，但依赖 Antigravity 内的扩展和 sidecar；本项目使用 Linux 常驻 agy Hub |
| [jkfujinami/antigravity-client](https://github.com/jkfujinami/antigravity-client) | Language Server SDK，Cascade 会话、事件和工具；支持连接现有服务或独立启动 | 适合研究类型化 RPC、流式事件、取消和会话恢复；并非现成的 OpenClaw 图片投递部署包 |
| [badrisnarayanan/antigravity-claude-proxy](https://github.com/badrisnarayanan/antigravity-claude-proxy) | 将 Antigravity Claude／Gemini 模型转为代理接口，有专门的 OpenClaw 配置文档 | OpenClaw 接入场景相近，应另外核对其后端路线、依赖、账号处理和通道附件行为 |
| [kqlio67/agy-proxy](https://github.com/kqlio67/agy-proxy) | 多协议代理、账号池、故障切换和管理面板 | 通用代理功能更广；本项目聚焦单用户本机 Hub、媒体归档和 systemd 部署 |
| [ericmaster/antigravity-cli-proxy](https://github.com/ericmaster/antigravity-cli-proxy) | HTTP 请求 → `agy --print` 子进程 → OpenAI JSON | 逐次执行 CLI 路线，和本项目复用常驻 Hub 的架构不同 |
| [pikapikaspeedup/Antigravity-Mobility-CLI](https://github.com/pikapikaspeedup/Antigravity-Mobility-CLI) | 基于本机已登录的 Antigravity 桌面应用，实现移动／远程使用 | 远程控制和原生能力复用相关，但其桌面依赖与无头 Linux 部署不同 |

本项目的具体组合是：**常驻 agy Hub + OpenClaw HTTP provider + 项目权限 + QQ／其他通道 MEDIA 附件 + ARM64 和旧 CPU QEMU 部署说明**。检索发现了相似组件和场景，不能据此声称这一技术路线是首创，也不应把其他项目未在 README 列出的功能当作其不支持。

借鉴其他项目代码时，应单独核对具体文件及其许可证；本仓库没有将上述项目代码或二进制纳入发布包。
