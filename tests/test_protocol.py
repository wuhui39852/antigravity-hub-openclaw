import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from adapter_protocol import AdapterError, validate_tools, validate_request, parse_tool_reply, validate_schema
import antigravity_pro_bridge as bridge

TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {
    "type": "object", "properties": {"path": {"type": "string"}},
    "required": ["path"], "additionalProperties": False}}}]


class ProtocolTests(unittest.TestCase):
    def test_common_openclaw_schema_constraints(self):
        schema = {"type": "object", "properties": {
            "query": {"type": "string", "maxLength": 4},
            "limit": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
        }, "required": ["query"], "additionalProperties": False}
        validate_tools([{"type": "function", "function": {"name": "search", "parameters": schema}}], None)
        validate_schema({"query": "test", "limit": None}, schema)
        with self.assertRaises(ValueError):
            validate_schema({"query": "too-long"}, schema)

    def test_pattern_properties(self):
        schema = {"type": "object", "patternProperties": {"^x-": {"type": "integer"}},
                  "additionalProperties": False}
        validate_tools([{"type": "function", "function": {"name": "dynamic", "parameters": schema}}], None)
        validate_schema({"x-count": 2}, schema)
        with self.assertRaises(ValueError):
            validate_schema({"other": 2}, schema)

    def test_tool_choices(self):
        self.assertEqual(parse_tool_reply("完成\nMEDIA:/tmp/result.txt", TOOLS, "auto")["content"],
                         "完成\nMEDIA:/tmp/result.txt")
        with self.assertRaises(AdapterError):
            parse_tool_reply("plain text", TOOLS, "required")
        call = json.dumps({"content": None, "tool_calls": [{"name": "read", "arguments": {"path": "x"}}]})
        for choice in ("auto", "required", {"type": "function", "function": {"name": "read"}}):
            self.assertEqual(parse_tool_reply(call, TOOLS, choice)["tool_calls"][0]["function"]["name"], "read")
        with self.assertRaises(AdapterError):
            parse_tool_reply(call, TOOLS, "none")
        for choice in ("required", {"type": "function", "function": {"name": "read"}}):
            with self.assertRaises(AdapterError):
                parse_tool_reply('{"content":"ok","tool_calls":[]}', TOOLS, choice)

    def test_schema_and_malformed_responses(self):
        for args in ({}, {"path": 1}, {"path": "x", "extra": True}):
            with self.assertRaises(AdapterError):
                parse_tool_reply(json.dumps({"content": None, "tool_calls": [{"name": "read", "arguments": args}]}), TOOLS, "auto")
        for text in ("{not json", "[]", "{}", '{"content":3,"tool_calls":[]}', '{"content":null,"tool_calls":[null]}'):
            with self.assertRaises(AdapterError):
                parse_tool_reply(text, TOOLS, "auto")

    def test_invalid_requests_and_schema_rejected_before_generation(self):
        for data in ([], {}, {"model": "x", "stream": "true"}, {"model": "x", "tools": {}},
                     {"model": "x", "tool_choice": "required"}):
            with self.assertRaises(ValueError):
                validate_request(data)
        with self.assertRaises(ValueError):
            validate_tools([{"type": "function", "function": {"name": "x", "parameters": {"$ref": "x"}}}], None)
        for messages in ({}, [None], [{"role": "user", "content": {}}], [{"role": "tool", "content": "x"}]):
            with self.assertRaises(ValueError):
                bridge.Bridge().normalize(messages)

    def test_extra_data_and_multiple_json_envelopes(self):
        call_with_extra = '{"content": null, "tool_calls": [{"name": "read", "arguments": {"path": "x"}}]} Extra text line 1'
        res = parse_tool_reply(call_with_extra, TOOLS, "auto")
        self.assertEqual(res["tool_calls"][0]["function"]["name"], "read")

        multiple_envelopes = '{"content": null, "tool_calls": [{"name": "read", "arguments": {"path": "1"}}]} {"content": "done", "tool_calls": [{"name": "read", "arguments": {"path": "2"}}]}'
        res2 = parse_tool_reply(multiple_envelopes, TOOLS, "auto")
        self.assertEqual(len(res2["tool_calls"]), 2)
        self.assertEqual(res2["content"], "done")

    def test_missing_closing_bracket_repaired(self):
        missing_bracket = '{"content": null, "tool_calls": [{"name": "read", "arguments": {"path": "x"}}]}'
        # Malformed: missing ']' before last '}'
        malformed = '{"content": null, "tool_calls": [{"name": "read", "arguments": {"path": "x"}}}'
        res = parse_tool_reply(malformed, TOOLS, "auto")
        self.assertEqual(res["tool_calls"][0]["function"]["name"], "read")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.pid = 1
        self.sent = 0
        self.calls = []
        self.idle = True
        self.fail_cancel = False
        self.timeout = False
        self.restart = False
        self.answer = "ok"
        self.error = None
        self.error_after_empty = False
        self.step_reads = 0
        self.b = bridge.Bridge()
        self.patches = [
            patch.object(bridge, "discover", side_effect=lambda: ("1", "csrf", self.pid)),
            patch.object(bridge, "rpc", side_effect=self.rpc),
            patch.object(bridge, "settings", return_value={"workspace": str(Path.cwd()), "projectId": "test", "generationTimeout": 1}),
            patch.object(bridge, "collect_media", return_value=[]),
            patch.object(self.b, "models", return_value=[{"modelId": "test", "modelOrAlias": "TEST"}]),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def rpc(self, method, data=None, timeout=30, expected_pid=None):
        self.calls.append((method, data, timeout))
        if expected_pid != self.pid:
            raise AdapterError("restarted", "hub_restarted", 503)
        if method == "StartCascade":
            return {"cascadeId": "c"}
        if method == "SendUserCascadeMessage":
            self.sent += 1
            if self.restart:
                self.pid += 1
            if self.timeout:
                raise TimeoutError("slow")
        if method == "CancelCascadeInvocation" and self.fail_cancel:
            raise RuntimeError("cancel failed")
        if method == "GetCascadeTrajectorySteps":
            self.step_reads += 1
            if self.error_after_empty and self.step_reads == 2:
                return {"steps": []}
            if self.error:
                return {"steps": [{"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
                                   "errorMessage": {"error": {"userErrorMessage": self.error}}}]}
            return {"steps": [{"plannerResponse": {"response": self.answer}}] if self.sent else []}
        if method == "GetAllCascadeTrajectories":
            return {"trajectorySummaries": {"c": {"status": "CASCADE_RUN_STATUS_IDLE" if self.idle else "RUNNING"}}}
        return {}

    def complete(self, messages=None, tools=None):
        return self.b.complete("test", messages or [{"role": "user", "content": "hi"}], tools)

    def test_restart_during_generation_does_not_replay(self):
        self.restart = True
        with self.assertRaises(AdapterError) as caught:
            self.complete()
        self.assertEqual(caught.exception.code, "hub_restarted")
        self.assertEqual(self.sent, 1)
        self.assertFalse(self.b.sessions)

    def test_cancel_failure_blocks_until_confirmed_idle(self):
        self.timeout = self.fail_cancel = True
        with self.assertRaises(AdapterError) as caught:
            self.complete()
        self.assertEqual(caught.exception.status, 504)
        self.assertIn("failed", str(caught.exception))
        self.assertFalse(self.b.lock.locked())
        self.timeout = False
        self.idle = False
        with self.assertRaises(AdapterError) as caught:
            self.complete()
        self.assertEqual(caught.exception.code, "hub_task_pending")
        self.assertEqual(self.sent, 1)
        self.idle = True
        self.complete()
        self.assertEqual(self.sent, 2)

    def test_restart_after_timeout_allows_new_request(self):
        self.timeout = True
        with self.assertRaises(AdapterError):
            self.complete()
        self.pid += 1
        self.timeout = False
        self.complete()
        self.assertEqual(self.sent, 2)

    def test_multiple_tool_results_reuse_session(self):
        self.answer = json.dumps({"content": None, "tool_calls": [
            {"name": "read", "arguments": {"path": "a"}}, {"name": "read", "arguments": {"path": "b"}}]})
        message, _ = self.complete(tools=TOOLS)
        self.answer = '{"content":"done","tool_calls":[]}'
        results = [{"role": "tool", "tool_call_id": call["id"], "content": "result"} for call in message["tool_calls"]]
        self.complete([{"role": "user", "content": "hi"}, message] + results, TOOLS)
        self.assertEqual(sum(method == "StartCascade" for method, _, _ in self.calls), 1)
        sent = [data for method, data, _ in self.calls if method == "SendUserCascadeMessage"]
        records = json.loads(sent[-1]["items"][0]["text"].split("CONVERSATION_RECORDS=")[1])
        self.assertEqual(records, results)

    def test_quota_error_is_reported_as_rate_limit(self):
        self.error = "RESOURCE_EXHAUSTED: Individual quota reached"
        with self.assertRaises(AdapterError) as caught:
            self.complete()
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.code, "rate_limited")

    def test_idle_before_error_step_is_not_empty_success(self):
        self.error = "Individual quota reached"
        self.error_after_empty = True
        with self.assertRaises(AdapterError) as caught:
            self.complete()
        self.assertEqual(caught.exception.status, 429)


if __name__ == "__main__":
    unittest.main()
