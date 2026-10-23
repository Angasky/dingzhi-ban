import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import remote_manager as remote


class CoreInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = remote.RemoteManager(Path(self.temp.name), None, None)
        self.payload = {"name": "core", "host": "198.51.100.30", "sshUser": "root",
                        "sshPassword": "ssh-password", "fingerprint": "SHA256:" + "a" * 43,
                        "installType": "sing-box"}
        self.config = json.dumps({"inbounds": [], "outbounds": [], "secret": "config-private-value"})

    def test_core_requires_no_panel_password_and_config_is_encrypted(self):
        saved = self.manager.save({**self.payload, "coreConfig": self.config})
        self.assertEqual(saved["installType"], "sing-box")
        self.assertTrue(saved["hasCoreConfig"])
        self.assertNotIn("coreConfig", saved)
        self.assertNotIn("config-private-value", self.manager.path.read_text())
        record = self.manager.record(saved["id"])
        self.assertEqual(self.manager.secret(record, "coreConfig"), self.config)
        self.manager.save({**self.payload, "id": saved["id"], "coreConfig": ""})
        self.assertIn("coreConfig", record)
        self.manager.save({**self.payload, "id": saved["id"], "clearCoreConfig": True})
        self.assertNotIn("coreConfig", record)

    def test_invalid_types_modes_and_config_rejected(self):
        for changes in ({"installType": "x-panel"}, {"installType": []},
                        {"connectionMode": "existing"}, {"domain": "example.com"},
                        {"coreConfig": "[]"}, {"coreConfig": "{"},
                        {"coreConfig": '{"inbounds":{}}'}, {"coreConfig": "x" * 131073}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.manager.save({**self.payload, **changes})

    def test_generated_scripts_are_valid_and_use_correct_core(self):
        for kind, binary, check in (("xray", "/usr/local/bin/xray", "run -test -config"),
                                    ("sing-box", "/usr/bin/sing-box", "check -c")):
            script = remote.install_script({"installType": kind, "coreConfig": self.config}, "")
            subprocess.run(["bash", "-n"], input=script, text=True, check=True)
            self.assertIn(binary + " " + check, script)
            self.assertIn("systemctl is-active --quiet " + kind, script)
            self.assertNotIn("config-private-value", script)
            self.assertNotIn("/usr/local/x-ui", script)
            self.assertIn('bash -e "$installer"', script)

    def test_worker_verifies_core_without_panel_api(self):
        for kind in ("xray", "sing-box"):
            with self.subTest(kind=kind):
                saved = self.manager.save({**self.payload, "installType": kind,
                                           "coreConfig": self.config})
                record = self.manager.record(saved["id"])
                job = {"status": "running", "log": "", "stage": "connecting"}
                self.manager.connect = Mock(return_value=Mock())
                self.manager.execute = Mock(return_value=(0, ""))
                self.manager.panel = Mock(side_effect=AssertionError("must not call panel"))
                self.manager.install_worker(record, job)
                self.assertEqual(job["status"], "succeeded")
                self.assertNotIn("panelUrl", job)
                self.manager.panel.assert_not_called()
                self.assertNotIn("config-private-value", job["log"])
                self.manager.remove(saved["id"])

    def test_core_failure_records_error_and_uses_core_diagnostics(self):
        saved = self.manager.save(self.payload)
        record = self.manager.record(saved["id"])
        self.manager.connect = Mock(return_value=Mock())
        self.manager.execute = Mock(return_value=(22, "configuration invalid"))
        self.manager.panel = Mock(side_effect=AssertionError("must not call panel"))
        job = {"status": "running", "log": "", "stage": "config"}
        self.manager.install_worker(record, job)
        self.assertEqual(job["status"], "failed")
        self.assertIn("22", job["error"])
        self.manager.panel.assert_not_called()
        self.assertIn("systemctl status sing-box", self.manager.execute.call_args.args[2])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'requires Linux executable scripts and filesystem paths')
    def test_shell_validates_applies_and_restores_configuration(self):
        # Run the actual generated shell against fake binaries and an isolated filesystem.
        for kind in ("xray", "sing-box"):
            for scenario in ("success", "invalid", "restart-failure"):
                with self.subTest(kind=kind, scenario=scenario), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    bindir = root / "bin"
                    bindir.mkdir()
                    config = root / "config.json"
                    old = '{"inbounds": [], "outbounds": [], "old": true}'
                    new = json.dumps({"inbounds": [], "outbounds": [], "scenario": scenario})
                    config.write_text(old)
                    programs = {
                        "id": "#!/bin/sh\necho 0\n",
                        "curl": "#!/bin/sh\nwhile [ \"$1\" != -o ]; do shift; done\nprintf 'exit 0\\n' > \"$2\"\n",
                        kind: "#!/bin/sh\nif [ \"$1\" = version ]; then exit 0; fi\nfor arg do last=$arg; done\n! grep -q invalid \"$last\"\n",
                        "systemctl": "#!/bin/sh\nif [ \"$1\" = restart ]; then ! grep -q restart-failure " + str(config) + "; fi\n",
                    }
                    for name, content in programs.items():
                        file = bindir / name
                        file.write_text(content)
                        file.chmod(0o755)
                    script = remote.install_script({"installType": kind, "coreConfig": new}, "")
                    for path in ("/usr/local/etc/xray/config.json", "/etc/sing-box/config.json"):
                        script = script.replace(path, str(config))
                    script = script.replace("/usr/local/bin/xray", str(bindir / kind))
                    script = script.replace("/usr/bin/sing-box", str(bindir / kind))
                    script = script.replace("/var/lock", str(root / "locks"))
                    script = script.replace("sleep 2", ":")
                    result = subprocess.run(["bash"], input=script, text=True, capture_output=True,
                                            env={**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"]})
                    self.assertEqual(result.returncode, 0 if scenario == "success" else
                                     22 if scenario == "invalid" else 1, result.stdout + result.stderr)
                    self.assertEqual(config.read_text(), new if scenario == "success" else old)
                    self.assertNotIn(new, result.stdout + result.stderr)

    def test_core_routes_to_native_adapter(self):
        with patch("core_adapter.CoreAPI") as adapter:
            self.assertIs(self.manager.panel(None, {"installType": "xray"}), adapter.return_value)
            adapter.assert_called_once_with(self.manager, None, {"installType": "xray"})


if __name__ == "__main__":
    unittest.main()
