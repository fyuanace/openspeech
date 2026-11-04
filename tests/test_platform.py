import base64
import copy
import json
import threading
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import numpy as np
from fastapi.testclient import TestClient

from voice_platform.audio import FRAME_SAMPLES, UtteranceCapture, decode_wav, encode_wav
from voice_platform.providers import ChatCompletionsLLM, DoubaoSTT, ProviderError
from voice_platform.server import TurnService, create_app, remove_wake_prefix

CONFIG = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
CONFIG["server"]["token"] = "test-token"
WAV = encode_wav(np.full(1600, 500, dtype="<i2").tobytes())


class StubSTT:
    def __init__(self):
        self.calls = 0

    def transcribe(self, wav):
        self.calls += 1
        return "你好 助手，帮我找螺丝刀"


class StubLLM:
    def __init__(self):
        self.messages = []

    def reply(self, messages):
        self.messages = messages
        return "请在货物列表中查看位置。"


class StubTTS:
    def synthesize(self, text):
        return WAV


class CaptureTests(unittest.TestCase):
    def test_wake_phrase_uses_model_vocabulary_and_rejects_unknown_words(self):
        from voice_platform.wake import wake_tokens

        model = SimpleNamespace(vosk_model_find_word=lambda word: 1 if word in {"你好", "助手"} else -1)
        self.assertEqual(wake_tokens(model, "你好助手"), ["你好", "助手"])
        with self.assertRaises(ValueError):
            wake_tokens(model, "你好管家")

    def test_wake_test_finishes_without_calling_any_server(self):
        from voice_platform.device import listen

        detector = SimpleNamespace(process=lambda pcm: True, reset=lambda: None, last_text="你好助手")
        frames = [np.full(FRAME_SAMPLES, 1000, dtype="<i2").tobytes()] * 20
        frames += [bytes(FRAME_SAMPLES * 2)] * 60

        class InputStream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def __enter__(self):
                for frame in frames:
                    self.callback(frame, FRAME_SAMPLES, None, SimpleNamespace(input_overflow=False))
                return self

            def __exit__(self, *args):
                return False

        output = io.StringIO()
        with patch("voice_platform.device.VoskWakeDetector", return_value=detector), \
                patch("voice_platform.device.sd.RawInputStream", InputStream), \
                patch("voice_platform.device.send_turn") as send, redirect_stdout(output):
            listen(CONFIG, wake_only=True, seconds=1)
        send.assert_not_called()
        self.assertIn("唤醒测试通过", output.getvalue())

    def test_delayed_wake_preserves_prefix_and_each_frame_once(self):
        class Detector:
            count = 0

            def process(self, pcm):
                self.count += 1
                return self.count == 40

            def reset(self):
                self.count = 0

        capture = UtteranceCapture(Detector(), CONFIG["device"])
        frames = [np.full(FRAME_SAMPLES, i + 1000, dtype="<i2").tobytes() for i in range(75)]
        frames += [bytes(FRAME_SAMPLES * 2)] * 60
        output = None
        for frame in frames:
            output = capture.feed(frame)
        self.assertEqual(output, b"".join(frames))
        self.assertIsNone(capture.frames)

    def test_wake_word_removal_is_prefix_only(self):
        self.assertEqual(remove_wake_prefix("你好 助手，打开灯", "你好助手"), "打开灯")
        self.assertEqual(remove_wake_prefix("解释你好助手的意思", "你好助手"), "解释你好助手的意思")

    def test_reject_truncated_and_wrong_rate_audio(self):
        with self.assertRaises(ValueError):
            decode_wav(WAV[:-2], input_audio=True)
        with self.assertRaises(ValueError):
            decode_wav(encode_wav(bytes(320), rate=8000), input_audio=True)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.stt, self.llm, self.tts = StubSTT(), StubLLM(), StubTTS()
        self.client = TestClient(create_app(CONFIG, (self.stt, self.llm, self.tts)))
        self.headers = {"Authorization": "Bearer test-token", "Content-Type": "audio/wav",
                        "X-Device-Id": "pc", "X-Session-Id": "session", "X-Turn-Id": "turn1",
                        "X-Wake-Word": "你好助手".encode().hex()}

    def test_normal_entrypoint_and_replay(self):
        first = self.client.post("/v1/turns", headers=self.headers, content=WAV)
        self.assertEqual(first.status_code, 200)
        result = first.json()
        self.assertEqual(result["text"], "帮我找螺丝刀")
        self.assertEqual(base64.b64decode(result["audio"]), WAV)
        replay = self.client.post("/v1/turns", headers=self.headers, content=WAV)
        self.assertEqual(replay.json(), result)
        self.assertEqual(self.stt.calls, 1)
        conflict = self.client.post("/v1/turns", headers=self.headers,
                                    content=encode_wav(bytes(3200)))
        self.assertEqual(conflict.status_code, 409)

    def test_authentication_and_invalid_audio(self):
        self.assertEqual(self.client.post("/v1/turns", content=WAV).status_code, 401)
        self.assertEqual(self.client.post("/v1/turns", headers=self.headers, content=b"invalid").status_code, 400)
        self.assertEqual(self.stt.calls, 0)

    def test_tts_failure_keeps_text_without_repeating_llm(self):
        with patch.object(self.tts, "synthesize", side_effect=ProviderError("tts", "音色缺失")):
            result = self.client.post("/v1/turns", headers=self.headers, content=WAV).json()
        self.assertIsNone(result["audio"])
        self.assertEqual(result["audio_error"], "音色缺失")
        self.assertIn("reply", result)

    def test_history_isolated_per_device_and_session(self):
        self.client.post("/v1/turns", headers=self.headers, content=WAV)
        headers = {**self.headers, "X-Turn-Id": "turn2"}
        self.client.post("/v1/turns", headers=headers, content=WAV)
        self.assertEqual(len(self.llm.messages), 4)
        headers["X-Device-Id"] = "other-pc"
        self.client.post("/v1/turns", headers=headers, content=WAV)
        self.assertEqual(len(self.llm.messages), 2)

    def test_concurrent_turn_is_rejected_then_active_state_recovers(self):
        started, release = threading.Event(), threading.Event()
        original = self.stt.transcribe

        def slow_transcribe(wav):
            started.set()
            release.wait(5)
            return original(wav)

        self.stt.transcribe = slow_transcribe
        service = TurnService(CONFIG, (self.stt, self.llm, self.tts))
        worker = threading.Thread(target=service.run, args=("pc", "s", "one", WAV, "你好助手"))
        worker.start()
        self.assertTrue(started.wait(2))
        try:
            status, _ = service.run("pc", "s", "two", WAV, "你好助手")
            self.assertEqual(status, 409)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(service.active), 0)


class ProviderTests(unittest.TestCase):
    def test_doubao_wire_format_and_status(self):
        config = {**CONFIG["stt"], "api_key": "secret"}
        response = httpx.Response(200, headers={"X-Api-Status-Code": "20000000"},
                                  json={"result": {"text": "你好"}}, request=httpx.Request("POST", config["url"]))
        with patch("httpx.post", return_value=response) as post:
            self.assertEqual(DoubaoSTT(config).transcribe(WAV), "你好")
        self.assertEqual(base64.b64decode(post.call_args.kwargs["json"]["audio"]["data"]), WAV)
        self.assertEqual(post.call_args.kwargs["headers"]["X-Api-Key"], "secret")
        response.headers["X-Api-Status-Code"] = "45000001"
        with patch("httpx.post", return_value=response), self.assertRaises(ProviderError):
            DoubaoSTT(config).transcribe(WAV)

    def test_chat_provider_supports_configured_endpoint(self):
        config = copy.deepcopy(CONFIG["llm"])
        config.update(api_key="local", url="http://127.0.0.1:9000/v1/chat/completions", model="local-model")
        response = httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "答复"}}]},
                                  request=httpx.Request("POST", config["url"]))
        with patch("httpx.post", return_value=response) as post:
            self.assertEqual(ChatCompletionsLLM(config).reply([{"role": "user", "content": "你好"}]), "答复")
        self.assertEqual(post.call_args.args[0], config["url"])


if __name__ == "__main__":
    unittest.main()
