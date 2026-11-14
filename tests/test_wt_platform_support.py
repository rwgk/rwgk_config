"""Native Windows locking does not need the POSIX fcntl module."""

import errno
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libexec"))
from rwgk_wt import platform_support


class WindowsPlatformTests(unittest.TestCase):
    def setUp(self):
        self.windows = mock.patch.object(platform_support, "IS_WINDOWS", True)
        self.windows.start()
        self.addCleanup(self.windows.stop)
        self.msvcrt = types.SimpleNamespace(
            LK_NBLCK=11, LK_UNLCK=12, locking=mock.Mock()
        )
        self.modules = mock.patch.dict(sys.modules, {"msvcrt": self.msvcrt})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_lock_and_unlock_use_same_first_byte_and_nonblocking_mode(self):
        offsets = []
        with tempfile.TemporaryFile("w+b") as stream:
            # Locking an empty file is supported; no byte needs to be written.
            self.msvcrt.locking.side_effect = lambda fd, mode, count: offsets.append(
                os.lseek(fd, 0, os.SEEK_CUR)
            )
            stream.seek(42)
            platform_support.lock_exclusive(stream)
            stream.seek(84)
            platform_support.unlock(stream)
            self.assertEqual(
                self.msvcrt.locking.call_args_list,
                [mock.call(stream.fileno(), 11, 1), mock.call(stream.fileno(), 12, 1)],
            )
            self.assertEqual(offsets, [0, 0])
            self.assertEqual(os.fstat(stream.fileno()).st_size, 0)

    def test_only_lock_contention_becomes_blocking_io_error(self):
        with tempfile.TemporaryFile("w+b") as stream:
            self.msvcrt.locking.side_effect = OSError(errno.EACCES, "locked")
            with self.assertRaises(BlockingIOError):
                platform_support.lock_exclusive(stream)
            self.msvcrt.locking.side_effect = OSError(errno.EBADF, "invalid fd")
            with self.assertRaises(OSError) as raised:
                platform_support.lock_exclusive(stream)
            self.assertEqual(raised.exception.errno, errno.EBADF)
            self.assertNotIsInstance(raised.exception, BlockingIOError)

    def test_directory_fsync_is_skipped_on_windows(self):
        with mock.patch.object(platform_support.os, "open") as opening:
            platform_support.fsync_directory(Path("unused-directory"))
        opening.assert_not_called()


if __name__ == "__main__":
    unittest.main()
