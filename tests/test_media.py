from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from threading import Event

from subject_to_change.media import MediaReadError, read_media

HAS_MEDIA = all(importlib.util.find_spec(name) for name in ("PIL", "torchcodec"))


@unittest.skipUnless(HAS_MEDIA, "Optional media dependencies are not installed")
class MediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def audio(self) -> Path:
        path = self.root / "recording.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"\0\0" * (21 * 16000))
        return path

    def test_audio_covers_the_end_of_long_recordings(self) -> None:
        parts = list(read_media(self.audio()))
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[-1].location, "Audio · 00:00:20–00:00:21")
        self.assertEqual(sum(len(p.inputs["audio"]) for p in parts), 21 * 16000)

    def test_decoding_observes_cancellation_between_clips(self) -> None:
        cancel = Event()
        iterator = read_media(self.audio(), cancel.is_set)
        next(iterator)
        cancel.set()
        self.assertEqual(list(iterator), [])

    def test_invalid_image_is_a_file_error(self) -> None:
        path = self.root / "bad.png"
        path.write_bytes(b"Not an image")
        with self.assertRaises(MediaReadError):
            list(read_media(path))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_video_clips_include_frames_audio_and_final_segment(self) -> None:
        path = self.root / "recording.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=96x64:r=1:d=12",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=16000:duration=12",
                "-shortest",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                "-y",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        parts = list(read_media(path))
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[-1].location, "Video · 00:00:10–00:00:12")
        self.assertEqual(len(parts[0].inputs["video"]), 10)
        self.assertEqual(len(parts[1].inputs["video"]), 2)
        self.assertIn("audio", parts[0].inputs)
        self.assertIn("audio", parts[1].inputs)
