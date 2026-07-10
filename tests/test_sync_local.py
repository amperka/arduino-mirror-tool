"""End-to-end sync_bucket test on LOCAL test infrastructure (no real domains).

No live network, no real arduino.cc / amperka.ru hosts. We spin up a local
HTTP server on 127.0.0.1:HOST_PORT that stands in for the ORIGIN CDN and serve
the fixture archives from it. The mirror host is a made-up local URL too.
Nothing in these tests knows about production domains.

The real `upload_object` (download via requests -> sha256 verify ->
target.upload_file) runs UNTOUCHED. The fake origin returns the archive body
only for its own host; a mirror-host url (the original 404 bug) never reaches
this server and sync dies — exactly like production. The recording target
captures the bytes it received so we prove sync fetched from `obj["url"]`
(origin), not the mirror.

Why the previous version was garbage (and this isn't):
  * CACHE_DIR is pointed at a fresh per-test tmp dir, so upload_object ACTUALLY
    downloads every run (the old version was defeated by a persistent cache/).
  * The origin host in the manifest is a LOCAL server we control, not a string
    compared against a hardcoded production domain. Break the download path and
    the test fails — it's not decorative.
"""

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "package_index.json"
sys.path.insert(0, str(ROOT / "src"))

from arduino_mirror.cli import main  # noqa: E402
from arduino_mirror.sync import LocalTarget, MirrorTarget, build_target, sync_bucket  # noqa: E402

HOST = "127.0.0.1"
PORT = 18099
ORIGIN_HOST = f"http://{HOST}:{PORT}"
MIRROR_HOST = "http://mirror.test.invalid"

# Fake origin CDN keyed by the path portion of the origin urls.
# Bodies' sha256 matches the checksums declared in the fixture, so
# upload_object's verify step passes (proving the real download+verify ran).
_ORIGIN_ARCHIVES = {
    "/cores/staging/avr-1.8.8.tar.bz2": b"avr-core-archive-bytes",
    "/tools/avrdude_8.0-arduino.1_Linux_64bit.tar.gz": b"avrdude-archive-bytes",
    "/tools/avr-gcc-7.3.0-atmel3.6.1-arduino7-x86_64-pc-linux-gnu.tar.bz2": (
        b"avr-gcc-archive-bytes"
    ),
}


class _OriginHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _ORIGIN_ARCHIVES.get(self.path)
        if body is None:
            self.send_error(404, "not in fake origin")
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


def _start_origin() -> HTTPServer:
    srv = HTTPServer((HOST, PORT), _OriginHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _RecordingTarget(MirrorTarget):
    """MirrorTarget that captures what sync pushed (bytes per relkey)."""

    def __init__(self):
        self.uploaded: dict[str, bytes] = {}

    def list_keys(self) -> dict[str, dict]:
        return {k: {"size": len(v)} for k, v in self.uploaded.items()}

    def upload_file(self, local_path: Path, key: str) -> None:
        self.uploaded[key] = Path(local_path).read_bytes()

    def delete_key(self, key: str) -> None:
        self.uploaded.pop(key, None)

    def prepare_public_read(self) -> None:
        return


def _make_manifest(tmp: Path, origin_host: str, mirror_host: str) -> dict:
    """Build a manifest from the fixture, rewriting the fixture's origin host to
    our LOCAL server so `obj['url']` is a real, reachable LOCAL url.

    Crux: the manifest under test carries a local origin url, so a correct sync
    fetches from it; a buggy sync (rewriting obj urls to the mirror host) would
    request the mirror host instead and never reach our server -> sync dies.
    """
    out = tmp / "manifest.json"
    tmp.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    index = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = json.dumps(index)
    patched = tmp / "patched_index.json"
    patched.write_text(raw, encoding="utf-8")

    rc = main(
        [
            "manifest",
            "--input",
            str(patched),
            "--manifest",
            str(out),
            "--mirror-host",
            mirror_host,
            "--origin-host",
            "http://127.0.0.1:18099",
            "--architectures",
            "avr",
            "--packages",
            "arduino",
            "--latest-only",
        ]
    )
    assert rc == 0, rc
    return json.loads(out.read_text(encoding="utf-8"))


class TestLocalTargetSync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "tmp_local_sync"
        # Fresh cache dir PER TEST so upload_object actually downloads.
        self.cache = self.tmp / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        os.environ["CACHE_DIR"] = str(self.cache)

        self.origin = _start_origin()
        self.mirror_root = self.tmp / "mirror-out"
        if self.mirror_root.exists():
            for p in self.mirror_root.rglob("*"):
                if p.is_file():
                    p.unlink()
        self.mirror_root.mkdir(parents=True, exist_ok=True)

        self.manifest = _make_manifest(self.tmp, ORIGIN_HOST, MIRROR_HOST)
        self.target = LocalTarget(root=self.mirror_root)

    def tearDown(self):
        self.origin.shutdown()
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CACHE_DIR", None)

    def _seed(self, rel: str, data: bytes = b"x"):
        p = self.mirror_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def test_full_sync_uploads_missing_and_publishes_index(self):
        sync_bucket(self.manifest, target=self.target)

        desired = {o["relpath"] for o in self.manifest["objects"]}
        present = set(self.target.list_keys())
        self.assertLessEqual(desired, present)
        self.assertIn("package_index.json", present)

        idx = json.loads((self.mirror_root / "package_index.json").read_text(encoding="utf-8"))
        self.assertIn("arduino", {p["name"] for p in idx["packages"]})
        # Published index must point clients at the MIRROR host.
        for pkg in idx["packages"]:
            for plat in pkg["platforms"]:
                self.assertTrue(plat["url"].startswith(MIRROR_HOST))
            for tool in pkg["tools"]:
                for sys_flavor in tool["systems"]:
                    self.assertTrue(sys_flavor["url"].startswith(MIRROR_HOST))

    def test_stale_deleted_protected_kept(self):
        desired_map = {o["relpath"]: o for o in self.manifest["objects"]}
        kept = next(iter(desired_map))
        kept_size = int(desired_map[kept].get("size", 0) or 0)
        stale = "cores/staging/avr-1.8.7.tar.bz2"  # not in desired
        protected_root = "index.txt"
        protected_sub = "othervendor/firmware.bin"
        self._seed(kept, b"x" * kept_size)  # matching size -> no re-fetch
        self._seed(stale, b"old")
        self._seed(protected_root, b"do-not-touch")
        self._seed(protected_sub, b"foreign")

        sync_bucket(self.manifest, target=self.target)

        self.assertFalse((self.mirror_root / stale).exists())
        self.assertTrue((self.mirror_root / protected_root).exists())
        self.assertTrue((self.mirror_root / protected_sub).exists())
        # Kept key not re-fetched: bytes unchanged, not an archive.
        self.assertEqual((self.mirror_root / kept).read_bytes(), b"x" * kept_size)

    def test_build_target_local(self):
        t = build_target(kind="local", local_root=self.mirror_root, prefix="mir")
        self.assertIsInstance(t, LocalTarget)
        self._seed("mir/cores/avr.tar.bz2")
        self.assertIn("cores/avr.tar.bz2", t.list_keys())


class TestDownloadSourceIsOrigin(unittest.TestCase):
    """Regression guard for the 404 bug: sync must fetch from the ORIGIN host
    (obj url), never from the published mirror host. If obj urls get rewritten
    to the mirror host, our origin server is never hit and sync dies.
    """

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "tmp_dl_src"
        self.cache = self.tmp / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        os.environ["CACHE_DIR"] = str(self.cache)

        self.origin = _start_origin()
        self.manifest = _make_manifest(self.tmp, ORIGIN_HOST, MIRROR_HOST)
        self.target = _RecordingTarget()

    def tearDown(self):
        self.origin.shutdown()
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CACHE_DIR", None)

    def test_objects_fetched_from_origin_not_mirror(self):
        sync_bucket(self.manifest, target=self.target)

        for o in self.manifest["objects"]:
            self.assertFalse(
                o["url"].startswith(MIRROR_HOST),
                f"object url points at mirror host, would 404: {o['url']}",
            )
            self.assertTrue(
                o["url"].startswith(ORIGIN_HOST),
                f"object url not on origin: {o['url']}",
            )

        # Bytes landed == what the local origin served -> real download happened.
        self.assertEqual(
            self.target.uploaded["cores/staging/avr-1.8.8.tar.bz2"],
            b"avr-core-archive-bytes",
        )
        self.assertEqual(
            self.target.uploaded["tools/avrdude_8.0-arduino.1_Linux_64bit.tar.gz"],
            b"avrdude-archive-bytes",
        )
        self.assertEqual(
            self.target.uploaded[
                "tools/avr-gcc-7.3.0-atmel3.6.1-arduino7-x86_64-pc-linux-gnu.tar.bz2"
            ],
            b"avr-gcc-archive-bytes",
        )


if __name__ == "__main__":
    unittest.main()
