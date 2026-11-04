import io
import wave
from collections import deque
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 35 + 4096


class WakeDetector(Protocol):
    def process(self, pcm: bytes) -> bool: ...
    def reset(self) -> None: ...


def encode_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return output.getvalue()


def decode_wav(data: bytes, input_audio: bool = False) -> tuple[bytes, int, int]:
    if input_audio and len(data) > MAX_AUDIO_BYTES:
        raise ValueError("音频超过 35 秒限制。")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate, channels = wav.getframerate(), wav.getnchannels()
            if wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise ValueError("需要 16-bit PCM WAV 音频。")
            if input_audio and (rate != SAMPLE_RATE or channels != 1):
                raise ValueError("输入需为 16 kHz 单声道 WAV。")
            expected = wav.getnframes() * channels * 2
            pcm = wav.readframes(wav.getnframes())
            if not pcm or len(pcm) != expected:
                raise ValueError("音频为空或文件不完整。")
            if input_audio and len(pcm) > SAMPLE_RATE * 2 * 35:
                raise ValueError("音频超过 35 秒限制。")
            return pcm, rate, channels
    except (wave.Error, EOFError) as error:
        raise ValueError("无法读取 WAV 音频。") from error


class UtteranceCapture:
    """Retain pre-wake samples and splice live frames without overlap."""

    def __init__(self, detector: WakeDetector, settings: dict):
        self.detector = detector
        frame_seconds = FRAME_SAMPLES / SAMPLE_RATE
        pre_roll = float(settings["pre_roll_seconds"])
        maximum = float(settings["max_seconds"])
        silence = float(settings["silence_seconds"])
        if not (0.5 <= pre_roll < maximum <= 30 and 0.2 <= silence <= 5):
            raise ValueError("请检查缓冲、静音结束和最长录音时长。")
        self.ring = deque(maxlen=round(pre_roll / frame_seconds))
        self.max_frames = round(maximum / frame_seconds)
        self.silence_frames = round(silence / frame_seconds)
        self.threshold = float(settings["energy_threshold"])
        self.frames: list[bytes] | None = None
        self.quiet = 0

    def reset(self) -> None:
        self.ring.clear()
        self.frames = None
        self.quiet = 0
        self.detector.reset()

    def feed(self, pcm: bytes) -> bytes | None:
        if len(pcm) != FRAME_SAMPLES * 2:
            raise ValueError("音频分片长度不正确。")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        silent = float(np.sqrt(np.mean(samples * samples))) < self.threshold
        if self.frames is None:
            self.ring.append(pcm)
            if not self.detector.process(pcm):
                return None
            self.frames = list(self.ring)
            self.ring.clear()
        else:
            self.frames.append(pcm)
        self.quiet = self.quiet + 1 if silent else 0
        if self.quiet >= self.silence_frames or len(self.frames) >= self.max_frames:
            utterance = b"".join(self.frames)
            self.reset()
            return utterance
        return None
