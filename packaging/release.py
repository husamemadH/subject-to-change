"""Wrap the frozen application as an AppImage or Windows installer, with SHA-256 checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_ASSET_BYTES = 2 * 1024**3


def version() -> str:
    value = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"Unsupported release version: {value}")
    tag = os.environ.get("RELEASE_TAG", "")
    if tag and tag != f"v{value}":
        raise ValueError(f"Release tag {tag} does not match project version {value}")
    return value


def download_asset(repo: str, name: str, destination: Path) -> Path:
    headers = {"User-Agent": "subject-to-change-packaging"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest", headers=headers
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    asset = next(asset for asset in release["assets"] if asset["name"] == name)
    expected = asset.get("digest", "")
    if not expected.startswith("sha256:"):
        raise RuntimeError(f"No published SHA-256 digest for {name}")
    request = urllib.request.Request(
        asset["browser_download_url"], headers={"User-Agent": "subject-to-change-packaging"}
    )
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    with destination.open("rb") as stream:
        digest = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected:
        raise RuntimeError(f"Checksum verification failed: {name}")
    destination.chmod(0o755)
    return destination


class PartWriter:
    def __init__(self, output: Path, prefix: str):
        self.output = output
        self.prefix = prefix
        self.stream = None
        self.parts = []
        self.position = 0
        self.limit = 1536 * 1024**2

    def write(self, data):
        written = len(data)
        view = memoryview(data)
        while view:
            if self.stream is None or self.stream.tell() == self.limit:
                self.close()
                target = self.output / f"{self.prefix}-{len(self.parts) + 1:03}.bin"
                self.parts.append(target)
                self.stream = target.open("wb")
            count = min(len(view), self.limit - self.stream.tell())
            self.stream.write(view[:count])
            self.position += count
            view = view[count:]
        return written

    def tell(self):
        return self.position

    def flush(self):
        if self.stream is not None:
            self.stream.flush()

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def prepare_setup(payload: Path, output: Path, release_version: str, platform: str) -> Path:
    prefix = f"Subject-to-Change-{release_version}-{platform}-runtime"
    for stale in output.glob(prefix + "-*.bin*"):
        stale.unlink()
    writer = PartWriter(output, prefix)
    unpacked_size = 0
    try:
        with tarfile.open(
            fileobj=writer, mode="w|gz", compresslevel=6, dereference=False
        ) as archive:
            for path in sorted(payload.rglob("*")):
                archive.add(path, arcname=path.relative_to(payload).as_posix(), recursive=False)
                if path.is_file() and not path.is_symlink():
                    unpacked_size += path.stat().st_size
    finally:
        writer.close()
    parts = []
    for part in writer.parts:
        checksum(part)
        with part.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        parts.append(
            {
                "name": part.name,
                "size": part.stat().st_size,
                "sha256": digest,
                "url": f"https://github.com/husamemadH/subject-to-change/releases/download/"
                f"v{release_version}/{part.name}",
            }
        )
    manifest = ROOT / "build/runtime.json"
    manifest.write_text(
        json.dumps(
            {
                "version": release_version,
                "unpacked_size": unpacked_size,
                "executable": "subject-to-change.exe"
                if platform == "windows-x64"
                else "subject-to-change",
                "parts": parts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--windowed",
            "--onedir",
            "--name",
            "subject-to-change-setup",
            "--exclude-module",
            "torch",
            "--exclude-module",
            "transformers",
            "--exclude-module",
            "sentence_transformers",
            "--add-data",
            f"{manifest}{os.pathsep}.",
            "--add-data",
            f"{ROOT / 'src/subject_to_change/icon.svg'}{os.pathsep}.",
            str(ROOT / "packaging/bootstrap.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    return ROOT / "dist/subject-to-change-setup"


def appimage(payload: Path, output: Path, release_version: str) -> Path:
    appdir = ROOT / "build/Subject-to-Change.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    appdir.mkdir(parents=True)
    # Hard links avoid duplicating the multi-GB GPU runtime on the build runner.
    shutil.copytree(payload, appdir / "app", symlinks=True, copy_function=os.link)
    (appdir / "AppRun").write_text(
        "#!/bin/sh\n"
        'HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'exec "$HERE/app/subject-to-change-setup" "$@"\n',
        encoding="utf-8",
    )
    (appdir / "AppRun").chmod(0o755)
    (appdir / "subject-to-change.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Subject to Change\n"
        "Comment=Search local files by meaning\nExec=subject-to-change\n"
        "Icon=subject-to-change\nCategories=Utility;\nTerminal=false\n",
        encoding="utf-8",
    )
    shutil.copyfile(ROOT / "src/subject_to_change/icon.svg", appdir / "subject-to-change.svg")
    (appdir / ".DirIcon").symlink_to("subject-to-change.svg")
    target = output / f"Subject-to-Change-{release_version}-x86_64.AppImage"
    with tempfile.TemporaryDirectory(prefix="subject-to-change-appimagetool-") as directory:
        temporary = Path(directory)
        tool = download_asset(
            "AppImage/appimagetool",
            "appimagetool-x86_64.AppImage",
            temporary / "appimagetool.AppImage",
        )
        runtime = download_asset(
            "AppImage/type2-runtime", "runtime-x86_64", temporary / "runtime-x86_64"
        )
        subprocess.run(
            [str(tool), "--appimage-extract"], cwd=temporary, check=True, stdout=subprocess.DEVNULL
        )
        command = [
            str(temporary / "squashfs-root/AppRun"),
            "--runtime-file",
            str(runtime),
            "--comp",
            "zstd",
            "--mksquashfs-opt",
            "-Xcompression-level",
            "--mksquashfs-opt",
            "15",
            "--mksquashfs-opt",
            "-b",
            "--mksquashfs-opt",
            "1M",
            str(appdir),
            str(target),
        ]
        subprocess.run(
            command, check=True, env={**os.environ, "ARCH": "x86_64", "VERSION": release_version}
        )
    target.chmod(0o755)
    return target


def windows(payload: Path, output: Path, release_version: str) -> Path:
    compiler = shutil.which("ISCC.exe")
    if compiler is None:
        compiler = str(
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Inno Setup 6/ISCC.exe"
        )
    subprocess.run(
        [
            compiler,
            "/Qp",
            f"/DAppVersion={release_version}",
            f"/DAppSource={payload}",
            f"/DReleaseOutput={output}",
            str(ROOT / "packaging/windows.iss"),
        ],
        check=True,
    )
    return output / f"Subject-to-Change-{release_version}-windows-x64-Setup.exe"


def checksum(artifact: Path) -> None:
    if artifact.stat().st_size >= MAX_ASSET_BYTES:
        raise RuntimeError(f"Release asset exceeds GitHub's 2 GiB limit: {artifact.name}")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    artifact.with_name(artifact.name + ".sha256").write_text(
        f"{digest}  {artifact.name}\n", encoding="utf-8"
    )
    print(f"Release asset: {artifact} ({artifact.stat().st_size / 1024**2:.1f} MiB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("appimage", "windows"), required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    release_version = version()
    payload = ROOT / "dist/subject-to-change"
    executable = payload / (
        "subject-to-change.exe" if args.format == "windows" else "subject-to-change"
    )
    if not executable.is_file():
        raise FileNotFoundError("Build the frozen application with packaging/build.py first")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = prepare_setup(
        payload,
        output,
        release_version,
        "windows-x64" if args.format == "windows" else "linux-x86_64",
    )
    artifact = (
        appimage(payload, output, release_version)
        if args.format == "appimage"
        else windows(payload, output, release_version)
    )
    checksum(artifact)


if __name__ == "__main__":
    main()
