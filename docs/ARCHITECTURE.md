# 架构与实现

## 运行组件

`start_hub.py` 选择空闲本机端口和随机 CSRF，启动原版 `agy --hub`。可选 `AGY_QEMU_BINARY` 走用户态模拟，否则原生启动。端口分配存在释放后重新绑定的短暂竞争窗口；启动失败由 systemd 重试。

`antigravity_pro_bridge.py` 仅扫描当前系统用户的 `/proc/*/cmdline`，识别原生或 QEMU 包装的 Hub。每次 RPC 读取实际端口和 CSRF，重启后不需要手工更新端口。当前假设每个系统用户只有一个匹配的 Hub；多个 Hub 同时运行不受支持。

`Bridge.models()` 从 `GetUserStatus` 获取当前账号可用模型，排除 disabled。账号邮箱仅用于检查登录状态，不写入 HTTP 模型列表。

`Bridge.complete()` 将 OpenAI 消息和工具列表规范化后交给原生会话。`StartCascade` 使用 SDK 来源，绑定本机项目 ID 和 workspace URI。`SendUserCascadeMessage` 使用实时 `modelOrAlias`，不把 UI 标签当作模型 ID。

会话内存缓存依据规范化消息、模型、工具和 scope 匹配，最长一小时、约 128 项；Hub PID 改变后不复用旧会话。缓存不是持久化存储。串行锁仅在当前适配器进程内有效；外部直接调用 Hub 不受此锁保护。

工具调用通过提示协议返回 JSON，再转为 OpenAI `tool_calls`。只接受本轮提供的工具名称。此协议的稳定性取决于模型遵从程度，不是云端强类型工具接口。

`media_forward.py` 收集原生生成的 inline 图片、产物 URI、文件变更、最终 Markdown 链接和 MEDIA 标记。真实路径必须落在已配置项目或 Hub 产物目录，且文件类型和大小通过检查。附件复制到 OpenClaw media 目录，最终输出 canonical `MEDIA:` 标记。

`openai_adapter.py` 提供 `/health`、`/v1/models`、`/v1/chat/completions`。请求体上限 2 MiB。生成超时来自 `generationTimeout`（默认 180 秒），不是端到端排队 SLA。当前 `CancelCascadeInvocation` 是尽力取消，不能保证取消服务器端任务；超时后应检查 Hub 日志。

## 本机文件

```text
~/.local/share/antigravity-hub-adapter/
  start_hub.py
  antigravity_pro_bridge.py
  openai_adapter.py
  media_forward.py
  wait_ready.py
  adapter-settings.json        # 安装时生成，勿提交
  adapter.key                  # 安装时生成，勿提交
~/.gemini/jetski-standalone-oauth-token
~/.config/systemd/user/agy-hub.service
~/.config/systemd/user/antigravity-openai-adapter.service
~/.config/systemd/user/openclaw-gateway.service.d/antigravity-adapter.conf
~/.openclaw/media/antigravity/
```

Hub 初始化授权源是同机自己的 `~/.gemini/antigravity-cli/antigravity-oauth-token`。该路径与 Hub 文件路径是当前实测版本行为，未来可能变化。安装程序不在网络中传送 token，也不创建新的 OAuth 客户端或冒用其他客户端身份。

## 代理和权限

Hub unit 可设置 `HTTP_PROXY`／`HTTPS_PROXY`，本机端点通过 `NO_PROXY` 直连。HTTP 适配器不需要外网连接。没有禁用 TLS 校验。

项目默认使用本地后端、Default preset、sandbox 开启、外部文件 fallback Deny；自动执行策略为 sandbox 中 Proceed。显式 `--allow-all-tools` 增加终端、网页和 MCP 的全匹配许可，终端可以访问用户有权限的其他位置。项目文件规则与 OS 进程权限不是同一种边界。

## API 限制

不支持所有 OpenAI 参数、图片输入、准确 token 计数或实时增量生成。返回 OpenAI 兼容格式不意味着实现了完整 OpenAI API。网络错误、限额、账号不可用、Hub 崩溃会作为错误返回给 OpenClaw；可按自身需求设置 fallback，不应默默伪造成功回复。
