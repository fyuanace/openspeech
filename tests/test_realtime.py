import asyncio
import base64
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_platform.config import missing_credentials
from voice_platform.realtime import QwenRealtimeSession
from voice_platform.providers import ProviderError
from voice_platform.realtime_device import captured_chunks
from voice_platform.server import create_app

CONFIG = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
CONFIG["server"]["token"] = "test-token"
HEADERS = {"Authorization": "Bearer test-token"}


class FakeSession:
    def __init__(self):
        self.frames = []
        self.closed = False
        self.rounds = 0

    async def open(self):
        pass

    async def append(self, pcm):
        self.frames.append(pcm)

    async def respond(self):
        self.rounds += 1
        yield {"type": "audio.delta", "audio": base64.b64encode(b"\x01\x00" * 100).decode()}
        yield {"type": "text.delta", "text": "答复"}
        yield {"type": "turn.done"}

    async def close(self):
        self.closed = True


class RealtimeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []

        def factory(config):
            session = FakeSession()
            self.sessions.append(session)
            return session

        self.client = TestClient(create_app(CONFIG, realtime_factory=factory))

    def start(self, ws, session="session"):
        ws.send_json({"type": "session.start", "device_id": "pc", "session_id": session})
        self.assertEqual(ws.receive_json()["type"], "session.ready")

    def test_streaming_two_turns_preserves_session_and_audio_order(self):
        with self.client.websocket_connect("/v1/realtime", headers=HEADERS) as ws:
            self.start(ws)
            for turn in ["one", "two"]:
                ws.send_json({"type": "turn.start", "turn_id": turn})
                ws.send_bytes(b"\x01\x00" * 800)
                ws.send_bytes(b"\x02\x00" * 800)
                ws.send_json({"type": "input.commit", "turn_id": turn})
                events = [ws.receive_json() for _ in range(3)]
                self.assertEqual([e["type"] for e in events], ["audio.delta", "text.delta", "turn.done"])
                self.assertTrue(all(e["turn_id"] == turn and e["session_id"] == "session" for e in events))
            ws.send_json({"type": "session.close"})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(self.sessions[0].rounds, 2)
        self.assertEqual(self.sessions[0].frames, [b"\x01\x00" * 800, b"\x02\x00" * 800] * 2)
        self.assertTrue(self.sessions[0].closed)

    def test_authentication_and_mode_endpoints(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/v1/realtime"):
                pass
        self.assertEqual(self.client.post("/v1/turns", headers=HEADERS).status_code, 409)
        capabilities = self.client.get("/v1/capabilities", headers=HEADERS).json()
        self.assertEqual(capabilities["mode"], "s2s")
        self.assertTrue(capabilities["streaming"])
        self.assertEqual(self.sessions, [])

    def test_bad_pcm_closes_upstream(self):
        with self.client.websocket_connect("/v1/realtime", headers=HEADERS) as ws:
            self.start(ws)
            ws.send_json({"type": "turn.start", "turn_id": "one"})
            ws.send_bytes(b"odd")
            self.assertEqual(ws.receive_json()["stage"], "protocol")
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertTrue(self.sessions[0].closed)
        self.assertEqual(self.sessions[0].frames, [])

    def test_missing_key_reports_s2s_without_initializing_cascade(self):
        config = copy.deepcopy(CONFIG)
        config["stt"]["provider"] = "invalid-unused-provider"
        client = TestClient(create_app(config))
        health = client.get("/health").json()
        self.assertEqual(set(health["missing"]), {"dashscope_api_key", "dashscope_workspace_id"})
        with client.websocket_connect("/v1/realtime", headers=HEADERS) as ws:
            ws.send_json({"type": "session.start", "device_id": "pc", "session_id": "s"})
            event = ws.receive_json()
            self.assertEqual(event["stage"], "s2s")
            self.assertIn("DASHSCOPE_API_KEY", event["error"])

    def test_disconnect_during_response_cancels_provider(self):
        closed = asyncio.Event()

        class Slow(FakeSession):
            async def respond(self):
                yield {"type": "text.delta", "text": "开始"}
                await asyncio.sleep(60)

            async def close(self):
                closed.set()

        client = TestClient(create_app(CONFIG, realtime_factory=lambda c: Slow()))
        with client.websocket_connect("/v1/realtime", headers=HEADERS) as ws:
            self.start(ws)
            ws.send_json({"type": "turn.start", "turn_id": "one"})
            ws.send_bytes(bytes(3200))
            ws.send_json({"type": "input.commit", "turn_id": "one"})
            self.assertEqual(ws.receive_json()["type"], "text.delta")
            ws.send_json({"type": "session.close"})
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertTrue(closed.is_set())

    def test_mode_credentials_are_independent(self):
        config = copy.deepcopy(CONFIG)
        config["s2s"].update(api_key="test", workspace_id="test")
        self.assertEqual(missing_credentials(config), [])
        config["dialogue"]["mode"] = "cascade"
        self.assertEqual(set(missing_credentials(config)), {"doubao_credentials", "llm_credentials"})

    def test_device_stream_keeps_preroll_and_each_live_frame_once(self):
        count = 0

        def process(pcm):
            nonlocal count
            count += 1
            return count == 40

        detector = SimpleNamespace(process=process, reset=lambda: None)
        frames = [int(i + 1000).to_bytes(2, "little") * 320 for i in range(75)]
        frames += [bytes(640)] * 60

        class Stream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def __enter__(self):
                for frame in frames:
                    self.callback(frame, 320, None, SimpleNamespace(input_overflow=False))

            def __exit__(self, *args):
                pass

        with patch("voice_platform.realtime_device.sd.RawInputStream", Stream):
            pieces = list(captured_chunks(CONFIG, detector))
        self.assertEqual(pieces[0], b"".join(frames[:40]))
        self.assertEqual(b"".join(pieces), b"".join(frames))


class QwenProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_model_response_is_not_reported_as_success(self):
        peer = QwenRealtimeSession(CONFIG)
        peer.send = AsyncMock()
        peer.wait_for = AsyncMock()
        peer.receive = AsyncMock(return_value={"type": "response.done", "response": {"status": "failed"}})
        with self.assertRaises(ProviderError):
            _ = [event async for event in peer.respond()]

    async def test_completed_without_audio_is_rejected(self):
        peer = QwenRealtimeSession(CONFIG)
        peer.send = AsyncMock()
        peer.wait_for = AsyncMock()
        peer.receive = AsyncMock(return_value={"type": "response.done", "response": {"status": "completed"}})
        with self.assertRaises(ProviderError):
            _ = [event async for event in peer.respond()]

    async def test_official_handshake_commit_ack_and_stream_mapping(self):
        class Socket:
            def __init__(self):
                self.events = asyncio.Queue()
                self.events.put_nowait({"type": "session.created"})
                self.sent = []
                self.closed = False

            async def send(self, data):
                event = json.loads(data)
                self.sent.append(event)
                kind = event["type"]
                if kind == "session.update":
                    self.events.put_nowait({"type": "session.updated"})
                if kind == "input_audio_buffer.commit":
                    self.events.put_nowait({"type": "input_audio_buffer.committed"})
                if kind == "response.create":
                    for response in [{"type": "response.created", "response": {"id": "r"}},
                                     {"type": "response.audio.delta", "response_id": "r", "delta": "AAA="},
                                     {"type": "response.audio_transcript.delta", "response_id": "r", "delta": "你好"},
                                     {"type": "response.done", "response": {"id": "r", "status": "completed"}}]:
                        self.events.put_nowait(response)

            async def recv(self):
                return json.dumps(await self.events.get())

            async def close(self):
                self.closed = True

        config = copy.deepcopy(CONFIG)
        config["s2s"].update(api_key="test", workspace_id="workspace123")
        socket = Socket()
        with patch("voice_platform.realtime.connect", AsyncMock(return_value=socket)) as connect:
            peer = QwenRealtimeSession(config)
            await peer.open()
            await peer.append(bytes(3200))
            events = [event async for event in peer.respond()]
            await peer.close()
        self.assertIn("workspace123.cn-beijing.maas.aliyuncs.com", connect.call_args.args[0])
        self.assertEqual([e["type"] for e in socket.sent],
                         ["session.update", "input_audio_buffer.append", "input_audio_buffer.commit", "response.create"])
        self.assertIsNone(socket.sent[0]["session"]["turn_detection"])
        self.assertEqual(socket.sent[0]["session"]["audio"]["input"]["format"]["sample_rate"], 16000)
        self.assertEqual([e["type"] for e in events], ["audio.delta", "text.delta", "turn.done"])
        self.assertTrue(socket.closed)


if __name__ == "__main__":
    unittest.main()
