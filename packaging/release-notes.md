Search local images, audio, video, documents, and code using Google EmbeddingGemma 2.

- Automatic NVIDIA GPU selection, with CPU fallback.
- Indexing progress, responsive Stop, and incremental resume.
- Image previews and timestamped audio/video playback.
- Local indexing and search; no files or search queries are uploaded.

**Linux x86_64 (Ubuntu 22.04 or newer):** download the `.AppImage`, make it executable, and run it. If FUSE is unavailable, start it with `APPIMAGE_EXTRACT_AND_RUN=1`.

**Windows x64:** download and run the `Setup.exe` installer. It installs for your user account without administrator privileges. The installer is unsigned.

No Python or separate FFmpeg installation is needed. On first launch, click **Download and set up** to install the verified search runtime (about 3 GB). Both builds select a compatible NVIDIA GPU automatically and fall back to CPU. Setup shows progress and can be cancelled; folders are indexed only when you click **Index folders**. Download EmbeddingGemma 2 inside the app on first use, add folders, and click **Index folders**. The model is about 1.5 GB and is downloaded separately from the application.

Download only the AppImage or installer for your platform. The `.bin` assets are runtime parts fetched automatically during setup; they do not need to be opened manually. They are split to fit GitHub's 2 GB per-file limit.

`SHA256SUMS` contains checksums for all release files. Runtime parts are also verified automatically by setup before extraction.
