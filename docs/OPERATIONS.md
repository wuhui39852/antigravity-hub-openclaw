# 维护与排障

macOS 使用 `python3 scripts/manage_macos.py doctor`、`python3 scripts/manage_macos.py smoke-test` 和 `launchctl print gui/$(id -u)/ai.antigravity.openai-adapter`。适配器日志位于 `~/.local/share/antigravity-hub-adapter/adapter.log`；Hub 由本机 Antigravity IDE／CLI 维持。macOS 安装器不会改动代理环境变量。若模型返回 `rate_limited`／HTTP 429，说明账号额度不足，不应当靠重启或更改网络解决。等额度重置后再测试并切换默认模型。安装前后的 OpenClaw 配置私有备份为 `~/.openclaw/openclaw.json.before-hub-adapter-*`，不要提交到仓库。

## 健康检查

在项目目录执行：

```bash
python3 scripts/manage.py doctor
python3 scripts/manage.py smoke-test
systemctl --user status agy-hub antigravity-openai-adapter openclaw-gateway
journalctl --user -u agy-hub -n 40 --no-pager
journalctl --user -u antigravity-openai-adapter -n 40 --no-pager
journalctl --user -u openclaw-gateway -n 60 --no-pager
```

`active` 只代表进程状态。`/health` 能读取模型也不代表真实推理成功，必须结合 `smoke-test` 和聊天端验证。日志可能包含用户输入或内部路径，不要直接公开上传。

## 聊天端没有回应

依次检查：通道连接 → 消息是否到达 Gateway → 会话选用的 provider／runtime → Hub 模型调用 → 附件上传和发送结果。

```bash
openclaw gateway call sessions.list --timeout 60000 --json
```

默认模型修改不一定清除旧会话覆盖。以自己版本的 `--help` 为准，可将相关会话改为：

```bash
openclaw gateway call sessions.patch --params \
  '{"key":"<实际会话 key>","model":"antigravity/gemini-3.8-flash-high"}' --json
```

同时检查旧 CLI／Codex runtime 覆盖，避免仍走旧后端。不要直接修改 OpenClaw SQLite 数据库。

大段旧历史会增加模型耗时。先用新会话测试，保存已有记录后再决定是否重置旧会话。不要因一次超时就清空全部会话。

确认某个旧请求阻塞时，先检查运行中的任务，再尝试：

```bash
openclaw gateway call chat.abort --params '{"sessionKey":"<实际会话 key>"}' --json
# 普通终止无效且确认需要新会话时：
openclaw gateway call sessions.reset --params '{"key":"<实际会话 key>","reason":"new"}' --json
```

平台 API 返回成功与用户实际收到是两项检查。附件需验证完整 MIME、文件大小、目标账号和通道支持；必要时请用户确认。

## Hub 重启与掉线

```bash
systemctl --user restart agy-hub.service
python3 scripts/manage.py doctor
python3 scripts/manage.py smoke-test
```

Hub 显式重启会通过 PartOf 带动适配器重启，动态端口／CSRF 自动重新发现。Hub 意外崩溃依靠 Restart=always 重启；正在进行的请求仍可能失败，不能保证无感续传。

旧 CPU 模拟运行要观察 `NRestarts`、内存与磁盘 I/O。启动慢时等到 Gateway 端口和通道日志就绪再测试，不要反复强制重启。Linux 默认 180 秒、macOS 默认 3600 秒的上限可在运行目录的 `adapter-settings.json` 调整，但延长超时不能修复进程崩溃。

## 更新和回滚

升级官方 agy 后先验证版本、Hub 登录和真实回复。RPC 不是稳定公开集成 API，勿假设任何新版本均可直接替换。

安装与配置命令在覆盖前创建 `*.before-hub-adapter-*` 私有备份。回滚时恢复指定的 OpenClaw 配置、相关服务文件，再执行 daemon-reload 和 Gateway 重启。恢复模型默认值后，检查已有聊天会话的模型覆盖。

要停用适配器：先把 OpenClaw 默认模型及相关会话切到另一个已经验证可用的 provider，移除 Gateway 的 `antigravity-adapter.conf` drop-in，daemon-reload，再停用 Hub 和适配器。不要在 OpenClaw 仍依赖此 provider 时删除授权文件。

```bash
systemctl --user disable --now antigravity-openai-adapter.service agy-hub.service
```

备份文件包含原有配置及凭据，应继续保存在自己的私有目录中。源码仓库不是密钥备份。
