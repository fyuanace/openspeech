"""Optional device measurements; all elapsed times use one local monotonic clock."""
from collections import deque
import time

from .audio import FRAME_SAMPLES, SAMPLE_RATE, audio_rms


class CaptureTiming:
    def __init__(self, settings, *, follow_up=False):
        self.entry = "follow_up" if follow_up else "wake"
        self.threshold = float(settings["energy_threshold"])
        self.history = deque(maxlen=round((0.5 if follow_up else settings["pre_roll_seconds"]) / 0.02))
        self.start = self.last_voice = self.trigger = self.end = None

    def observe(self, pcm, frame_end, *, triggered=False, completed=False, now=None):
        now = time.monotonic() if now is None else now
        voiced = audio_rms(pcm) >= self.threshold
        frame_start = frame_end - FRAME_SAMPLES / SAMPLE_RATE
        self.history.append((voiced, frame_start, frame_end))
        if triggered:
            self.trigger = now
            # Ignore isolated energy spikes. This estimates speech, not phoneme alignment.
            run = 0
            for active, begin, end in self.history:
                run = run + 1 if active else 0
                if run >= 5 and self.start is None:
                    self.start = begin - 4 * FRAME_SAMPLES / SAMPLE_RATE
                if active:
                    self.last_voice = end
        elif self.trigger is not None and voiced:
            recent = list(self.history)[-5:]
            if self.start is None and len(recent) == 5 and all(frame[0] for frame in recent):
                self.start = recent[0][1]
            self.last_voice = frame_end
        if completed:
            self.end = now

    def at_commit(self, now):
        if self.end is None or self.start is None or self.last_voice is None:
            return {"entry": self.entry}
        ms = lambda value: max(0, round(value * 1000))
        result = {"entry": self.entry,
                  "speech_ms": ms(self.last_voice - self.start),
                  "endpoint_ms": ms(self.end - self.last_voice),
                  "submit_ms": ms(now - self.end),
                  "input_elapsed_ms": ms(now - self.start)}
        if self.entry == "wake":
            result["wake_ms"] = ms(self.trigger - self.start)
        return result


def device_measurements(value, *, playback=False):
    """Ignore invalid optional telemetry, never accept server timing fields from a device."""
    if not isinstance(value, dict):
        return {}
    fields = ("reply_wait_ms", "playback_ms", "total_ms") if playback else (
        "speech_ms", "wake_ms", "endpoint_ms", "submit_ms", "input_elapsed_ms")
    result = {key: value[key] for key in fields
              if type(value.get(key)) is int and 0 <= value[key] <= 600_000}
    if not playback and value.get("entry") in ("wake", "follow_up", "file"):
        result["entry"] = value["entry"]
    return result
