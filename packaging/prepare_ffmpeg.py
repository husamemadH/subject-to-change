"""Download a checksum-verified LGPL shared FFmpeg build for Windows builds/development."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "vendor/ffmpeg")
    args = parser.parse_args()
    api = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
    request = urllib.request.Request(api, headers={"User-Agent": "subject-to-change-packaging"})
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    name = "ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip"
    asset = next(item for item in release["assets"] if item["name"] == name)
    expected = asset.get("digest", "")
    if not expected.startswith("sha256:"):
        raise RuntimeError("FFmpeg asset has no published SHA-256 digest")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / name
        download = urllib.request.Request(
            asset["browser_download_url"], headers={"User-Agent": "subject-to-change-packaging"}
        )
        with urllib.request.urlopen(download, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with archive.open("rb") as stream:
            actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError("FFmpeg checksum verification failed")
        with zipfile.ZipFile(archive) as bundle:
            # Strip the archive's top-level directory; reject any path traversal.
            for entry in bundle.infolist():
                parts = Path(entry.filename).parts[1:]
                if not parts or entry.is_dir() or ".." in parts:
                    continue
                relative = Path(*parts)
                if (
                    relative.parts[0] not in {"bin", "doc"}
                    and "license" not in relative.name.lower()
                ):
                    continue
                target = args.output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
    (args.output / "SOURCE.json").write_text(
        json.dumps(
            {
                "url": asset["browser_download_url"],
                "sha256": actual,
                "release": release["html_url"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if not list((args.output / "bin").glob("avcodec-*.dll")):
        raise RuntimeError("The FFmpeg shared DLLs were not found")
    print(f"FFmpeg shared libraries ready: {args.output.resolve()}")


if __name__ == "__main__":
    main()
