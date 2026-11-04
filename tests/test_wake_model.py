"""Opt-in local model regression: set VOICE_TEST_WAKE_MODEL=1 on Windows."""
import os
import unittest
from pathlib import Path

from voice_platform.audio import UtteranceCapture, decode_wav
from voice_platform.config import load_config
from voice_platform.providers import WindowsTTS
from voice_platform.wake import VoskWakeDetector, is_conversation_exit


@unittest.skipUnless(os.environ.get("VOICE_TEST_WAKE_MODEL") == "1", "requires local Vosk model and Windows TTS")
class WakeModelTests(unittest.TestCase):
    def test_local_exit_recognizes_complete_command_only(self):
        config = load_config(Path(__file__).parents[1] / "config.local.json")
        detector = VoskWakeDetector(config["device"]["wake_model"], config["device"]["wake_word"])
        tts = WindowsTTS(config["tts"].get("voice", ""))
        for phrase, expected in (("退出", True), ("结束对话", True), ("怎么退出登录", False), ("退出登录", False)):
            with self.subTest(phrase=phrase):
                pcm, rate, channels = decode_wav(tts.synthesize(phrase))
                self.assertEqual((rate, channels), (16000, 1))
                self.assertEqual(is_conversation_exit(detector.model, [pcm, bytes(38400)]), expected)

    def test_positive_negative_phrases_and_continuous_capture(self):
        config = load_config(Path(__file__).parents[1] / "config.local.json")
        detector = VoskWakeDetector(config["device"]["wake_model"], "小美小美")
        tts = WindowsTTS(config["tts"].get("voice", ""))
        cases = [("小美小美帮我找十字螺丝刀", True), ("小美小美", True),
                 ("今天天气怎么样", False), ("帮我找十字螺丝刀", False),
                 ("请打开房间的灯", False), ("小美", False),
                 ("请你住手", False), ("你好", False)]
        for phrase, expected in cases:
            with self.subTest(phrase=phrase):
                pcm, rate, channels = decode_wav(tts.synthesize(phrase))
                self.assertEqual((rate, channels), (16000, 1))
                pcm += bytes(64000)
                pcm += bytes((-len(pcm)) % 640)
                capture = UtteranceCapture(detector, config["device"])
                capture.reset()
                triggered, captured = False, None
                for start in range(0, len(pcm), 640):
                    output = capture.feed(pcm[start:start + 640])
                    triggered = triggered or capture.frames is not None or output is not None
                    if output is not None:
                        captured = output
                        break
                self.assertEqual(triggered, expected)
                if expected:
                    self.assertIsNotNone(captured)
                    self.assertEqual(captured, pcm[:len(captured)])


if __name__ == "__main__":
    unittest.main()
