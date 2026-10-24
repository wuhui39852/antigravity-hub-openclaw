import json, urllib.request, urllib.error, os
from pathlib import Path


def discover():
    for p in Path("/proc").iterdir():
        try:
            if p.stat().st_uid != os.getuid():
                continue
        except OSError:
            continue
        try:
            a = (p / "cmdline").read_bytes().decode().split("\0")
        except (OSError, UnicodeError):
            continue
        if not a or not any(Path(x).name == "agy" for x in a[:6]) or "--hub" not in a:
            continue
        d = dict(x.split("=", 1) for x in a if x.startswith("--") and "=" in x)
        if "--hub-port" in d and "--csrf_token" in d:
            return d["--hub-port"], d["--csrf_token"], int(p.name)
    raise RuntimeError("No authenticated local Hub process discovered")


def rpc(method, data=None, timeout=30):
    port, csrf, pid = discover()
    req = urllib.request.Request(
        "http://127.0.0.1:"
        + port
        + "/exa.language_server_pb.LanguageServerService/"
        + method,
        data=json.dumps(data or {}).encode(),
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(str(e.code) + " " + e.read().decode()[:1200])


import time, threading, hashlib, uuid, re
from media_forward import settings, collect_media


class Bridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.last_reused = False

    def models(self):
        u = rpc("GetUserStatus").get("userStatus", {})
        if not u.get("email"):
            raise RuntimeError("Hub is not logged in")
        return [
            m
            for m in u.get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
            if m.get("modelId") and not m.get("disabled")
        ]

    def normalize(self, messages):
        out = []
        for m in messages:
            c = m.get("content", "")
            if isinstance(c, list):
                if any(
                    x.get("type") not in ["text", "input_text", "output_text"]
                    for x in c
                    if isinstance(x, dict)
                ):
                    raise ValueError("Only text content is currently supported")
                c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
            e = {"role": m.get("role"), "content": c or ""}
            for k in ["tool_calls", "tool_call_id", "name"]:
                if k in m:
                    e[k] = m[k]
            out.append(e)
        return out

    def key(self, model, scope, messages, tools):
        return hashlib.sha256(
            json.dumps(
                [model, scope, messages, tools], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()

    def complete(self, model, messages, tools=None, scope="", tool_choice=None):
        tools = tools or []
        messages = self.normalize(messages)
        if not messages:
            raise ValueError("messages must not be empty")
        if not self.lock.acquire(timeout=10):
            raise RuntimeError("Hub adapter busy; retry later")
        cid = None
        try:
            models = {m["modelId"]: m for m in self.models()}
            if model not in models:
                raise ValueError("Unknown or disabled Hub model: " + model)
            if len(self.sessions) > 128:
                self.sessions.clear()
            found = self.sessions.pop(
                self.key(model, scope, messages[:-1], tools), None
            )
            pid = discover()[2]
            reused = bool(
                found and found["pid"] == pid and time.monotonic() - found["at"] < 3600
            )
            if reused:
                cid = found["cascade"]
                records = messages[-1:]
            else:
                cid = rpc(
                    "StartCascade",
                    {
                        "source": "CORTEX_TRAJECTORY_SOURCE_SDK",
                        "workspaceUris": [Path(settings()["workspace"]).as_uri()],
                        "projectEnvConfig": {"projectId": settings()["projectId"]},
                    },
                )["cascadeId"]
                records = messages
            offset = len(
                rpc(
                    "GetCascadeTrajectorySteps", {"cascadeId": cid, "stepOffset": 0}
                ).get("steps", [])
            )
            instruction = (
                "Respond to the supplied conversation. The messages retain their stated system/user/assistant/tool roles. Native Hub tools are available under this project permission policy. Work within the configured project tree: "
                + settings()["workspace"]
                + ". Do not read credential stores or system configuration unless the user task explicitly requires them. Prefer the caller-provided tools when they cover the task; use native tools for image generation and other capabilities absent from the caller. If you create requested deliverables, include their absolute file paths as MEDIA:path lines in your final answer.\n"
            )
            if tools:
                instruction += (
                    "If a supplied tool is needed, return ONLY a JSON object with content (string or null) and tool_calls: [{name: exact tool name, arguments: JSON object}]. The listed caller tools are executed by the caller; native Hub capabilities are separate. Otherwise return ONLY a JSON object {content: your answer, tool_calls: []}. The caller executes tool calls and supplies their results.\nAVAILABLE_TOOLS="
                    + json.dumps(tools, ensure_ascii=False)
                    + "\nTOOL_CHOICE="
                    + json.dumps(tool_choice)
                    + "\n"
                )
            instruction += "CONVERSATION_RECORDS=" + json.dumps(
                records, ensure_ascii=False
            )
            rpc(
                "SendUserCascadeMessage",
                {
                    "cascadeId": cid,
                    "items": [{"text": instruction}],
                    "cascadeConfig": {
                        "plannerConfig": {
                            "requestedModel": models[model]["modelOrAlias"],
                            "toolConfig": {
                                "forceDisableToolCalling": not settings().get(
                                    "nativeTools", False
                                )
                            },
                        }
                    },
                },
            )
            timeout = float(settings().get("generationTimeout", 180))
            deadline = time.monotonic() + timeout
            answer = ""
            while time.monotonic() < deadline:
                steps = rpc(
                    "GetCascadeTrajectorySteps",
                    {"cascadeId": cid, "stepOffset": offset},
                ).get("steps", [])
                for s in steps:
                    # A failed native tool (e.g. a missing optional file) may be
                    # recovered by the planner. Only a trajectory error is fatal.
                    if s.get("type") == "CORTEX_STEP_TYPE_ERROR":
                        raise RuntimeError(
                            "Hub generation failed: "
                            + json.dumps(s.get("errorMessage", s.get("error", {})))[
                                :500
                            ]
                        )
                    p = s.get("plannerResponse", {})
                    if p.get("response") or p.get("modifiedResponse"):
                        answer = p.get("modifiedResponse") or p["response"]
                state = (
                    rpc("GetAllCascadeTrajectories")
                    .get("trajectorySummaries", {})
                    .get(cid, {})
                )
                if state.get("status") == "CASCADE_RUN_STATUS_IDLE":
                    if not answer:
                        raise RuntimeError("Hub returned no assistant text")
                    break
                time.sleep(0.8)
            else:
                try:
                    rpc("CancelCascadeInvocation", {"cascadeId": cid})
                except Exception:
                    pass
                raise RuntimeError(
                    "Hub generation timed out after %s seconds" % timeout
                )
            message = {"role": "assistant", "content": answer}
            if tools:
                text = answer.strip()
                if text.startswith("```"):
                    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text).strip()
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    message = {"role": "assistant", "content": parsed.get("content")}
                    calls = parsed.get("tool_calls") or []
                    if calls:
                        allowed = {t.get("function", {}).get("name") for t in tools}
                        normalized = []
                        for call in calls:
                            fn = call.get("function", call)
                            name = fn.get("name")
                            args = fn.get("arguments", {})
                            if name not in allowed:
                                raise RuntimeError("Hub requested an unavailable tool")
                            if isinstance(args, str):
                                args = json.loads(args)
                            if not isinstance(args, dict):
                                raise RuntimeError("Tool arguments must be an object")
                            normalized.append(
                                {
                                    "id": "call_" + uuid.uuid4().hex[:16],
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(
                                            args, ensure_ascii=False
                                        ),
                                    },
                                }
                            )
                        message["tool_calls"] = normalized
            if not message.get("tool_calls"):
                outputs = collect_media(steps, message.get("content") or "")
                if outputs:
                    content = message.get("content") or ""
                    content = re.sub(r"(?m)^MEDIA:.*$", "", content).strip()
                    message["content"] = (
                        content + "\n" + "\n".join("MEDIA:" + x for x in outputs)
                    )
            cachemessages = messages + [message]
            self.sessions[
                self.key(model, scope, self.normalize(cachemessages), tools)
            ] = {"pid": pid, "cascade": cid, "at": time.monotonic()}
            self.last_reused = reused
            return message, {"cascade": cid, "reused": reused, "hubPid": pid}
        finally:
            self.lock.release()
