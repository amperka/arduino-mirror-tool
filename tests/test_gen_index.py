"""Tests for the manifest builder, driven through the CLI (no network, no real
hosts).

Runs the `manifest` subcommand over a bundled fixture index (whose urls point
at 127.0.0.1:PORT / example.invalid — never a production domain) and asserts on
the produced manifest: filtering, host rewriting, non-mirrorable URLs left
intact, latest-only selection, and object metadata.
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

ORIGIN_HOST = "http://127.0.0.1:18099"
MIRROR_HOST = "http://mirror.test.invalid"


class TestManifestBuilder(unittest.TestCase):
    def setUp(self):
        self.manifest = self._run()

    def _run(self, **env_extra):
        env = dict(os.environ)
        env.update(
            {
                "MIRROR_HOST": MIRROR_HOST,
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
                MIRROR_HOST,
                "--origin-host",
                "http://127.0.0.1:18099",
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

    def test_object_urls_stay_on_origin(self):
        # Download SOURCE urls must point at the origin (where sync fetches
        # from). Rewriting these to the mirror host would make sync download from
        # the target -> 404.
        for o in self.manifest["objects"]:
            self.assertTrue(
                o["url"].startswith(ORIGIN_HOST),
                f"object url not on origin host: {o['url']}",
            )
            self.assertNotIn(MIRROR_HOST, o["url"])

    def test_published_index_rewritten_to_mirror(self):
        # The published Boards Manager index (manifest["index"]) is the contract
        # clients consume: its archive urls MUST advertise the mirror host.
        idx = self.manifest["index"]
        pkgs = {p["name"]: p for p in idx["packages"]}
        for p in pkgs["arduino"]["platforms"]:
            self.assertTrue(p["url"].startswith(MIRROR_HOST))
        for t in pkgs["arduino"]["tools"]:
            for s in t["systems"]:
                self.assertTrue(s["url"].startswith(MIRROR_HOST))

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
        # Non-archive URL: left untouched (not rewritten to origin or mirror).
        self.assertTrue(help_url.startswith("http://example.invalid"))
        self.assertNotIn(MIRROR_HOST, help_url)
        self.assertNotIn("127.0.0.1", help_url)

    def test_object_keys_preserved(self):
        relpaths = {o["relpath"] for o in self.manifest["objects"]}
        self.assertIn("cores/staging/avr-1.8.8.tar.bz2", relpaths)
        self.assertIn("tools/avrdude_8.0-arduino.1_Linux_64bit.tar.gz", relpaths)
        for o in self.manifest["objects"]:
            self.assertIsNotNone(o.get("sha256"))
            self.assertGreater(int(o.get("size", 0)), 0)


if __name__ == "__main__":
    unittest.main()
