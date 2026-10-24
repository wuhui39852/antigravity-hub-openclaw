import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "manage", Path(__file__).resolve().parents[1] / "scripts/manage.py"
)
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class DeploymentTests(unittest.TestCase):
    def test_install_creates_private_key_and_qemu_units(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            binary = home / "agy"
            binary.touch()
            qemu = home / "qemu"
            qemu.touch()
            runtime = home / "runtime"
            units = home / "units"
            args = argparse.Namespace(
                workspace=str(home / "project"),
                agy=str(binary),
                qemu=str(qemu),
                project="test",
                timeout=75,
                proxy="http://127.0.0.1:7890",
            )
            with patch.multiple(
                manage, HOME=home, RUNTIME=runtime, UNITS=units
            ), patch.object(manage, "run"):
                manage.install(args)
                original = (runtime / "adapter.key").read_text()
                manage.install(args)
            self.assertEqual((runtime / "adapter.key").read_text(), original)
            cfg = json.loads((runtime / "adapter-settings.json").read_text())
            self.assertEqual(cfg["generationTimeout"], 75)
            self.assertEqual(cfg["projectId"], "test")
            self.assertIn("AGY_QEMU_BINARY=", (units / "agy-hub.service").read_text())
            self.assertIn(
                "PartOf=agy-hub.service",
                (units / "antigravity-openai-adapter.service").read_text(),
            )

    def test_configure_preserves_channels_and_existing_providers(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            runtime = home / "runtime"
            runtime.mkdir()
            (runtime / "adapter.key").write_text("unit-test-key")
            config = home / ".openclaw/openclaw.json"
            config.parent.mkdir()
            original = {
                "channels": {"example": {"enabled": True}},
                "models": {"providers": {"existing": {"baseUrl": "http://localhost"}}},
                "agents": {"defaults": {"model": "existing/model"}},
            }
            config.write_text(json.dumps(original))

            class B:
                def models(self):
                    return [{"modelId": "test", "label": "Test"}]

            class API:
                Bridge = B

            with patch.multiple(
                manage, HOME=home, RUNTIME=runtime, UNITS=home / "units"
            ), patch.object(manage, "bridge", return_value=API), patch.object(
                manage, "run"
            ):
                manage.configure_openclaw(argparse.Namespace(model="test"))
            result = json.loads(config.read_text())
            self.assertEqual(result["channels"], original["channels"])
            self.assertEqual(
                result["models"]["providers"]["existing"],
                original["models"]["providers"]["existing"],
            )
            self.assertEqual(
                result["agents"]["defaults"]["model"]["primary"], "antigravity/test"
            )
            self.assertEqual(len(list(config.parent.glob("*.before-hub-adapter-*"))), 1)

    def test_service_quote_rejects_line_injection(self):
        with self.assertRaises(ValueError):
            manage.unit_quote("bad\nExecStart=bad")
        self.assertIn("%%", manage.unit_quote("100%"))


if __name__ == "__main__":
    unittest.main()
