import json, urllib.request, urllib.error, os, socket, re, subprocess, sys
from adapter_protocol import AdapterError, validate_tools, parse_tool_reply
from pathlib import Path


def discover():
    if sys.platform == "darwin":
        output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
        for line in output.splitlines():
            match = re.match(r"\s*(\d+)\s+(.+)", line)
            if not match:
                continue
            command = match.group(2)
            if not re.search(r"(?:^|/)agy\s+--hub(?:\s|$)", command):
                continue
            port = re.search(r"--hub-port=(\d+)", command)
            token = re.search(r"--csrf_token=([A-Za-z0-9_-]+)", command)
            if port and token:
                return port.group(1), token.group(1), int(match.group(1))
        raise AdapterError("No authenticated local Hub process discovered", "hub_unavailable", 503)
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
    raise AdapterError("No authenticated local Hub process discovered", "hub_unavailable", 503)


def rpc(method, data=None, timeout=30, expected_pid=None):
    port, csrf, pid = discover()
    if expected_pid is not None and pid != expected_pid:
        raise AdapterError("Hub restarted during generation; request was not replayed", "hub_restarted", 503)
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
        # Do not expose raw upstream bodies (which can contain private data).
        e.close()
        if e.code == 429:
            raise AdapterError("Hub quota or capacity exhausted", "rate_limited", 429) from e
        if e.code in (401, 403):
            raise AdapterError("Hub authentication or permission failed", "hub_auth_error", 503) from e
        raise AdapterError("Hub RPC failed (HTTP %s)" % e.code, "hub_rpc_error") from e
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        raise AdapterError("Hub connection failed; generation was not replayed", "hub_unavailable", 503) from e
    except (ValueError, UnicodeError) as e:
        raise AdapterError("Hub returned an invalid JSON response", "hub_protocol_error") from e


import time, threading, hashlib, re
from media_forward import settings, collect_media


class Bridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.last_reused = False
        self.pending = None

    def models(self):
        u = rpc("GetUserStatus").get("userStatus", {})
        if not u.get("email"):
            raise AdapterError("Hub is not logged in", "hub_auth_error", 503)
        return [
            m
            for m in u.get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
            if m.get("modelId") and not m.get("disabled")
        ]

    def normalize(self, messages):
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a nonempty array")
        out = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("system", "developer", "user", "assistant", "tool"):
                raise ValueError("Each message needs a supported role")
            c = m.get("content", "")
            if c is not None and not isinstance(c, (str, list)):
                raise ValueError("message content must be text or text blocks")
            if isinstance(c, list):
                if any(not isinstance(x, dict) or not isinstance(x.get("text"), str) for x in c):
                    raise ValueError("Only text content blocks with string text are supported")
                if any(
                    x.get("type") not in ["text", "input_text", "output_text"]
                    for x in c
                    if isinstance(x, dict)
                ):
                    raise ValueError("Only text content is currently supported")
                c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
            if m["role"] == "tool" and (not isinstance(m.get("tool_call_id"), str) or not m["tool_call_id"]):
                raise ValueError("tool messages need a tool_call_id")
            if "tool_calls" in m:
                calls = m["tool_calls"]
                if m["role"] != "assistant" or not isinstance(calls, list):
                    raise ValueError("tool_calls must be an assistant array")
                for tool in calls:
                    if not isinstance(tool, dict) or not isinstance(tool.get("id"), str) or tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
                        raise ValueError("Invalid assistant tool call")
                    fn = tool["function"]
                    if not isinstance(fn.get("name"), str) or not isinstance(fn.get("arguments"), str):
                        raise ValueError("Tool name and serialized arguments must be strings")
                    try:
                        args = json.loads(fn["arguments"])
                    except ValueError as exc:
                        raise ValueError("Invalid assistant tool arguments") from exc
                    if not isinstance(args, dict):
                        raise ValueError("Assistant tool arguments must encode an object")
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
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a nonempty string")
        model = settings().get("modelAliases", {}).get(model, model)
        if not isinstance(scope, str):
            raise ValueError("session scope must be a string")
        tools, tool_choice = validate_tools(tools, tool_choice)
        messages = self.normalize(messages)
        if not messages:
            raise ValueError("messages must not be empty")
        if not self.lock.acquire(timeout=10):
            raise AdapterError("Hub adapter busy; retry later", "adapter_busy", 503, 1)
        cid = None
        submitted = False
        completed = False
        try:
            pid = discover()[2]
            def call(method, data=None, timeout=30):
                # Every RPC in this invocation is pinned to the same Hub process.
                if discover()[2] != pid:
                    raise AdapterError("Hub restarted during generation; request was not replayed", "hub_restarted", 503)
                return rpc(method, data, timeout=timeout, expected_pid=pid)
            if self.pending:
                old_pid, old_cid = self.pending
                if old_pid == pid:
                    state = call("GetAllCascadeTrajectories").get("trajectorySummaries", {}).get(old_cid, {})
                    if state.get("status") != "CASCADE_RUN_STATUS_IDLE":
                        raise AdapterError("Previous invocation has not confirmed idle; check Hub before retrying", "hub_task_pending", 503, 1)
                self.pending = None
            models = {m["modelId"]: m for m in self.models()}
            if model not in models:
                raise ValueError("Unknown or disabled Hub model: " + model)
            if len(self.sessions) > 128:
                self.sessions.clear()
            found = None
            prefix_length = 0
            # A tool round may append several tool result messages at once.
            for length in range(len(messages) - 1, 0, -1):
                candidate = self.sessions.pop(self.key(model, scope, messages[:length], tools), None)
                if candidate:
                    found, prefix_length = candidate, length
                    break
            reused = bool(
                found and found["pid"] == pid and time.monotonic() - found["at"] < 3600
            )
            if reused:
                cid = found["cascade"]
                records = messages[prefix_length:]
            else:
                cid = call(
                    "StartCascade",
                    {
                        "source": "CORTEX_TRAJECTORY_SOURCE_SDK",
                        "workspaceUris": [Path(settings()["workspace"]).as_uri()],
                        "projectEnvConfig": {"projectId": settings()["projectId"]},
                    },
                )["cascadeId"]
                records = messages
            offset = len(
                call(
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
            timeout = float(settings().get("generationTimeout", 180))
            if not 0 < timeout < float("inf"):
                raise AdapterError("Invalid generationTimeout setting", "configuration_error", 503)
            deadline = time.monotonic() + timeout
            send_call = call
            def timed_call(method, data=None):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Generation deadline exceeded")
                return send_call(method, data, timeout=min(30, remaining))
            call = timed_call
            submitted = True
            call(
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
            answer = ""
            all_steps = []
            idle_without_answer_at = None
            while time.monotonic() < deadline:
                steps = call(
                    "GetCascadeTrajectorySteps",
                    {"cascadeId": cid, "stepOffset": offset},
                ).get("steps", [])
                all_steps = steps
                for s in steps:
                    # A failed native tool (e.g. a missing optional file) may be
                    # recovered by the planner. Only a trajectory error is fatal.
                    if s.get("type") in ("CORTEX_STEP_TYPE_ERROR", "CORTEX_STEP_TYPE_ERROR_MESSAGE"):
                        error = s.get("errorMessage", {}).get("error", {})
                        detail = str(error.get("userErrorMessage", ""))
                        if "RESOURCE_EXHAUSTED" in detail or "quota reached" in detail.lower():
                            raise AdapterError("Antigravity account quota exhausted", "rate_limited", 429)
                        raise AdapterError("Hub generation failed", "hub_generation_error", 502)
                    p = s.get("plannerResponse", {})
                    if p.get("response") or p.get("modifiedResponse"):
                        answer = p.get("modifiedResponse") or p["response"]
                state = (
                    call("GetAllCascadeTrajectories")
                    .get("trajectorySummaries", {})
                    .get(cid, {})
                )
                if state.get("status") == "CASCADE_RUN_STATUS_IDLE":
                    if answer:
                        completed = True
                        break
                    if idle_without_answer_at is None:
                        idle_without_answer_at = time.monotonic()
                    elif time.monotonic() - idle_without_answer_at >= 3:
                        raise AdapterError("Hub returned no assistant text", "hub_empty_response", 502)
                else:
                    idle_without_answer_at = None
                time.sleep(min(0.8, max(0, deadline - time.monotonic())))
            else:
                raise TimeoutError("Generation deadline exceeded")
            message = {"role": "assistant", "content": answer}
            if tools:
                message = parse_tool_reply(answer, tools, tool_choice)
            if not message.get("tool_calls"):
                outputs = collect_media(all_steps, message.get("content") or "")
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
        except Exception as exc:
            timed_out = isinstance(exc, TimeoutError) or (
                isinstance(exc, AdapterError) and exc.__cause__ is not None
                and isinstance(exc.__cause__, (socket.timeout, TimeoutError))
            )
            if isinstance(exc.__cause__, urllib.error.URLError):
                timed_out = timed_out or isinstance(exc.__cause__.reason, (socket.timeout, TimeoutError))
            if submitted and not completed and cid:
                self.pending = (pid, cid)
            if timed_out and submitted and cid:
                cancelled = False
                try:
                    rpc("CancelCascadeInvocation", {"cascadeId": cid}, timeout=3, expected_pid=pid)
                    cancelled = True
                except Exception:
                    pass
                self.sessions.clear()
                self.pending = (pid, cid)
                raise AdapterError(
                    "Hub generation timed out; cancellation %s; request was not replayed" %
                    ("requested (completion unconfirmed)" if cancelled else "failed (task may still be running)"),
                    "generation_timeout", 504) from exc
            raise
        finally:
            self.lock.release()
