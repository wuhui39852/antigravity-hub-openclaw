import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
import media_forward
import antigravity_pro_bridge as bridge


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.cfg = {
            "workspace": str(self.workspace),
            "mediaRoot": str(self.root / "media"),
        }
        self.patcher = patch.object(media_forward, "settings", return_value=self.cfg)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_allowed_file_is_staged_and_deduplicated(self):
        p = self.workspace / "result.txt"
        p.write_text("hello")
        outputs = media_forward.collect_media(
            [], "MEDIA:" + str(p) + "\nMEDIA:" + str(p)
        )
        self.assertEqual(len(outputs), 1)
        self.assertEqual(Path(outputs[0]).read_text(), "hello")
        self.assertEqual(Path(outputs[0]).parent, self.root / "media")

    def test_outside_file_and_remote_url_are_rejected(self):
        p = self.root / "outside.txt"
        p.write_text("private")
        self.assertEqual(
            media_forward.collect_media(
                [], "MEDIA:" + str(p) + "\n![img](https://example.invalid/x.png)"
            ),
            [],
        )

    def test_symlink_escape_is_rejected(self):
        p = self.root / "outside.txt"
        p.write_text("private")
        link = self.workspace / "result.txt"
        try:
            link.symlink_to(p)
        except OSError:
            self.skipTest("Symbolic links unavailable on this system")
        self.assertEqual(media_forward.collect_media([], "MEDIA:" + str(link)), [])

    def test_large_and_internal_files_are_excluded(self):
        p = self.workspace / "large.txt"
        with p.open("wb") as f:
            f.truncate(20 * 1024 * 1024 + 1)
        internal = self.workspace / "task.md"
        internal.write_text("internal")
        self.assertEqual(
            media_forward.collect_media(
                [], "MEDIA:" + str(p) + "\nMEDIA:" + str(internal)
            ),
            [],
        )

    def test_inline_image_and_invalid_base64(self):
        data = b"test image bytes"
        steps = [
            {
                "generateImage": {
                    "generatedMedia": {
                        "inlineData": base64.b64encode(data).decode(),
                        "mimeType": "image/png",
                    }
                }
            },
            {
                "generateImage": {
                    "generatedMedia": {
                        "inlineData": "**invalid**",
                        "mimeType": "image/png",
                    }
                }
            },
        ]
        files = media_forward.collect_media(steps, "")
        self.assertEqual(len(files), 1)
        self.assertEqual(Path(files[0]).read_bytes(), data)


class BridgeTests(unittest.TestCase):
    def run_complete(self, answer, tools=None, recovered_error=False):
        calls = []

        def fake_rpc(method, data=None, timeout=30):
            calls.append(method)
            if method == "StartCascade":
                return {"cascadeId": "test-cascade"}
            if method == "GetCascadeTrajectorySteps":
                return {
                    "steps": (
                        []
                        if calls.count(method) == 1
                        else (
                            [
                                {
                                    "type": "CORTEX_STEP_TYPE_VIEW_FILE",
                                    "status": "CORTEX_STEP_STATUS_ERROR",
                                    "error": {"shortError": "file does not exist"},
                                }
                            ]
                            if recovered_error
                            else []
                        )
                        + [{"plannerResponse": {"response": answer}}]
                    )
                }
            if method == "GetAllCascadeTrajectories":
                return {
                    "trajectorySummaries": {
                        "test-cascade": {"status": "CASCADE_RUN_STATUS_IDLE"}
                    }
                }
            return {}

        b = bridge.Bridge()
        with patch.object(
            b,
            "models",
            return_value=[{"modelId": "test", "modelOrAlias": "TEST_MODEL"}],
        ), patch.object(bridge, "rpc", side_effect=fake_rpc), patch.object(
            bridge, "discover", return_value=("1", "not-a-real-token", 123)
        ), patch.object(
            bridge,
            "settings",
            return_value={"workspace": str(Path.cwd()), "projectId": "test"},
        ), patch.object(
            bridge, "collect_media", return_value=[]
        ):
            message, meta = b.complete(
                "test", [{"role": "user", "content": "hello"}], tools
            )
            b.complete(
                "test",
                [
                    {"role": "user", "content": "hello"},
                    message,
                    {"role": "user", "content": "again"},
                ],
                tools,
            )
        self.assertEqual(calls.count("StartCascade"), 1)
        self.assertTrue(b.last_reused)
        return message

    def test_text_reply_and_session_reuse(self):
        self.assertEqual(self.run_complete("OK")["content"], "OK")

    def test_recoverable_native_tool_error_does_not_abort_reply(self):
        self.assertEqual(
            self.run_complete("RECOVERED", recovered_error=True)["content"], "RECOVERED"
        )

    def test_function_call_translation(self):
        tools = [{"type": "function", "function": {"name": "read_file"}}]
        result = self.run_complete(
            json.dumps(
                {
                    "content": None,
                    "tool_calls": [
                        {"name": "read_file", "arguments": {"path": "example"}}
                    ],
                }
            ),
            tools,
        )
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(
            json.loads(result["tool_calls"][0]["function"]["arguments"]),
            {"path": "example"},
        )

    def test_unknown_tool_rejected(self):
        tools = [{"type": "function", "function": {"name": "read_file"}}]
        with self.assertRaisesRegex(RuntimeError, "unavailable tool"):
            self.run_complete(
                '{"content":null,"tool_calls":[{"name":"unlisted","arguments":{}}]}',
                tools,
            )

    def test_non_text_input_rejected(self):
        with self.assertRaisesRegex(ValueError, "Only text"):
            bridge.Bridge().normalize(
                [{"role": "user", "content": [{"type": "image_url"}]}]
            )


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "test_http_adapter", SRC / "openai_adapter.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        with patch.object(Path, "read_text", return_value="unit-test-key"):
            spec.loader.exec_module(cls.module)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.module.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_model_endpoint_requires_bearer_key(self):
        for headers in [{}, {"Authorization": "Bearer wrong"}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(
                    urllib.request.Request(self.url + "/v1/models", headers=headers)
                )
            self.assertEqual(error.exception.code, 401)
            error.exception.close()

    def test_authenticated_models(self):
        with patch.object(
            self.module.bridge, "models", return_value=[{"modelId": "test"}]
        ):
            r = urllib.request.Request(
                self.url + "/v1/models",
                headers={"Authorization": "Bearer unit-test-key"},
            )
            with urllib.request.urlopen(r) as response:
                self.assertEqual(json.load(response)["data"][0]["id"], "test")

    def test_sse_tool_response(self):
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "test",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        }
        with patch.object(
            self.module.bridge,
            "complete",
            return_value=(message, {"reused": False, "cascade": "test"}),
        ):
            r = urllib.request.Request(
                self.url + "/v1/chat/completions",
                data=json.dumps(
                    {
                        "model": "test",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                    }
                ).encode(),
                headers={
                    "Authorization": "Bearer unit-test-key",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(r) as response:
                body = response.read().decode()
        self.assertIn('"finish_reason": "tool_calls"', body)
        self.assertIn('"index": 0', body)
        self.assertTrue(body.endswith("data: [DONE]\n\n"))


if __name__ == "__main__":
    unittest.main()
