import unittest

import server


class BatchLinkImportTests(unittest.TestCase):
    def setUp(self):
        self.doc = {
            "proxies": [{"name": "已有节点", "type": "vless", "server": "old.example", "port": 443}],
            "proxy-groups": [
                {"name": "✈️ Proxy", "type": "select", "proxies": ["DIRECT", "已有节点"]},
                {"name": "业务组", "type": "select", "proxies": ["✈️ Proxy", "DIRECT", "已有节点"]},
                {"name": "🛡️ 基础广告拦截", "type": "select", "proxies": ["REJECT"]},
            ],
            "rules": ["MATCH,✈️ Proxy"],
        }
        self.original_read = server.read_config
        self.original_write = server.write_config
        server.read_config = lambda: self.doc
        server.write_config = lambda doc, _reason: setattr(self, "written", doc)
        self.written = None

    def tearDown(self):
        server.read_config = self.original_read
        server.write_config = self.original_write

    def test_extracts_multiline_and_markdown_wrapped_links(self):
        links = server.extract_node_links("""
            vless://abc@one.example:443?encryption=none#One
            [second](socks5://two.example:1080#Two)
        """)
        self.assertEqual(links, [
            "vless://abc@one.example:443?encryption=none#One",
            "socks5://two.example:1080#Two",
        ])

    def test_imports_each_valid_link_and_reports_invalid_ones(self):
        result = server.import_node_links({
            "links": "\n".join([
                "vless://abc@one.example:443?encryption=none#已有节点",
                "socks5://two.example:1080#Two",
                "vless://bad",
            ]),
            "addToGroups": True,
        })
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["added"], 2)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["renamed"], [{"from": "已有节点", "to": "已有节点-02"}])
        self.assertIs(self.written, self.doc)
        self.assertEqual([node["name"] for node in self.doc["proxies"]], ["已有节点", "已有节点-02", "Two"])
        self.assertIn("已有节点-02", self.doc["proxy-groups"][1]["proxies"])
        self.assertIn("Two", self.doc["proxy-groups"][1]["proxies"])
        self.assertNotIn("Two", self.doc["proxy-groups"][2]["proxies"])

    def test_does_not_write_when_every_link_fails(self):
        result = server.import_node_links({"links": "vless://bad"})
        self.assertEqual(result["added"], 0)
        self.assertEqual(len(result["failed"]), 1)
        self.assertIsNone(self.written)


if __name__ == "__main__":
    unittest.main()
