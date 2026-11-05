import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voice_platform.realtime_device import RealtimeClient, play_stream
from voice_platform.turn_timing import CaptureTiming, device_measurements

SETTINGS = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))["device"]
VOICE = (3000).to_bytes(2, "little") * 320
QUIET = bytes(640)


class TurnTimingTests(unittest.TestCase):
    def test_speech_excludes_preroll_and_endpoint_and_wake_overlaps_speech(self):
        timing = CaptureTiming(SETTINGS)
        # Three seconds of silence, then 1s wake/command, then 0.8s silence.
        for index in range(240):
            end = (index + 1) * 0.02
            timing.observe(VOICE if 150 <= index < 200 else QUIET, end,
                           triggered=index == 174, completed=index == 239, now=end)
        result = timing.at_commit(4.9)
        self.assertEqual(result, {"entry": "wake", "speech_ms": 1000, "wake_ms": 500,
                                  "endpoint_ms": 800, "submit_ms": 100, "input_elapsed_ms": 1900})
        self.assertEqual(result["input_elapsed_ms"], sum(result[key] for key in ("speech_ms", "endpoint_ms", "submit_ms")))

    def test_follow_up_does_not_count_as_another_wake(self):
        timing = CaptureTiming(SETTINGS, follow_up=True)
        for index in range(50):
            end = (index + 1) * 0.02
            timing.observe(VOICE if index < 10 else QUIET, end,
                           triggered=index == 4, completed=index == 49, now=end)
        measured = timing.at_commit(1.1)
        self.assertEqual(measured["speech_ms"], 200)
        self.assertNotIn("wake_ms", measured)
        self.assertEqual(measured["entry"], "follow_up")

    def test_unknown_speech_is_missing_instead_of_zero(self):
        timing = CaptureTiming(SETTINGS)
        for index in range(40):
            timing.observe(QUIET, index * 0.02, triggered=index == 1, completed=index == 39, now=index * 0.02)
        self.assertEqual(timing.at_commit(2), {"entry": "wake"})

    def test_optional_telemetry_is_bounded_and_cannot_replace_server_measurements(self):
        self.assertEqual(device_measurements({"speech_ms": True, "wake_ms": -1, "endpoint_ms": float("inf"),
                                              "submit_ms": 600001, "input_elapsed_ms": "5", "first_audio_ms": 1}), {})
        self.assertEqual(device_measurements({"entry": "wake", "reply_wait_ms": 10}, playback=True), {"reply_wait_ms": 10})
        self.assertEqual(device_measurements(None), {})

    def test_report_is_sent_after_sound_stream_drains_and_uses_current_turn(self):
        client = RealtimeClient({})
        client.ws = SimpleNamespace(send=lambda value: events.append(json.loads(value)))
        client.turn = "current"
        client.commit_at = 10
        client.input_timing = {"input_elapsed_ms": 3000}
        client.finish = lambda: iter([{"type": "audio", "pcm": bytes(4800)}])
        events = []

        class Stream:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def write(self, pcm):
                events.append("write")

            def __exit__(self, *args):
                events.append("drained")

        with patch("voice_platform.realtime_device.sd.RawOutputStream", Stream), \
                patch("voice_platform.realtime_device.time.monotonic", side_effect=[10.6, 12.4]):
            self.assertFalse(play_stream(client, None))
        self.assertEqual(events, ["write", "drained", {"type": "turn.metrics", "turn_id": "current",
            "device_timing": {"reply_wait_ms": 600, "playback_ms": 1800, "total_ms": 5400}}])
