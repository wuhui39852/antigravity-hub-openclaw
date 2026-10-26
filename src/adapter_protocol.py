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
    """Validate the tool-schema subset used by current OpenClaw functions."""
    if not isinstance(schema, dict):
        raise ValueError("Tool schema must be an object")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    if kind is not None:
        kinds = [kind] if isinstance(kind, str) else kind
        if not isinstance(kinds, list) or not all(k in types for k in kinds):
            raise ValueError("Unsupported schema type at " + path)
        if not any(isinstance(value, types[k]) and not (k in ("integer", "number") and isinstance(value, bool)) for k in kinds):
            raise ValueError(path + " must be " + str(kind))
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(path + " is not in enum")
    if "const" in schema and value != schema["const"]:
        raise ValueError(path + " does not match const")
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in schema:
            successes = 0
            for child in schema[keyword]:
                try:
                    validate_schema(value, child, path)
                    successes += 1
                except ValueError:
                    pass
            if (keyword == "allOf" and successes != len(schema[keyword])) or (keyword == "anyOf" and successes < 1) or (keyword == "oneOf" and successes != 1):
                raise ValueError(path + " does not satisfy " + keyword)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", float("inf")):
            raise ValueError(path + " has invalid length")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(path + " does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for keyword, valid in (
            ("minimum", lambda n: value >= n), ("maximum", lambda n: value <= n),
            ("exclusiveMinimum", lambda n: value > n), ("exclusiveMaximum", lambda n: value < n),
        ):
            if keyword in schema and not valid(schema[keyword]):
                raise ValueError(path + " violates " + keyword)
        if "multipleOf" in schema and value / schema["multipleOf"] % 1 != 0:
            raise ValueError(path + " violates multipleOf")
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0) or len(value) > schema.get("maxProperties", float("inf")):
            raise ValueError(path + " has invalid property count")
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(path + "." + name + " is required")
        props = schema.get("properties", {})
        pattern_props = schema.get("patternProperties", {})
        for name, item in value.items():
            if name in props:
                validate_schema(item, props[name], path + "." + name)
            elif any(re.search(pattern, name) for pattern in pattern_props):
                for pattern, child in pattern_props.items():
                    if re.search(pattern, name):
                        validate_schema(item, child, path + "." + name)
            elif schema.get("additionalProperties") is False:
                raise ValueError(path + "." + name + " is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(item, schema["additionalProperties"], path + "." + name)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(path + " has invalid item count")
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            raise ValueError(path + " requires unique items")
        if "items" in schema:
            for item in value:
                validate_schema(item, schema["items"], path + "[]")


def check_schema(schema):
    # Check definitions even when no argument of that type has been supplied yet.
    if not isinstance(schema, dict):
        raise ValueError("Tool schema must be an object")
    supported = {"type", "properties", "patternProperties", "required", "additionalProperties", "items", "enum",
                 "description", "title", "default", "const", "format", "examples", "deprecated",
                 "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum",
                 "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "uniqueItems",
                 "minProperties", "maxProperties", "allOf", "anyOf", "oneOf"}
    if set(schema) - supported:
        raise ValueError("Unsupported tool schema keywords: " + ", ".join(sorted(set(schema) - supported)))
    kind = schema.get("type", "object")
    kinds = [kind] if isinstance(kind, str) else kind
    if not isinstance(kinds, list) or not kinds or any(k not in ("object", "array", "string", "integer", "number", "boolean", "null") for k in kinds):
        raise ValueError("Unsupported tool schema type")
    props = schema.get("properties", {})
    pattern_props = schema.get("patternProperties", {})
    required = schema.get("required", [])
    if not isinstance(props, dict) or not isinstance(required, list) or any(not isinstance(n, str) for n in required):
        raise ValueError("Invalid schema properties or required")
    if not isinstance(pattern_props, dict):
        raise ValueError("Invalid patternProperties")
    for pattern, child in pattern_props.items():
        re.compile(pattern)
        check_schema(child)
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise ValueError("Schema enum must be an array")
    for keyword in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
        if keyword in schema and (not isinstance(schema[keyword], int) or schema[keyword] < 0):
            raise ValueError("Invalid " + keyword)
    for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if keyword in schema and (not isinstance(schema[keyword], (int, float)) or isinstance(schema[keyword], bool)):
            raise ValueError("Invalid " + keyword)
    if schema.get("multipleOf", 1) == 0:
        raise ValueError("multipleOf cannot be zero")
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise ValueError("Invalid pattern")
        re.compile(schema["pattern"])
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise ValueError("Invalid uniqueItems")
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in schema:
            if not isinstance(schema[keyword], list) or not schema[keyword]:
                raise ValueError("Invalid " + keyword)
            for child in schema[keyword]:
                check_schema(child)
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
    if not text.startswith(("{", "[", "```")):
        if choice == "required" or isinstance(choice, dict):
            raise AdapterError("Tool choice requires a tool call", "tool_protocol_error")
        return {"role": "assistant", "content": text}
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
