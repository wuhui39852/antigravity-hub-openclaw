import importlib.util
import json
from pathlib import Path
import plistlib
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "manage_macos", Path(__file__).resolve().parents[1] / "scripts/manage_macos.py"
)
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class MacDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)
        self.workspace = home / "clawd"
        self.workspace.mkdir()
        self.patches = [
            patch.object(manager, "HOME", home),
            patch.object(manager, "RUNTIME", home / ".local/share/antigravity-hub-adapter"),
            patch.object(manager, "PLIST", home / "Library/LaunchAgents/ai.antigravity.openai-adapter.plist"),
            patch.object(manager.subprocess, "run", return_value=SimpleNamespace(returncode=1)),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_install_uses_launchd_without_proxy_and_keeps_key(self):
        args = SimpleNamespace(workspace=str(self.workspace), project="test-project",
                               model="gemini-3.8-flash-high", timeout=3600, port=18801)
        manager.install(args)
        key = (manager.RUNTIME / "adapter.key").read_text()
        manager.install(args)
        self.assertEqual((manager.RUNTIME / "adapter.key").read_text(), key)
        settings = json.loads((manager.RUNTIME / "adapter-settings.json").read_text())
        self.assertEqual(settings["port"], 18801)
        self.assertEqual(settings["generationTimeout"], 3600)
        with manager.PLIST.open("rb") as handle:
            service = plistlib.load(handle)
        self.assertEqual(service["EnvironmentVariables"], {"ADAPTER_PORT": "18801"})
        self.assertNotIn("HTTP_PROXY", str(service))

    def test_configure_preserves_channels_and_fallbacks(self):
        args = SimpleNamespace(workspace=str(self.workspace), project="test-project",
                               model="gemini-3.8-flash-high", timeout=3600, port=18801)
        manager.install(args)
        path = manager.HOME / ".openclaw/openclaw.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "channels": {"telegram": {"enabled": True}},
            "agents": {"defaults": {
                "model": {"primary": "old/model", "fallbacks": ["openai/gpt"]},
                "modelPolicy": {"allow": ["openai/gpt"]},
            }},
        }))
        fake = SimpleNamespace(Bridge=lambda: SimpleNamespace(models=lambda: [
            {"modelId": "gemini-3.8-flash-high", "label": "Gemini"}
        ]))
        with patch.object(manager, "bridge", return_value=fake):
            manager.configure_openclaw(SimpleNamespace(model="gemini-3.8-flash-high"))
        config = json.loads(path.read_text())
        self.assertTrue(config["channels"]["telegram"]["enabled"])
        self.assertEqual(config["agents"]["defaults"]["model"]["fallbacks"], ["openai/gpt"])
        self.assertIn("openai/gpt", config["agents"]["defaults"]["modelPolicy"]["allow"])
        self.assertIn("antigravity/gemini-3.8-flash-high", config["agents"]["defaults"]["modelPolicy"]["allow"])
        self.assertEqual(config["models"]["providers"]["antigravity"]["baseUrl"],
                         "http://127.0.0.1:18801/v1")
        self.assertIn("antigravity-pro", [
            m["id"] for m in config["models"]["providers"]["antigravity"]["models"]
        ])


if __name__ == "__main__":
    unittest.main()
