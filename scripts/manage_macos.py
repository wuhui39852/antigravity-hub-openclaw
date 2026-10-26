#!/usr/bin/env python3
"""Install the Hub adapter alongside an existing Antigravity Hub on macOS."""

import argparse
import datetime
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import subprocess
import sys
import urllib.request
import urllib.error


REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
RUNTIME = HOME / ".local/share/antigravity-hub-adapter"
PLIST = HOME / "Library/LaunchAgents/ai.antigravity.openai-adapter.plist"
LABEL = "ai.antigravity.openai-adapter"


def backup(path):
    if path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = path.with_name(path.name + ".before-hub-adapter-" + stamp)
        shutil.copy2(path, target)
        target.chmod(0o600)
        return target
    return None


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def bridge():
    sys.path.insert(0, str(RUNTIME))
    import antigravity_pro_bridge
    return antigravity_pro_bridge


def install(args):
    if not 1 <= args.port <= 65535 or args.timeout <= 0:
        raise SystemExit("Port and timeout must be positive valid values")
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit("Workspace must already exist: " + str(workspace))
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
    save_json(settings, {
        "projectId": args.project,
        "workspace": str(workspace),
        "nativeTools": True,
        "mediaRoot": str(workspace / "media/outbound/antigravity-hub"),
        "generationTimeout": args.timeout,
        "port": args.port,
        "modelAliases": {"antigravity-pro": args.model},
    })
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    backup(PLIST)
    with PLIST.open("wb") as handle:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": [sys.executable, "-u", str(RUNTIME / "openai_adapter.py")],
            "WorkingDirectory": str(RUNTIME),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "EnvironmentVariables": {"ADAPTER_PORT": str(args.port)},
            "StandardOutPath": str(RUNTIME / "adapter.log"),
            "StandardErrorPath": str(RUNTIME / "adapter.log"),
        }, handle)
    PLIST.chmod(0o644)
    domain = "gui/" + str(os.getuid())
    loaded = subprocess.run(["launchctl", "print", domain + "/" + LABEL],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if loaded:
        subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], check=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    print("macOS adapter installed at http://127.0.0.1:" + str(args.port) + "/v1")


def setup_project(args):
    cfg = json.loads((RUNTIME / "adapter-settings.json").read_text())
    workspace = Path(cfg["workspace"])
    allow = ["read_file(" + str(workspace) + ")", "write_file(" + str(workspace) + ")"]
    if args.allow_all_tools:
        allow += ["command(*)", "read_url(*)", "execute_url(*)", "mcp(*)"]
    project = {
        "id": cfg["projectId"],
        "name": cfg["projectId"],
        "backendType": "BACKEND_TYPE_LOCAL",
        "projectResources": {"resources": [{"folderUri": workspace.as_uri()}]},
        "permissionGrants": {
            "v2Migrated": True,
            "permissionGrants": {"allow": allow, "deny": [], "ask": []},
        },
        "settings": {
            "permissionPreset": "AGENT_PERMISSION_PRESET_DEFAULT",
            "sandboxMode": True,
            "fileAccessPolicy": "AGENT_SETTING_POLICY_DENY",
            "internetPolicy": "AGENT_SETTING_POLICY_ALLOW",
            "autoExecutionPolicy": "CASCADE_COMMANDS_AUTO_EXECUTION_PROCEED_IN_SANDBOX",
        },
    }
    api = bridge()
    models = api.Bridge().models()
    ids = api.rpc("ListProjects", {"backend": "BACKEND_TYPE_LOCAL"}).get("projectIds", [])
    if project["id"] in ids:
        save_json(RUNTIME / ("project.before-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"),
                  api.rpc("ReadProject", {"id": project["id"]}))
        api.rpc("UpdateProject", {"project": project})
    else:
        api.rpc("CreateProject", {"backend": "BACKEND_TYPE_LOCAL", "project": project})
    print("Project ready; models:", ", ".join(m["modelId"] for m in models))


def configure_openclaw(args):
    cfg = json.loads((RUNTIME / "adapter-settings.json").read_text())
    models = bridge().Bridge().models()
    ids = [m["modelId"] for m in models]
    if args.model not in ids:
        raise SystemExit("Requested model is not enabled for this account")
    path = HOME / ".openclaw/openclaw.json"
    config = json.loads(path.read_text())
    catalog = [{
        "id": m["modelId"], "name": m.get("label", m["modelId"]),
        "contextWindow": 131072, "maxTokens": 8192,
        "reasoning": False, "input": ["text"],
    } for m in models]
    for alias, target in cfg.get("modelAliases", {}).items():
        if target in ids and alias not in ids:
            catalog.append({
                "id": alias, "name": alias + " (compatibility alias)",
                "contextWindow": 131072, "maxTokens": 8192,
                "reasoning": False, "input": ["text"],
            })
    backup(path)
    config.setdefault("models", {}).setdefault("providers", {})["antigravity"] = {
        "baseUrl": "http://127.0.0.1:" + str(cfg.get("port", 18800)) + "/v1",
        "api": "openai-completions",
        "apiKey": (RUNTIME / "adapter.key").read_text().strip(),
        "models": catalog,
    }
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    if isinstance(defaults.get("model"), str):
        defaults["model"] = {"primary": defaults["model"]}
    defaults.setdefault("model", {})["primary"] = "antigravity/" + args.model
    for model in catalog:
        model_name = "antigravity/" + model["id"]
        defaults.setdefault("models", {}).setdefault(model_name, {})
        allowed = defaults.get("modelPolicy", {}).get("allow")
        if isinstance(allowed, list) and model_name not in allowed:
            allowed.append(model_name)
    save_json(path, config)
    print("OpenClaw configured; channels and fallback models preserved")


def doctor(_args):
    api = bridge()
    port, _token, pid = api.discover()
    print("Hub PID:", pid, "port:", port)
    print("Models:", ", ".join(m["modelId"] for m in api.Bridge().models()))
    cfg = json.loads((RUNTIME / "adapter-settings.json").read_text())
    print("Adapter:", json.load(urllib.request.urlopen("http://127.0.0.1:" + str(cfg.get("port", 18800)) + "/health", timeout=5)))
    config = json.loads((HOME / ".openclaw/openclaw.json").read_text())
    print("OpenClaw provider:", config.get("models", {}).get("providers", {}).get("antigravity", {}).get("baseUrl"))
    print("Default model:", config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"))


def smoke(args):
    cfg = json.loads((RUNTIME / "adapter-settings.json").read_text())
    key = (RUNTIME / "adapter.key").read_text().strip()
    request = urllib.request.Request("http://127.0.0.1:" + str(cfg.get("port", 18800)) + "/v1/chat/completions",
        data=json.dumps({"model": args.model, "messages": [{"role": "user", "content": "Reply exactly HUB_OK. Do not use tools."}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        response = json.load(urllib.request.urlopen(request, timeout=300))
    except urllib.error.HTTPError as exc:
        with exc:
            error = json.load(exc).get("error", {})
        raise SystemExit("Smoke test failed (HTTP %s, %s): %s" % (
            exc.code, error.get("type", "unknown"), error.get("message", "unknown")
        )) from None
    answer = response["choices"][0]["message"]["content"]
    print(answer)
    if answer.strip() != "HUB_OK":
        raise SystemExit("Unexpected smoke-test answer")


def main():
    if sys.platform != "darwin":
        raise SystemExit("This installer requires macOS")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("install")
    p.add_argument("--workspace", default=str(HOME / ".openclaw/workspace"))
    p.add_argument("--project", default="openclaw-project")
    p.add_argument("--model", default="gemini-3.8-flash-high")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--port", type=int, default=18800)
    p.set_defaults(func=install)
    p = sub.add_parser("setup-project")
    p.add_argument("--allow-all-tools", action="store_true")
    p.set_defaults(func=setup_project)
    p = sub.add_parser("configure-openclaw")
    p.add_argument("--model", default="gemini-3.8-flash-high")
    p.set_defaults(func=configure_openclaw)
    sub.add_parser("doctor").set_defaults(func=doctor)
    p = sub.add_parser("smoke-test")
    p.add_argument("--model", default="gemini-3.8-flash-high")
    p.set_defaults(func=smoke)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
