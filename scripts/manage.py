#!/usr/bin/env python3
"""Install and maintain the Linux user-local Hub adapter (standard library only)."""

import argparse
import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import urllib.request

REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
RUNTIME = HOME / ".local/share/antigravity-hub-adapter"
UNITS = HOME / ".config/systemd/user"


def run(*args):
    subprocess.run(args, check=True)


def backup(path):
    if path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, str(path) + ".before-hub-adapter-" + stamp)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    path.chmod(0o600)


def unit_quote(value):
    value = str(value)
    if any(x in value for x in ["\n", "\r", "\0"]):
        raise ValueError("Newlines and NUL are not allowed in service values")
    return (
        '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
    )


def bridge():
    sys.path.insert(0, str(RUNTIME))
    import antigravity_pro_bridge

    return antigravity_pro_bridge


def adapter_url(path=""):
    config = RUNTIME / "adapter-settings.json"
    cfg = json.loads(config.read_text()) if config.exists() else {}
    return "http://127.0.0.1:" + str(cfg.get("port", 18800)) + path


def install(args):
    port = getattr(args, "port", 18800)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid adapter port")
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    binary = Path(args.agy).expanduser().resolve()
    if not binary.is_file():
        raise SystemExit("Install the official agy CLI first; see README.")
    if args.qemu and not Path(args.qemu).is_file():
        raise SystemExit("--qemu must be an absolute path to qemu-x86_64")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    RUNTIME.chmod(0o700)
    for source in (REPO / "src").glob("*.py"):
        target = RUNTIME / source.name
        backup(target)
        shutil.copy2(source, target)
    key = RUNTIME / "adapter.key"
    if not key.exists():
        key.write_text(secrets.token_urlsafe(48) + "\n")
    key.chmod(0o600)
    settings = RUNTIME / "adapter-settings.json"
    backup(settings)
    save(
        settings,
        dict(
            port=port,
            projectId=args.project,
            workspace=str(workspace),
            nativeTools=True,
            mediaRoot=str(HOME / ".openclaw/media/antigravity"),
            generationTimeout=args.timeout,
        ),
    )
    env = {
        "HOME": str(HOME),
        "AGY_ENABLE_HUB": "1",
        "AGY_BINARY": str(binary),
        "PATH": str(HOME / ".local/bin") + ":/usr/local/bin:/usr/bin:/bin",
        "NO_PROXY": "localhost,127.0.0.1,::1",
    }
    if args.proxy:
        env.update(HTTP_PROXY=args.proxy, HTTPS_PROXY=args.proxy)
    if args.qemu:
        env["AGY_QEMU_BINARY"] = args.qemu
    hub = "[Unit]\nDescription=Antigravity Hub Daemon\nAfter=network.target\n[Service]\nType=simple\n"
    hub += "ExecStart=/usr/bin/python3 " + unit_quote(RUNTIME / "start_hub.py") + "\n"
    hub += "Restart=always\nRestartSec=5\nUMask=0077\n"
    hub += "".join(
        "Environment=" + unit_quote(k + "=" + v) + "\n" for k, v in env.items()
    )
    hub += "[Install]\nWantedBy=default.target\n"
    adapter = (
        "[Unit]\nDescription=Antigravity OpenAI Adapter\nRequires=agy-hub.service\n"
        "After=agy-hub.service\nPartOf=agy-hub.service\n[Service]\nType=simple\n"
    )
    adapter += "WorkingDirectory=" + str(RUNTIME).replace("%", "%%") + "\n"
    adapter += (
        "ExecStart=/usr/bin/python3 " + unit_quote(RUNTIME / "openai_adapter.py") + "\n"
    )
    adapter += (
        "ExecStartPost=/usr/bin/python3 " + unit_quote(RUNTIME / "wait_ready.py") + "\n"
    )
    adapter += "Environment=ADAPTER_PORT=" + str(port) + "\n"
    adapter += "Restart=on-failure\nRestartSec=5\nUMask=0077\n[Install]\nWantedBy=default.target\n"
    UNITS.mkdir(parents=True, exist_ok=True)
    for name, content in [
        ("agy-hub.service", hub),
        ("antigravity-openai-adapter.service", adapter),
    ]:
        target = UNITS / name
        backup(target)
        target.write_text(content)
    run("systemctl", "--user", "daemon-reload")
    print(
        "Installed. Next: complete your own agy login, then init-auth and setup-project."
    )


def init_auth(args):
    source = HOME / ".gemini/antigravity-cli/antigravity-oauth-token"
    target = HOME / ".gemini/jetski-standalone-oauth-token"
    if not source.is_file():
        raise SystemExit("Complete an agy Google login on this machine first.")
    if target.exists() and not args.replace:
        raise SystemExit(
            "Hub authorization already exists. Use --replace only to replace it."
        )
    backup(target)
    shutil.copyfile(source, target)
    target.chmod(0o600)
    run("systemctl", "--user", "enable", "--now", "agy-hub.service")
    run("systemctl", "--user", "restart", "agy-hub.service")
    print("Initialized this machine's own Hub credential. No token printed.")


def setup_project(args):
    cfg = json.loads((RUNTIME / "adapter-settings.json").read_text())
    workspace = Path(cfg["workspace"])
    allow = ["read_file(" + str(workspace) + ")", "write_file(" + str(workspace) + ")"]
    if args.allow_all_tools:
        allow += ["command(*)", "read_url(*)", "execute_url(*)", "mcp(*)"]
    p = dict(
        id=cfg["projectId"],
        name=cfg["projectId"],
        backendType="BACKEND_TYPE_LOCAL",
        projectResources={"resources": [{"folderUri": workspace.as_uri()}]},
        permissionGrants={
            "v2Migrated": True,
            "permissionGrants": {"allow": allow, "deny": [], "ask": []},
        },
        settings={
            "permissionPreset": "AGENT_PERMISSION_PRESET_DEFAULT",
            "sandboxMode": True,
            "fileAccessPolicy": "AGENT_SETTING_POLICY_DENY",
            "internetPolicy": "AGENT_SETTING_POLICY_ALLOW",
            "autoExecutionPolicy": "CASCADE_COMMANDS_AUTO_EXECUTION_PROCEED_IN_SANDBOX",
        },
    )
    b = bridge()
    models = b.Bridge().models()  # Refuse to continue without an authenticated Hub.
    ids = b.rpc("ListProjects", {"backend": "BACKEND_TYPE_LOCAL"}).get("projectIds", [])
    if p["id"] in ids:
        old = b.rpc("ReadProject", {"id": p["id"]})
        backup_file = RUNTIME / (
            "project.before-"
            + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            + ".json"
        )
        save(backup_file, old)
        b.rpc("UpdateProject", {"project": p})
    else:
        b.rpc("CreateProject", {"backend": "BACKEND_TYPE_LOCAL", "project": p})
    run("systemctl", "--user", "enable", "--now", "antigravity-openai-adapter.service")
    print("Project ready; available models:", ", ".join(m["modelId"] for m in models))


def configure_openclaw(args):
    models = bridge().Bridge().models()
    if args.model not in [m["modelId"] for m in models]:
        raise SystemExit("Requested model is not enabled for this account.")
    path = HOME / ".openclaw/openclaw.json"
    config = json.loads(path.read_text())
    backup(path)
    catalog = [
        dict(
            id=m["modelId"],
            name=m.get("label", m["modelId"]),
            contextWindow=131072,
            maxTokens=8192,
            reasoning=False,
            input=["text"],
        )
        for m in models
    ]
    config.setdefault("models", {}).setdefault("providers", {})["antigravity"] = {
        "baseUrl": adapter_url("/v1"),
        "api": "openai-completions",
        "apiKey": (RUNTIME / "adapter.key").read_text().strip(),
        "models": catalog,
    }
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    if isinstance(defaults.get("model"), str):
        defaults["model"] = {"primary": defaults["model"]}
    defaults.setdefault("model", {})["primary"] = "antigravity/" + args.model
    for m in catalog:
        defaults.setdefault("models", {}).setdefault("antigravity/" + m["id"], {})
    save(path, config)
    dropin = UNITS / "openclaw-gateway.service.d/antigravity-adapter.conf"
    dropin.parent.mkdir(parents=True, exist_ok=True)
    backup(dropin)
    dropin.write_text(
        "[Unit]\nWants=antigravity-openai-adapter.service\nAfter=antigravity-openai-adapter.service\n"
    )
    run("systemctl", "--user", "daemon-reload")
    print(
        "OpenClaw configured; existing channels retained. Restart Gateway when current tasks finish."
    )
    print(
        "Existing per-session model/runtime overrides may require sessions.patch; see docs."
    )


def doctor(args):
    b = bridge()
    print("Hub PID:", b.discover()[2])
    print("Has token:", b.rpc("HasAuthToken").get("hasToken", False))
    print("Models:", ", ".join(m["modelId"] for m in b.Bridge().models()))
    print(
        "Adapter:",
        json.load(urllib.request.urlopen(adapter_url("/health"), timeout=5)),
    )
    run(
        "systemctl",
        "--user",
        "is-active",
        "agy-hub",
        "antigravity-openai-adapter",
        "openclaw-gateway",
    )


def smoke(args):
    key = (RUNTIME / "adapter.key").read_text().strip()
    request = urllib.request.Request(
        adapter_url("/v1/chat/completions"),
        data=json.dumps(
            {
                "model": args.model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply exactly HUB_OK. Do not use tools.",
                    }
                ],
            }
        ).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    response = json.load(urllib.request.urlopen(request, timeout=240))
    content = response["choices"][0]["message"]["content"]
    print(content)
    if content.strip() != "HUB_OK":
        raise SystemExit("Unexpected smoke-test answer")


def main():
    if sys.platform != "linux":
        raise SystemExit("Deployment requires Linux with systemd user services.")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("install")
    p.add_argument("--workspace", default=str(HOME / ".openclaw/workspace"))
    p.add_argument("--project", default="openclaw-project")
    p.add_argument("--port", type=int, default=18800)
    p.add_argument("--agy", default=str(HOME / ".local/bin/agy"))
    p.add_argument(
        "--qemu", help="Absolute qemu-x86_64 path; only for incompatible old x86 CPUs"
    )
    p.add_argument("--proxy", default="")
    p.add_argument("--timeout", type=int, default=180)
    p.set_defaults(func=install)
    p = sub.add_parser("init-auth")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=init_auth)
    p = sub.add_parser("setup-project")
    p.add_argument("--allow-all-tools", action="store_true")
    p.set_defaults(func=setup_project)
    p = sub.add_parser("configure-openclaw")
    p.add_argument("--model", default="gemini-3.8-flash-high")
    p.set_defaults(func=configure_openclaw)
    p = sub.add_parser("doctor")
    p.set_defaults(func=doctor)
    p = sub.add_parser("smoke-test")
    p.add_argument("--model", default="gemini-3.8-flash-high")
    p.set_defaults(func=smoke)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
