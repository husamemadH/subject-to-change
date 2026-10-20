import hashlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "desktop_setup", Path(__file__).resolve().parents[1] / "packaging/bootstrap.py"
)
setup_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_module)


class SetupTests(unittest.TestCase):
    def fixture(self, root):
        contents = b"#!/bin/sh\nexit 0\n"
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            item = tarfile.TarInfo("subject-to-change")
            item.size = len(contents)
            item.mode = 0o755
            archive.addfile(item, io.BytesIO(contents))
        data = output.getvalue()
        split = len(data) // 2
        assets = root / "assets"
        assets.mkdir()
        parts = []
        for index, block in enumerate((data[:split], data[split:])):
            name = f"runtime-{index}.bin"
            (assets / name).write_bytes(block)
            parts.append(
                {"name": name, "size": len(block), "sha256": hashlib.sha256(block).hexdigest()}
            )
        manifest = {
            "version": "0.1.0",
            "unpacked_size": len(contents),
            "executable": "subject-to-change",
            "parts": parts,
        }
        return setup_module.RuntimeSetup(manifest, root / "cache", assets=assets)

    def test_verified_parts_install_atomically_and_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            setup = self.fixture(Path(directory))
            executable = setup.install()
            self.assertEqual(executable.read_bytes(), b"#!/bin/sh\nexit 0\n")
            self.assertTrue(setup.ready())
            for part in setup.assets.iterdir():
                part.unlink()
            self.assertEqual(setup.install(), executable)

    def test_modified_download_is_rejected_before_runtime_can_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            setup = self.fixture(Path(directory))
            part = setup.assets / setup.manifest["parts"][0]["name"]
            part.write_bytes(b"x" * part.stat().st_size)
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                setup.install()
            self.assertFalse(setup.ready())
            self.assertFalse(list(setup.cache.glob(".install-*")))

    def test_cancel_removes_partial_install(self):
        with tempfile.TemporaryDirectory() as directory:
            setup = self.fixture(Path(directory))
            setup.progress = lambda *args: setup.cancel.set()
            with self.assertRaises(setup_module.Cancelled):
                setup.install()
            self.assertFalse(setup.ready())
            self.assertFalse(list(setup.cache.glob(".install-*")))
