"""Validation for the supported OpenAI subset (no external dependencies)."""
import json
import re
import uuid


def reject_constant(value):
    raise ValueError("Non-finite JSON number is not supported: " + value)


class AdapterError(RuntimeError):
    def __init__(self, message, code="upstream_error", status=502, retry_after=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after


def validate_schema(value, schema, path="arguments"):
    """Validate a deliberately small JSON Schema subset; reject other keywords."""
    if not isinstance(schema, dict):
        raise ValueError("Tool schema must be an object")
    supported = {"type", "properties", "required", "additionalProperties", "items",
                 "enum", "description", "title", "default"}
    if set(schema) - supported:
        raise ValueError("Unsupported tool schema keywords: " + ", ".join(sorted(set(schema) - supported)))
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    if kind is not None:
        if not isinstance(kind, str) or kind not in types:
            raise ValueError("Unsupported schema type at " + path)
        if not isinstance(value, types[kind]) or (kind in ("integer", "number") and isinstance(value, bool)):
            raise ValueError(path + " must be " + kind)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(path + " is not in enum")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(path + "." + name + " is required")
        props = schema.get("properties", {})
        for name, item in value.items():
            if name in props:
                validate_schema(item, props[name], path + "." + name)
            elif schema.get("additionalProperties") is False:
                raise ValueError(path + "." + name + " is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(item, schema["additionalProperties"], path + "." + name)
    if isinstance(value, list) and "items" in schema:
        for item in value:
            validate_schema(item, schema["items"], path + "[]")


def check_schema(schema):
    # Check definitions even when no argument of that type has been supplied yet.
    if not isinstance(schema, dict):
        raise ValueError("Tool schema must be an object")
    kind = schema.get("type", "object")
    samples = {"object": {}, "array": [], "string": "", "integer": 0,
               "number": 0, "boolean": False, "null": None}
    if not isinstance(kind, str) or kind not in samples:
        raise ValueError("Unsupported tool schema type")
    definition = dict(schema)
    definition.pop("required", None)
    definition.pop("enum", None)
    validate_schema(samples[kind], definition)
    props = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(props, dict) or not isinstance(required, list) or any(not isinstance(n, str) for n in required):
        raise ValueError("Invalid schema properties or required")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise ValueError("Schema enum must be an array")
    for child in props.values():
        check_schema(child)
    for keyword in ("items", "additionalProperties"):
        if keyword in schema:
            child = schema[keyword]
            if keyword == "additionalProperties" and isinstance(child, bool):
                continue
            check_schema(child)


def validate_tools(tools, choice):
    if tools is None:
        tools = []
    if not isinstance(tools, list):
        raise ValueError("tools must be an array")
    names = set()
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
            raise ValueError("Only function tools are supported")
        fn = tool["function"]
        name = fn.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in names:
            raise ValueError("Tool names must be valid and unique")
        names.add(name)
        check_schema(fn.get("parameters", {"type": "object"}))
    if choice is None:
        choice = "auto" if tools else "none"
    if isinstance(choice, str) and choice in ("auto", "none", "required"):
        if choice == "required" and not tools:
            raise ValueError("tool_choice required needs tools")
    elif isinstance(choice, dict) and choice.get("type") == "function" and isinstance(choice.get("function"), dict):
        if choice["function"].get("name") not in names:
            raise ValueError("tool_choice names an unavailable tool")
    else:
        raise ValueError("Invalid tool_choice")
    return tools, choice


def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("Request must be a JSON object")
    if not isinstance(data.get("model"), str) or not data["model"].strip():
        raise ValueError("model must be a nonempty string")
    if "stream" in data and not isinstance(data["stream"], bool):
        raise ValueError("stream must be a boolean")
    if "user" in data and not isinstance(data["user"], str):
        raise ValueError("user must be a string")
    validate_tools(data.get("tools"), data.get("tool_choice"))


def parse_tool_reply(answer, tools, choice):
    text = answer.strip()
    if text.startswith("```"):
        text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text).strip()
    try:
        parsed = json.loads(text, parse_constant=reject_constant)
        if not isinstance(parsed, dict) or not {"content", "tool_calls"} <= parsed.keys():
            raise ValueError("Expected content and tool_calls")
        if parsed["content"] is not None and not isinstance(parsed["content"], str):
            raise ValueError("content must be string or null")
        calls = parsed["tool_calls"]
        if not isinstance(calls, list):
            raise ValueError("tool_calls must be an array")
        if choice == "none" and calls:
            raise ValueError("tool_choice none forbids caller tools")
        if (choice == "required" or isinstance(choice, dict)) and not calls:
            raise ValueError("tool_choice requires a tool call")
        allowed = {t["function"]["name"]: t["function"] for t in tools}
        normalized = []
        for call in calls:
            if not isinstance(call, dict):
                raise ValueError("Tool call must be an object")
            fn = call.get("function", call)
            if not isinstance(fn, dict) or fn.get("name") not in allowed:
                raise ValueError("Hub requested an unavailable tool")
            name = fn["name"]
            if isinstance(choice, dict) and name != choice["function"]["name"]:
                raise ValueError("Hub requested a different tool than tool_choice")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args, parse_constant=reject_constant)
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be an object")
            validate_schema(args, allowed[name].get("parameters", {"type": "object"}))
            normalized.append({"id": "call_" + uuid.uuid4().hex[:16], "type": "function",
                               "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False, allow_nan=False)}})
        message = {"role": "assistant", "content": parsed["content"]}
        if normalized:
            message["tool_calls"] = normalized
        return message
    except (ValueError, TypeError, KeyError) as exc:
        raise AdapterError("Invalid Hub tool response: " + str(exc), "tool_protocol_error") from exc
