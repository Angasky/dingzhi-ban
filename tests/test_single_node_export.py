import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class SingleNodeExportTests(unittest.TestCase):
    def test_exports_selected_node_as_share_link(self):
        doc = {
            "proxies": [{
                "name": "Tokyo IPv6",
                "type": "vless",
                "server": "2001:db8::20",
                "port": 443,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "tls": True,
                "servername": "node.example.com",
            }]
        }
        with patch.object(server, "read_config", return_value=doc):
            result = server.exported_node_link("Tokyo IPv6")
        self.assertEqual(result["name"], "Tokyo IPv6")
        self.assertEqual(result["type"], "vless")
        self.assertIn("@[2001:db8::20]:443?", result["uri"])
        self.assertTrue(result["uri"].endswith("#Tokyo%20IPv6"))

    def test_rejects_chained_node(self):
        doc = {"proxies": [{
            "name": "Relay",
            "type": "vless",
            "server": "relay.example.com",
            "port": 443,
            "uuid": "id",
            "dialer-proxy": "Entry",
        }]}
        with patch.object(server, "read_config", return_value=doc):
            with self.assertRaisesRegex(ValueError, "链式代理"):
                server.exported_node_link("Relay")

    def test_rejects_unknown_node(self):
        with patch.object(server, "read_config", return_value={"proxies": []}):
            with self.assertRaisesRegex(ValueError, "节点不存在"):
                server.exported_node_link("missing")


if __name__ == "__main__":
    unittest.main()
