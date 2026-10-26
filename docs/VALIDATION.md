# 设备验证记录

日期：2026-10-26。为公开分享，本页省略设备 IP、登录账号、Google 身份、聊天标识和所有密钥。

## macOS 工作站

- macOS 用户级 launchd 适配器与已运行的 Hub 集成，动态发现 Hub 端口与 CSRF，项目级终端／网页／MCP 许可已通过 Hub `ReadProject` 核对。
- 实际占用 18800 的其他进程未被中断；安装器改用可配置的 18801，OpenClaw provider 指向该端口。
- `gemini-3.8-flash-high` 当前模型额度耗尽，轨迹错误正确映射为 HTTP 429；`gpt-oss-120b-medium` 的真实 Hub `HUB_OK` 回复通过。
- OpenClaw 新会话以 `antigravity/gpt-oss-120b-medium` 返回 `HUB_OK`，运行元数据确认实际 provider／model。OpenClaw 的模型白名单和工具 schema 兼容问题已修复。
- OpenClaw 新会话生成测试 TXT，返回正文及 `mediaUrls` 中的归档路径。尚未验证聊天平台向最终用户投递，也未验证长期负载或图片输入。

## ARM64 小主机（R5S）

- Ubuntu 20.04.6、agy 1.4.7、Hub 2.26.0、OpenClaw 2026.9.1。
- 真实 Hub RPC 和 HTTP 回复通过。
- 双轮会话复用、工具请求／工具结果往返通过。
- OpenClaw 读文件测试通过。
- 原生终端生成有效 PNG 和文本附件，通过 MEDIA 归档发送 QQ；用户确认两者均收到。
- 网页访问通过原生终端 HTTP 获取验证，未把此测试表述为浏览器 UI 自动化测试。
- Hub 重启后 PID／端口变化，适配器重新发现、项目权限持久化通过。
- MCP 许可规则已配置，尚未验证真实 MCP 服务器调用。

## 老款 x86 笔记本（E50）

- Ubuntu 22.04.5、Intel Core i5 M430、OpenClaw 2026.9.5。
- 原生 agy 1.4.7 因 CPU 缺少 pclmul 无法启动。
- Ubuntu `qemu-user 6.2`、`qemu-x86_64 -cpu max` 和 `GODEBUG=asyncpreemptoff=1` 能运行官方原版程序。
- 使用另一账号独立 OAuth 授权，Hub HasAuthToken 和实时六模型列表通过。
- 真实 Hub 回复约 6.2 秒、HTTP 回复约 5.9 秒。
- 原生终端生成红色 PNG 和文本并归档通过，约 41.4 秒。
- 带入大量旧聊天历史的首次 OpenClaw 测试中发生一次 Hub 退出及自动重启，最终请求超时。不能据此宣称旧 CPU 环境已稳定。
- Hub 重启后真实 RPC 回复通过；OpenClaw 新会话返回 `E50_OPENCLAW_HUB_OK`，约 8.4 秒，运行记录确认实际 provider 为 antigravity。
- 新会话通过 OpenClaw 将文本、测试 PNG 和 TXT 投递到 QQ，返回 status ok，四次附件上传／发送接口均为 HTTP 200。尚未取得用户实际接收确认，也未做长期负载测试。
- 排查期间发现适配器把“原生工具读取可选文件失败”误当作整轮失败；已改为只在轨迹级 ERROR 时终止，允许模型自行处理工具错误，并添加对应回归测试。

## 可重复执行的测试

离线测试覆盖：媒体真实路径范围、符号链接逃逸、大小限制、远程引用不下载、inline 图片归档、工具请求转换、会话复用和 HTTP 鉴权。测试不依赖 Google 或机器人。

部署后运行 `manage.py smoke-test` 检查真实 HTTP 推理。进一步验证应通过自己的 OpenClaw 聊天端完成；每个用户需使用自己的 Google 账号、平台机器人和合法可用的网络环境。

截至本次 macOS 适配，本地共有 33 项单元测试通过；GitHub Actions 的跨平台结果以最新运行记录为准。离线测试不代替云端、账号或机器人投递验证。
