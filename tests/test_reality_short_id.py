import unittest

import server


class RealityShortIdTests(unittest.TestCase):
    def test_yaml_always_quotes_scientific_notation_like_short_id(self):
        doc = {"proxies": [{
            "name": "Reality", "type": "vless", "server": "example.com", "port": 443,
            "reality-opts": {"public-key": "key", "short-id": "75e4"},
        }]}
        dumped = server.yaml_dump(doc)
        self.assertIn("short-id: '75e4'", dumped)
        self.assertEqual(server.yaml_load(dumped)["proxies"][0]["reality-opts"]["short-id"], "75e4")

    def test_valid_share_link_preserves_short_id_as_string(self):
        node = server.parse_node_link(
            "vless://00000000-0000-0000-0000-000000000001@example.com:443"
            "?encryption=none&security=reality&pbk=key&sid=75e4#Reality"
        )
        self.assertEqual(node["reality-opts"]["short-id"], "75e4")
        self.assertIn("short-id: '75e4'", server.yaml_dump({"proxies": [node]}))

    def test_null_or_empty_short_id_is_omitted(self):
        for value in ("null", "none", ""):
            node = {"reality-opts": {"public-key": "key", "short-id": value}}
            server.normalize_reality_options(node)
            self.assertNotIn("short-id", node["reality-opts"])

    def test_invalid_short_id_is_rejected_before_config_write(self):
        with self.assertRaisesRegex(ValueError, "Reality short ID"):
            server.parse_node_link(
                "vless://00000000-0000-0000-0000-000000000001@example.com:443"
                "?encryption=none&security=reality&pbk=key&sid=not-hex#Broken"
            )


if __name__ == "__main__":
    unittest.main()
