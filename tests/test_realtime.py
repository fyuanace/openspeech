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

    def test_terminal_tool_closes_only_its_own_session(self):
        class Ending(FakeSession):
            async def respond(self):
                yield {"type": "session.ended"}

        ending = Ending()
        other = FakeSession()
        peers = iter((ending, other))
        client = TestClient(create_app(CONFIG, realtime_factory=lambda config: next(peers)))
        with client.websocket_connect("/v1/realtime", headers=HEADERS) as ws:
            self.start(ws)
            with client.websocket_connect("/v1/realtime", headers=HEADERS) as second:
                self.start(second, session="other")
                ws.send_json({"type": "turn.start", "turn_id": "one"})
                ws.send_bytes(bytes(3200))
                ws.send_json({"type": "input.commit", "turn_id": "one"})
                self.assertEqual(ws.receive_json(), {"type": "session.ended", "session_id": "session", "turn_id": "one"})
                with self.assertRaises(WebSocketDisconnect):
                    ws.receive_json()
                self.assertTrue(ending.closed)
                self.assertFalse(other.closed)

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


class RealtimeListenerTests(unittest.TestCase):
    def test_follow_up_skips_wake_until_speech_wait_expires(self):
        self.check_follow_up_end(exit_command=False)

    def test_local_exit_closes_conversation_without_uploading_command(self):
        self.check_follow_up_end(exit_command=True)

    def test_server_exit_returns_listener_to_waiting_without_a_reply(self):
        self.check_follow_up_end(exit_command=False, server_exit=True)

    def check_follow_up_end(self, *, exit_command, server_exit=False):
        from voice_platform.audio import SpeechStartDetector
        from voice_platform.listener_control import ListenerControl, ListenerStopped
        from voice_platform.realtime_device import listen_realtime

        events = []
        detectors = []
        received = []
        closed = []
        first = SimpleNamespace(model=object())

        def capture(config, detector, control, *, start_timeout):
            detectors.append((detector, start_timeout))
            if len(detectors) <= 2:
                yield b"first" if len(detectors) == 1 else b"follow-up"
            elif len(detectors) == (3 if exit_command or server_exit else 4):
                raise ListenerStopped()

        client = SimpleNamespace(start_turn=lambda: None, append=received.append,
                                 __exit__=lambda *args: closed.append(True))
        control = ListenerControl(lambda state, detail: events.append(state))
        with patch("voice_platform.realtime_device.RealtimeClient", return_value=client), \
                patch("voice_platform.realtime_device.VoskWakeDetector", return_value=first), \
                patch("voice_platform.realtime_device.captured_chunks", side_effect=capture), \
                patch("voice_platform.realtime_device.is_conversation_exit", return_value=exit_command), \
                patch("voice_platform.realtime_device.play_stream", side_effect=[False, server_exit]):
            with self.assertRaises(ListenerStopped):
                listen_realtime(CONFIG, control=control)
        self.assertEqual(received, [b"first"] if exit_command else [b"first", b"follow-up"])
        if exit_command:
            end_events = ["conversation_ended"]
        else:
            end_events = ["processing", "conversation_ended" if server_exit else "follow_up"]
        self.assertEqual(events, ["waiting", "awakened", "processing", "follow_up",
                                  "recording", *end_events, "waiting"])
        self.assertEqual(detectors[0], (first, None))
        self.assertEqual(detectors[-1], (first, None))
        self.assertIsInstance(detectors[1][0], SpeechStartDetector)
        self.assertEqual([timeout for _, timeout in detectors[1:-1]], [20] if exit_command or server_exit else [20, 20])
        self.assertEqual(len(closed), 2)  # Conversation end and listener shutdown.

    def test_capture_keeps_audio_while_realtime_session_is_opening(self):
        from threading import Event

        from voice_platform.listener_control import ListenerStopped
        from voice_platform.realtime_device import listen_realtime

        captured = Event()
        frames = [b"prefix", b"live frame"]
        received = []

        def capture(*args, **kwargs):
            yield from frames
            captured.set()

        def start_turn():
            self.assertTrue(captured.wait(2), "Capture waited for the network")

        client = SimpleNamespace(start_turn=start_turn, append=received.append, __exit__=lambda *args: None)
        with patch("voice_platform.realtime_device.RealtimeClient", return_value=client), \
                patch("voice_platform.realtime_device.VoskWakeDetector"), \
                patch("voice_platform.realtime_device.captured_chunks", side_effect=capture), \
                patch("voice_platform.realtime_device.play_stream", side_effect=ListenerStopped):
            with self.assertRaises(ListenerStopped):
                listen_realtime(CONFIG)
        self.assertEqual(received, frames)


class QwenProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_input_transcript_is_matched_to_current_audio_item(self):
        peer = QwenRealtimeSession(CONFIG)
        peer.send = AsyncMock()
        peer.wait_for = AsyncMock(return_value={"item_id": "current"})
        peer.receive = AsyncMock(side_effect=[
            {"type": "response.audio.delta", "delta": "AAA="},
            {"type": "response.done", "response": {"status": "completed"}},
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "previous", "transcript": "旧话"},
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "current", "transcript": "这句"},
        ])
        events = [event async for event in peer.respond()]
        self.assertEqual(events[1:], [{"type": "input.transcript", "text": "这句"}, {"type": "turn.done"}])

    async def test_exit_tool_is_terminal_and_cannot_select_another_session(self):
        for arguments in ("{}", '{"session_id":"other"}'):
            peer = QwenRealtimeSession(CONFIG)
            peer.send = AsyncMock()
            peer.wait_for = AsyncMock()
            peer.receive = AsyncMock(return_value={
                "type": "response.function_call_arguments.done",
                "name": "end_conversation", "arguments": arguments,
            })
            if arguments == "{}":
                self.assertEqual([event async for event in peer.respond()], [{"type": "session.ended"}])
            else:
                with self.assertRaises(ProviderError):
                    _ = [event async for event in peer.respond()]
            self.assertEqual(peer.send.await_count, 2)  # Commit and initial response only.

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
