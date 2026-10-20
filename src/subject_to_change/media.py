"""Decode local media into bounded inputs for EmbeddingGemma 2."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .readers import media_kind
from .runtime import configure_native_libraries


class MediaReadError(ValueError):
    pass


@dataclass
class MediaPart:
    inputs: dict
    location: str
    number: int
    total: int


def timestamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def read_media(path: Path, cancelled: Callable[[], bool] = lambda: False) -> Iterator[MediaPart]:
    """Stream every clip; videos use sampled frames and their audio track when present."""
    from PIL import Image, ImageOps

    kind = media_kind(path)
    try:
        if path.is_symlink():
            raise MediaReadError("Symbolic links are not indexed")
        if kind == "image":
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((1536, 1536))
            yield MediaPart({"image": image}, "Image", 1, 1)
            return

        configure_native_libraries()
        from torchcodec.decoders import AudioDecoder, VideoDecoder

        if kind == "audio":
            audio = AudioDecoder(str(path), sample_rate=16000, num_channels=1)
            duration = audio.metadata.duration_seconds
            offset = audio.metadata.begin_stream_seconds or 0
            video = None
            step = 10
        elif kind == "video":
            video = VideoDecoder(str(path), num_ffmpeg_threads=1, seek_mode="approximate")
            duration = video.metadata.duration_seconds
            offset = video.metadata.begin_stream_seconds or 0
            step = 10
            try:
                audio = AudioDecoder(str(path), sample_rate=16000, num_channels=1)
            except (RuntimeError, ValueError):
                audio = None  # Silent videos are valid inputs.
        else:
            raise MediaReadError("Unsupported media type")
        if duration is None or not math.isfinite(duration) or duration <= 0:
            raise MediaReadError("Cannot determine media duration")
        total = math.ceil(duration / step)
        for index in range(total):
            if cancelled():
                return
            start, end = index * step, min((index + 1) * step, duration)
            inputs = {}
            if video is not None:
                seconds = [offset + start + n for n in range(math.ceil(end - start))]
                frames = video.get_frames_played_at(seconds=seconds).data
                images = []
                for frame in frames:
                    image = Image.fromarray(frame.permute(1, 2, 0).numpy())
                    image.thumbnail((768, 768))
                    images.append(image)
                inputs["video"] = images
            if audio is not None:
                audio_start = (audio.metadata.begin_stream_seconds or 0) + start
                audio_end = min(
                    audio_start + end - start,
                    (audio.metadata.begin_stream_seconds or 0) + audio.metadata.duration_seconds,
                )
                if audio_end > audio_start:
                    inputs["audio"] = (
                        audio.get_samples_played_in_range(audio_start, audio_end)
                        .data.squeeze(0)
                        .numpy()
                    )
            label = f"{kind.title()} · {timestamp(start)}–{timestamp(end)}"
            yield MediaPart(inputs, label, index + 1, total)
    except ImportError:
        raise
    except Exception as error:
        raise MediaReadError(f"Cannot decode {kind or 'media'}: {error}") from error
