"""Tests for the manifest builder, driven through the CLI (no network).

Runs the `manifest` subcommand over a bundled fixture index and asserts on the
produced manifest: filtering, host rewriting, non-mirrorable URLs left intact,
latest-only selection, and object metadata.
"""

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "package_index.json"
sys.path.insert(0, str(ROOT / "src"))

from arduino_mirror.cli import main  # noqa: E402


class TestManifestBuilder(unittest.TestCase):
    def setUp(self):
        self.manifest = self._run()

    def _run(self, **env_extra):
        env = dict(os.environ)
        env.update(
            {
                "MIRROR_HOST": "https://arduino-downloads.amperka.ru",
                "ARCHITECTURES": "avr",
                "PACKAGES": "arduino",
                "LATEST_ONLY": "true",
                "DRY_RUN": "1",
            }
        )
        env.update(env_extra)
        out = Path(ROOT / "manifest.json")
        if out.exists():
            out.unlink()
        rc = main(
            [
                "manifest",
                "--input",
                str(FIXTURE),
                "--manifest",
                str(out),
                "--mirror-host",
                "https://arduino-downloads.amperka.ru",
                "--architectures",
                "avr",
                "--packages",
                "arduino",
                "--latest-only",
            ]
        )
        self.assertEqual(rc, 0)
        with out.open(encoding="utf-8") as f:
            return json.load(f)

    def tearDown(self):
        out = ROOT / "manifest.json"
        if out.exists():
            out.unlink()

    def test_keeps_only_latest_avr_platform(self):
        pkgs = {p["name"]: p for p in self.manifest["index"]["packages"]}
        self.assertIn("arduino", pkgs)
        versions = [p["version"] for p in pkgs["arduino"]["platforms"]]
        self.assertEqual(versions, ["1.8.8"])  # 1.8.7 dropped (latest_only)

    def test_rewrites_downloads_host(self):
        for o in self.manifest["objects"]:
            self.assertTrue(o["url"].startswith("https://arduino-downloads.amperka.ru/"))
            self.assertNotIn("downloads.arduino.cc", o["url"])

    def test_keeps_only_required_tool_releases(self):
        pkgs = {p["name"]: p for p in self.manifest["index"]["packages"]}
        tool_versions = {(t["name"], t["version"]) for t in pkgs["arduino"]["tools"]}
        self.assertEqual(
            tool_versions,
            {("avrdude", "8.0.0-arduino1"), ("avr-gcc", "7.3.0-atmel3.6.1-arduino7")},
        )

    def test_help_url_not_mirrored(self):
        pkgs = {p["name"]: p for p in self.manifest["index"]["packages"]}
        help_url = pkgs["arduino"]["help"]["online"]
        self.assertEqual(help_url, "https://www.arduino.cc/en/Reference/HomePage")
        self.assertNotIn("arduino-downloads.amperka.ru", help_url)

    def test_object_keys_preserved(self):
        relpaths = {o["relpath"] for o in self.manifest["objects"]}
        self.assertIn("cores/staging/avr-1.8.8.tar.bz2", relpaths)
        self.assertIn("tools/avrdude_8.0-arduino.1_Linux_64bit.tar.gz", relpaths)
        for o in self.manifest["objects"]:
            self.assertIsNotNone(o.get("sha256"))
            self.assertGreater(int(o.get("size", 0)), 0)


if __name__ == "__main__":
    unittest.main()
