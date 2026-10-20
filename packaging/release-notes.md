Search local images, audio, video, documents, and code using Google EmbeddingGemma 2.

- Automatic NVIDIA GPU selection, with CPU fallback.
- Indexing progress, responsive Stop, and incremental resume.
- Image previews and timestamped audio/video playback.
- Local indexing and search; no files or search queries are uploaded.

**Linux x86_64 (Ubuntu 22.04 or newer):** download the `.AppImage`, make it executable, and run it. If FUSE is unavailable, start it with `APPIMAGE_EXTRACT_AND_RUN=1`.

**Windows x64:** download and run the `Setup.exe` installer. It installs for your user account without administrator privileges. The installer is unsigned.

No Python or separate FFmpeg installation is needed. Both builds include a GPU-capable runtime and use CPU when a compatible GPU/driver is unavailable. Download EmbeddingGemma 2 inside the app on first use, add folders, and click **Index folders**. The model is about 1.5 GB and is downloaded separately from the application.

`SHA256SUMS` contains checksums for both downloads.
