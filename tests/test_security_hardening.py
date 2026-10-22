import json
import tempfile
import unittest
from pathlib import Path

import server


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_auth = server.AUTH
        self.original_failures = server.LOGIN_FAILURES
        server.AUTH = Path(self.temp.name) / "auth.json"
        server.LOGIN_FAILURES = {}

    def tearDown(self):
        server.AUTH = self.original_auth
        server.LOGIN_FAILURES = self.original_failures
        self.temp.cleanup()

    def test_fifth_failed_login_locks_client_for_window(self):
        client = "198.51.100.8"
        for number in range(server.LOGIN_MAX_FAILURES - 1):
            self.assertEqual(server.record_login_failure(client, now=1000 + number), 0)
        retry_after = server.record_login_failure(client, now=1004)
        self.assertGreater(retry_after, 0)
        self.assertGreater(server.login_retry_after(client, now=1005), 0)
        server.clear_login_failures(client)
        self.assertEqual(server.login_retry_after(client, now=1005), 0)

    def test_legacy_password_hash_upgrades_after_successful_login(self):
        salt, digest = server.password_hash(
            "long-enough-password", iterations=server.LEGACY_PBKDF2_ITERATIONS
        )
        server.AUTH.write_text(json.dumps({
            "username": "admin", "salt": salt, "hash": digest
        }), encoding="utf-8")
        self.assertTrue(server.authenticate("admin", "long-enough-password"))
        upgraded = json.loads(server.AUTH.read_text(encoding="utf-8"))
        self.assertEqual(upgraded["iterations"], server.PBKDF2_ITERATIONS)
        self.assertNotEqual(upgraded["salt"], salt)

    def test_random_subscription_slug_has_roughly_256_bits(self):
        self.assertRegex(server.random_slug(), r"^[a-z0-9]{50}$")

    def test_write_requests_require_exact_https_origin(self):
        handler = object.__new__(server.Handler)
        handler.connection = object()
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {
            "Host": "vpn.example.com",
            "Origin": "https://vpn.example.com",
            "X-Forwarded-Proto": "https",
        }
        self.assertTrue(handler.same_origin_request())
        handler.headers["Origin"] = "https://attacker.example"
        self.assertFalse(handler.same_origin_request())

    def test_loopback_proxy_without_forwarded_header_is_accepted(self):
        handler = object.__new__(server.Handler)
        handler.connection = object()
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {"Host": "vpn.example.com"}
        self.assertTrue(handler.secure_admin_transport())
        handler.client_address = ("198.51.100.8", 12345)
        self.assertFalse(handler.secure_admin_transport())


if __name__ == "__main__":
    unittest.main()
