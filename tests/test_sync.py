"""Tests for the pure list-diff helpers in arduino_mirror.core.

Exercised WITHOUT rclone or the network: given a set of present bucket keys and
a manifest, we assert exactly which keys are scheduled for upload / deletion /
left protected. This is the core deletion-safety guarantee.
"""

import unittest

from arduino_mirror.core import managed_keys, top_level_dirs


class TestTopLevelDirs(unittest.TestCase):
    def test_derives_managed_dirs(self):
        objs = [
            {"relpath": "cores/staging/avr-1.8.8.tar.bz2"},
            {"relpath": "cores/staging/avr-1.8.7.tar.bz2"},
            {"relpath": "tools/avrdude.tar.gz"},
        ]
        self.assertEqual(top_level_dirs(objs), {"cores", "tools"})

    def test_ignores_loose_root_files(self):
        objs = [{"relpath": "package_index.json"}]
        self.assertEqual(top_level_dirs(objs), set())


class TestManagedKeys(unittest.TestCase):
    def setUp(self):
        self.objects = [
            {"relpath": "cores/staging/avr-1.8.8.tar.bz2"},
            {"relpath": "tools/avrdude_8.0_Linux_64bit.tar.gz"},
        ]
        self.managed_dirs = top_level_dirs(self.objects)
        self.desired = {o["relpath"]: o for o in self.objects}

    def test_stale_under_managed_dir_is_deleted(self):
        present = {
            "cores/staging/avr-1.8.8.tar.bz2": {"size": 1},
            "cores/staging/avr-1.8.7.tar.bz2": {"size": 1},  # stale
            "tools/avrdude_8.0_Linux_64bit.tar.gz": {"size": 1},
        }
        md, ms, prot = managed_keys(present, self.desired, self.managed_dirs)
        self.assertEqual(ms, ["cores/staging/avr-1.8.7.tar.bz2"])
        self.assertEqual(prot, [])

    def test_hand_maintained_root_files_are_protected(self):
        present = {
            "cores/staging/avr-1.8.8.tar.bz2": {"size": 1},
            "tools/avrdude_8.0_Linux_64bit.tar.gz": {"size": 1},
            "index.txt": {"size": 1},  # root, hand-maintained
            "arduino-1.8.19.tar.xz": {"size": 1},  # root dist mirror
        }
        md, ms, prot = managed_keys(present, self.desired, self.managed_dirs)
        self.assertEqual(ms, [])
        self.assertEqual(set(prot), {"index.txt", "arduino-1.8.19.tar.xz"})

    def test_unrelated_dir_outside_managed_is_protected(self):
        present = {
            "cores/staging/avr-1.8.8.tar.bz2": {"size": 1},
            "tools/avrdude_8.0_Linux_64bit.tar.gz": {"size": 1},
            "othervendor/firmware.bin": {"size": 1},
        }
        md, ms, prot = managed_keys(present, self.desired, self.managed_dirs)
        self.assertEqual(ms, [])
        self.assertEqual(prot, ["othervendor/firmware.bin"])

    def test_desired_under_managed_is_not_stale(self):
        present = {
            "cores/staging/avr-1.8.8.tar.bz2": {"size": 1},
            "tools/avrdude_8.0_Linux_64bit.tar.gz": {"size": 1},
        }
        md, ms, prot = managed_keys(present, self.desired, self.managed_dirs)
        self.assertEqual(ms, [])
        self.assertEqual(prot, [])


if __name__ == "__main__":
    unittest.main()
