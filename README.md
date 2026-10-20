# Subject to Change

Search local images, video, audio, documents, and code with Google EmbeddingGemma 2.
Embeddings and searches run offline after the model is downloaded.

Download the Linux `.AppImage` or Windows `Setup.exe` from
[GitHub Releases](https://github.com/husamemadH/subject-to-change/releases/latest).
On Linux, make the AppImage executable and open it; if FUSE is unavailable, run
`APPIMAGE_EXTRACT_AND_RUN=1 ./Subject-to-Change-*.AppImage`. On Windows, run the installer.
Python and FFmpeg are included; the model downloads separately on first use.

To run from source, install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone https://github.com/husamemadH/subject-to-change.git
cd subject-to-change
uv sync --extra model
```

Audio/video requires FFmpeg shared libraries. On Ubuntu, run
`sudo apt install ffmpeg libegl1 libopengl0 libpulse0`; on other Linux distributions, install the
equivalent packages. On Windows, run this once to download and configure the shared DLLs:

```powershell
uv run --no-project python packaging/prepare_ffmpeg.py
```

Start the app:

```sh
uv run --extra model subject-to-change
```

Click **Download model** once, **Add folder**, then **Index folders**. Enter a description to
search. A compatible GPU is selected automatically, with CPU fallback; the device is shown in
the app. **Stop indexing** interrupts the job and keeps completed files. Nothing indexes
automatically.

Images have visual previews; audio/video results include playback and matching timestamps.
Documents include text-based PDF, DOCX, ODT, HTML, Markdown, CSV, and plain text. Common code
and configuration formats are supported. Exact words mode searches media filenames only.
Images/documents are limited to 25 MiB; audio/video to 2 GiB.

To build a desktop package, run these on the target operating system after setup:

```sh
uv run --extra model --extra package python packaging/build.py
uv run --extra model --extra package python packaging/check.py --media
```

Keep the entire `dist/subject-to-change` folder together, or wrap it as a release download:

```sh
# Linux (requires squashfs-tools and desktop-file-utils)
uv run --extra model --extra package python packaging/release.py --format appimage
# Windows (requires Inno Setup 6)
uv run --extra model --extra package python packaging/release.py --format windows
```

The **Desktop packages** GitHub Actions workflow builds and checks both formats, including
real model inference, and stages a draft release when publishing is enabled or a `v*` tag
is pushed. Release downloads include GPU support, CPU fallback, FFmpeg, and SHA-256 checksums.

Run tests:

```sh
uv run --extra model --extra dev ruff check .
uv run --extra model --extra dev python -m unittest discover -s tests -q
```
