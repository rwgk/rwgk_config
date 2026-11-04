import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

SCRIPT = Path(__file__).resolve().parents[1] / "bin/watch_cuda_toolkit_pypi.py"
SPEC = importlib.util.spec_from_file_location("watch_cuda_toolkit_pypi", SCRIPT)
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


def wheel(name, version, platform="any", python="py3", **changes):
    return {
        "filename": f"{name}-{version}-{python}-none-{platform}.whl",
        "packagetype": "bdist_wheel",
        "yanked": False,
        "requires_python": ">=3.8",
        "upload_time_iso_8601": "2026-11-04T12:00:00Z",
        **changes,
    }


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(
            version="13.5.0",
            extras=["all"],
            platforms=["linux-x86_64", "windows-amd64"],
            python_version="3.12",
        )
        self.metadata = {
            "info": {
                "provides_extra": ["all", "cufile"],
                "requires_dist": [
                    'nvidia-runtime==13.5.21.*; extra == "all"',
                    'nvidia-link>=13.4.92,<14; extra == "all"',
                    'nvidia-file==1.20.*; sys_platform == "linux" and (extra == "all" or extra == "cufile")',
                ],
            },
            "urls": [wheel("cuda_toolkit", "13.5.0")],
        }
        self.projects = {
            "nvidia-runtime": {
                "releases": {"13.5.21": [wheel("nvidia_runtime", "13.5.21")]}
            },
            "nvidia-link": {"releases": {"13.4.92": [wheel("nvidia_link", "13.4.92")]}},
            "nvidia-file": {
                "releases": {
                    "1.20.1": [wheel("nvidia_file", "1.20.1", "manylinux_2_28_x86_64")]
                }
            },
        }

    def fetch(self, name, _version=None):
        return self.metadata if name == "cuda-toolkit" else self.projects.get(name)

    def test_absent_metapackage_is_waiting(self):
        with patch.object(watcher, "fetch_json", return_value=None):
            ready, output = watcher.check(self.args, {})
        self.assertFalse(ready)
        self.assertIn("Waiting for cuda-toolkit 13.5.0", output)

    def test_declared_ranges_and_platform_markers(self):
        with patch.object(watcher, "fetch_json", side_effect=self.fetch):
            ready, output = watcher.check(self.args, {})
        self.assertTrue(ready)
        self.assertIn("13.4.92", output)
        windows_file = next(
            line
            for line in output.splitlines()
            if "nvidia-file" in line and "windows-amd64" in line
        )
        self.assertIn("N/A", windows_file)

    def test_waits_for_usable_wheel_then_preserves_first_seen_on_restart(self):
        files = self.projects["nvidia-runtime"]["releases"]["13.5.21"]
        files[:] = [
            wheel("nvidia_runtime", "13.5.21", "manylinux_2_28_aarch64"),
            wheel("nvidia_runtime", "13.5.21", yanked=True),
            wheel("nvidia_runtime", "13.5.21", python="py2"),
            wheel("nvidia_runtime", "13.5.21", requires_python=">=3.13"),
            wheel("nvidia_runtime", "13.5.21", packagetype="sdist"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "history.json"
            history = {}
            with patch.object(watcher, "fetch_json", side_effect=self.fetch):
                ready, output = watcher.check(self.args, history)
                self.assertFalse(ready)
                self.assertIn("MISSING", output)
                files.append(wheel("nvidia_runtime", "13.5.21"))
                ready, _ = watcher.check(self.args, history)
                self.assertTrue(ready)
                watcher.save_history(state, history)
                restored = json.loads(state.read_text())
                ready, _ = watcher.check(self.args, restored)
            self.assertTrue(ready)
            self.assertEqual(restored, history)

    def test_empty_target_is_not_reported_ready(self):
        self.args.extras = ["cufile"]
        with (
            patch.object(watcher, "fetch_json", side_effect=self.fetch),
            self.assertRaisesRegex(ValueError, "No components apply to windows"),
        ):
            watcher.check(self.args, {})

    def test_once_exit_codes_and_network_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            argv = ["13.5.0", "--once", "--state", str(Path(directory) / "state.json")]
            with (
                patch.object(watcher, "fetch_json", return_value=None),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(watcher.main(argv), 1)
            with (
                patch.object(watcher, "fetch_json", side_effect=self.fetch),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(watcher.main(argv + ["--platform", "linux-x86_64"]), 0)
            errors = io.StringIO()
            with (
                patch.object(watcher, "fetch_json", side_effect=URLError("offline")),
                redirect_stderr(errors),
            ):
                self.assertEqual(watcher.main(argv), 2)
            self.assertIn("PyPI request failed", errors.getvalue())

    def test_only_http_404_means_not_published(self):
        for status in (404, 503):
            with (
                self.subTest(status=status),
                patch.object(
                    watcher,
                    "urlopen",
                    side_effect=HTTPError("https://pypi.org", status, "test", {}, None),
                ),
            ):
                if status == 404:
                    self.assertIsNone(watcher.fetch_json("cuda-toolkit", "13.5.0"))
                else:
                    with self.assertRaises(HTTPError):
                        watcher.fetch_json("cuda-toolkit", "13.5.0")


if __name__ == "__main__":
    unittest.main()
