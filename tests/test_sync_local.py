"""End-to-end sync_bucket test against the local (filesystem) target.

No network: the network half (upload_object, which downloads archives from
Arduino's CDN) is stubbed so we can exercise the real list-diff + reconcile +
index-publish logic through the LocalTarget backend.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "package_index.json"
sys.path.insert(0, str(ROOT / "src"))

from arduino_mirror.cli import main  # noqa: E402
from arduino_mirror.sync import LocalTarget, build_target, sync_bucket  # noqa: E402


def _make_manifest(tmp: Path) -> dict:
    out = tmp / "manifest.json"
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
    assert rc == 0, rc
    return json.loads(out.read_text(encoding="utf-8"))


class TestLocalTargetSync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "tmp_local_sync"
        self.mirror_root = self.tmp / "mirror-out"
        if self.mirror_root.exists():
            for p in self.mirror_root.rglob("*"):
                if p.is_file():
                    p.unlink()
        self.mirror_root.mkdir(parents=True, exist_ok=True)
        self.manifest = _make_manifest(self.tmp)
        self.target = LocalTarget(root=self.mirror_root)

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, rel: str, data: bytes = b"x"):
        p = self.mirror_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def test_full_sync_uploads_missing_and_publishes_index(self):
        # Empty target -> every desired object is uploaded, index is published.
        uploaded = {}
        patch = mock.patch(
            "arduino_mirror.sync.upload_object",
            side_effect=lambda o, t: uploaded.setdefault(o["relpath"], t),
        )
        with patch:
            sync_bucket(self.manifest, target=self.target)

        desired = {o["relpath"] for o in self.manifest["objects"]}
        self.assertEqual(set(uploaded), desired)
        # Index published at root.
        self.assertTrue((self.mirror_root / "package_index.json").exists())
        idx = json.loads((self.mirror_root / "package_index.json").read_text(encoding="utf-8"))
        self.assertIn("arduino", {p["name"] for p in idx["packages"]})

    def test_stale_deleted_protected_kept(self):
        # Seed: one desired file already present (correct size -> NOT re-uploaded),
        # one stale under cores/, a hand-placed root file, and an unrelated subdir.
        desired_map = {o["relpath"]: o for o in self.manifest["objects"]}
        kept = next(iter(desired_map))
        kept_size = int(desired_map[kept].get("size", 0) or 0)
        stale = "cores/staging/avr-1.8.7.tar.bz2"  # not in desired
        protected_root = "index.txt"
        protected_sub = "othervendor/firmware.bin"
        self._seed(kept, b"x" * kept_size)  # matching size -> no re-upload
        self._seed(stale, b"old")
        self._seed(protected_root, b"do-not-touch")
        self._seed(protected_sub, b"foreign")

        uploads = []
        patch = mock.patch(
            "arduino_mirror.sync.upload_object",
            side_effect=lambda o, t: uploads.append(o["relpath"]),
        )
        with patch:
            sync_bucket(self.manifest, target=self.target)

        # Stale under a managed dir is removed.
        self.assertFalse((self.mirror_root / stale).exists())
        # Protected files survive.
        self.assertTrue((self.mirror_root / protected_root).exists())
        self.assertTrue((self.mirror_root / protected_sub).exists())
        # The already-present desired key (matching size) is NOT re-uploaded.
        self.assertNotIn(kept, uploads)

    def test_build_target_local(self):
        t = build_target(kind="local", local_root=self.mirror_root, prefix="mir")
        self.assertIsInstance(t, LocalTarget)
        self._seed("mir/cores/avr.tar.bz2")
        # Prefix is applied to listing.
        keys = t.list_keys()
        self.assertIn("cores/avr.tar.bz2", keys)


if __name__ == "__main__":
    unittest.main()
