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


class TestMultiArchManifest(unittest.TestCase):
    """Selecting all supported architectures at once."""

    ALL_ARCHES = "avr,samd,sam,megaavr,mbed_nano,mbed_rp2040"

    def setUp(self):
        self.manifest = self._run()

    def _run(self):
        env = dict(os.environ)
        env.update(
            {
                "MIRROR_HOST": MIRROR_HOST,
                "ARCHITECTURES": self.ALL_ARCHES,
                "PACKAGES": "arduino",
                "LATEST_ONLY": "true",
                "DRY_RUN": "1",
            }
        )
        out = Path(ROOT / "manifest_multi.json")
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
                ORIGIN_HOST,
                "--architectures",
                self.ALL_ARCHES,
                "--packages",
                "arduino",
                "--latest-only",
            ]
        )
        self.assertEqual(rc, 0)
        with out.open(encoding="utf-8") as f:
            return json.load(f)

    def tearDown(self):
        out = ROOT / "manifest_multi.json"
        if out.exists():
            out.unlink()

    def test_all_architectures_selected(self):
        pkgs = {p["name"]: p for p in self.manifest["index"]["packages"]}
        archs = {p["architecture"] for p in pkgs["arduino"]["platforms"]}
        self.assertEqual(
            archs,
            {"avr", "samd", "sam", "megaavr", "mbed_nano", "mbed_rp2040"},
        )

    def test_shared_tool_deduplicated(self):
        # arm-none-eabi-gcc@7-2017q4 is referenced by samd, mbed_nano, and
        # mbed_rp2040 — the tool release must appear exactly once, and its
        # archive object exactly once in the manifest.
        pkgs = {p["name"]: p for p in self.manifest["index"]["packages"]}
        gcc_tools = [
            t
            for t in pkgs["arduino"]["tools"]
            if t["name"] == "arm-none-eabi-gcc" and t["version"] == "7-2017q4"
        ]
        self.assertEqual(len(gcc_tools), 1)
        relpaths = {o["relpath"] for o in self.manifest["objects"]}
        self.assertIn(
            "tools/gcc-arm-none-eabi-7-2017-q4-major-linux64.tar.bz2",
            relpaths,
        )

    def test_all_object_urls_on_origin(self):
        for o in self.manifest["objects"]:
            self.assertTrue(
                o["url"].startswith(ORIGIN_HOST),
                f"object url not on origin host: {o['url']}",
            )

    def test_all_published_urls_rewritten(self):
        idx = self.manifest["index"]
        for pkg in idx["packages"]:
            for plat in pkg["platforms"]:
                self.assertTrue(
                    plat["url"].startswith(MIRROR_HOST),
                    f"platform url not rewritten: {plat['url']}",
                )
            for tool in pkg["tools"]:
                for s in tool["systems"]:
                    self.assertTrue(
                        s["url"].startswith(MIRROR_HOST),
                        f"tool system url not rewritten: {s['url']}",
                    )


class TestCrossSchemeOriginMatching(unittest.TestCase):
    """The upstream index publishes mbed cores over http:// while everything
    else uses https:// on the same host. Origin matching must be scheme-agnostic
    so http:// URLs are mirrored when src_prefix is https:// (and vice versa).
    """

    def test_http_url_matches_https_prefix(self):
        from arduino_mirror.core import is_mirrorable, relpath_of, rewrite_index_url

        src = "https://downloads.arduino.cc"
        url = "http://downloads.arduino.cc/cores/staging/ArduinoCore-mbed-nano-4.6.0.tar.bz2"
        self.assertTrue(is_mirrorable(url, src))
        self.assertEqual(
            relpath_of(url, src),
            "cores/staging/ArduinoCore-mbed-nano-4.6.0.tar.bz2",
        )
        rewritten = rewrite_index_url(url, "https://mirror.test.invalid", src)
        self.assertTrue(rewritten.startswith("https://mirror.test.invalid/"))
        self.assertTrue(rewritten.endswith("cores/staging/ArduinoCore-mbed-nano-4.6.0.tar.bz2"))

    def test_https_url_matches_http_prefix(self):
        from arduino_mirror.core import is_mirrorable

        self.assertTrue(
            is_mirrorable(
                "https://downloads.arduino.cc/cores/staging/avr-1.8.8.tar.bz2",
                "http://downloads.arduino.cc",
            )
        )

    def test_foreign_host_not_mirrorable(self):
        from arduino_mirror.core import is_mirrorable

        self.assertFalse(
            is_mirrorable(
                "http://example.invalid/cores/staging/avr-1.8.8.tar.bz2",
                "https://downloads.arduino.cc",
            )
        )

    def test_help_url_not_mirrorable(self):
        from arduino_mirror.core import is_mirrorable

        self.assertFalse(
            is_mirrorable(
                "http://www.arduino.cc/en/Reference/HomePage",
                "https://downloads.arduino.cc",
            )
        )


if __name__ == "__main__":
    unittest.main()
