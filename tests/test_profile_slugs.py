import tempfile
import unittest
from pathlib import Path

import server


class ProfileSlugTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.originals = {
            name: getattr(server, name)
            for name in (
                "ROOT", "PROFILES_FILE", "PROFILES_DIR", "PROFILES_CACHE",
                "PROFILES_MTIME", "CONFIG_CACHE", "FILES", "BUNDLED_TEMPLATE",
            )
        }
        root = Path(self.temp.name)
        config = root / "clash.yaml"
        config.write_text(server.yaml_dump(server.minimal_profile_config()), encoding="utf-8")
        server.ROOT = root
        server.PROFILES_FILE = root / "profiles.json"
        server.PROFILES_DIR = root / "profiles"
        server.PROFILES_CACHE = None
        server.PROFILES_MTIME = 0
        server.CONFIG_CACHE = {}
        server.FILES = [config]
        server.BUNDLED_TEMPLATE = root / "missing-template.yaml"

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(server, name, value)
        self.temp.cleanup()

    def test_new_install_uses_random_fifty_character_slug(self):
        record = server.load_profiles()["profiles"][0]
        public = server.public_profile(record)
        self.assertRegex(record["slug"], r"^[a-z0-9]{50}$")
        self.assertFalse(public["customSlug"])
        self.assertEqual(public["url"], "/sub/" + record["slug"])
        self.assertNotEqual(public["url"], "/clash")

    def test_new_profile_defaults_to_random_slug(self):
        server.load_profiles()
        profile = server.create_profile({"name": "专用订阅", "templateId": "blank"})
        self.assertRegex(profile["slug"], r"^[a-z0-9]{50}$")
        self.assertFalse(profile["customSlug"])

    def test_long_custom_slug_can_be_disabled(self):
        server.load_profiles()
        profile = server.create_profile({
            "name": "自定义订阅", "templateId": "blank",
            "customSlug": True, "slug": "/custom-access-key-0123456789abcdef",
        })
        self.assertEqual(profile["slug"], "custom-access-key-0123456789abcdef")
        self.assertEqual(profile["url"], "/sub/custom-access-key-0123456789abcdef")
        self.assertTrue(profile["customSlug"])

        updated = server.update_profile({
            "oldId": profile["id"], "name": "自定义订阅",
            "customSlug": False,
        })
        self.assertRegex(updated["slug"], r"^[a-z0-9]{50}$")
        self.assertNotEqual(updated["slug"], "custom-access-key-0123456789abcdef")
        self.assertFalse(updated["customSlug"])

    def test_duplicate_custom_slug_is_rejected(self):
        server.load_profiles()
        shared = "shared-access-key-0123456789abcdef"
        server.create_profile({"name": "A", "customSlug": True, "slug": shared})
        with self.assertRaisesRegex(ValueError, "已经存在"):
            server.create_profile({"name": "B", "customSlug": True, "slug": shared})

    def test_legacy_main_link_is_migrated_to_secure_random_slug(self):
        legacy = {
            "profiles": [{
                "id": "clash", "name": "Clash", "protected": True,
                "files": [str(server.FILES[0])],
            }]
        }
        server.save_profiles(legacy)
        public = server.list_profiles()[0]
        self.assertRegex(public["slug"], r"^[a-z0-9]{50}$")
        self.assertEqual(public["url"], "/sub/" + public["slug"])
        self.assertFalse(public["customSlug"])

    def test_short_legacy_custom_slug_is_rotated(self):
        server.save_profiles({"profiles": [{
            "id": "clash", "name": "Clash", "protected": True,
            "slug": "easy-to-guess", "customSlug": True,
            "files": [str(server.FILES[0])],
        }]})
        public = server.list_profiles()[0]
        self.assertRegex(public["slug"], r"^[a-z0-9]{50}$")
        self.assertNotEqual(public["slug"], "easy-to-guess")
        self.assertFalse(public["customSlug"])


if __name__ == "__main__":
    unittest.main()
