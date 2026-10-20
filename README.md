# Subject to Change

Search local images, video, audio, documents, and code with Google EmbeddingGemma 2.
Embeddings and searches run offline after the model is downloaded.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

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

Keep the entire `dist/subject-to-change` folder together. On Windows, open
`subject-to-change.exe`; the package includes FFmpeg DLLs but downloads the model separately.
Windows CI checks are configured; native Windows validation is still pending.

Run tests:

```sh
uv run --extra model --extra dev ruff check .
uv run --extra model --extra dev python -m unittest discover -s tests -q
```
