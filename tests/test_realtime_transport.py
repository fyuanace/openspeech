"""Real loopback sockets: native device -> platform -> simulated Qwen service."""
import base64
import json
import socket
import threading
import time
import unittest
from pathlib import Path

import uvicorn
from fastapi import WebSocket, WebSocketDisconnect

from voice_platform.device import server_mode
from voice_platform.realtime_device import RealtimeClient
from voice_platform.server import create_app


class RealtimeTransportTests(unittest.TestCase):
    def test_native_client_through_platform_to_upstream(self):
        config = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
        config["server"]["token"] = "local-platform-test"
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config["device"]["server_url"] = f"http://127.0.0.1:{port}"
        config["s2s"].update(url=f"ws://127.0.0.1:{port}/fake-qwen", api_key="local-provider-test")
        app = create_app(config)
        received = []
        reply = b"\x11\x00" * 500
        upstream_closed = threading.Event()
        volume_changes = []
        tool_outputs = []

        @app.websocket("/fake-qwen")
        async def fake_qwen(ws: WebSocket):
            if ws.headers.get("authorization") != "Bearer local-provider-test":
                await ws.close(code=1008)
                return
            await ws.accept()
            await ws.send_json({"type": "session.created"})
            try:
                while True:
                    event = await ws.receive_json()
                    kind = event["type"]
                    if kind == "session.update":
                        assert "set_volume" in {tool["name"] for tool in event["session"]["tools"]}
                        await ws.send_json({"type": "session.updated"})
                    elif kind == "input_audio_buffer.append":
                        received.append(base64.b64decode(event["audio"]))
                    elif kind == "input_audio_buffer.commit":
                        await ws.send_json({"type": "input_audio_buffer.committed"})
                    elif kind == "response.create":
                        if not tool_outputs:
                            await ws.send_json({"type": "response.created", "response": {"id": "tool-response"}})
                            await ws.send_json({"type": "response.function_call_arguments.done", "response_id": "tool-response",
                                                "name": "set_volume", "call_id": "call-volume", "arguments": '{"percent":30}'})
                            await ws.send_json({"type": "response.done", "response": {"id": "tool-response", "status": "completed"}})
                            continue
                        await ws.send_json({"type": "response.created", "response": {"id": "r1"}})
                        await ws.send_json({"type": "response.audio.delta", "response_id": "r1",
                                            "delta": base64.b64encode(reply).decode()})
                        await ws.send_json({"type": "response.audio_transcript.delta", "delta": "收到"})
                        await ws.send_json({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
                    elif kind == "conversation.item.create":
                        item = event["item"]
                        assert item["type"] == "function_call_output" and item["call_id"] == "call-volume"
                        tool_outputs.append(json.loads(item["output"]))
            except WebSocketDisconnect:
                upstream_closed.set()

        server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="websockets-sansio", access_log=False))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started)
            self.assertEqual(server_mode(config), "s2s")
            pcm = b"\x01\x00" * 4000
            def set_volume(percent):
                volume_changes.append(percent)
                return 29  # Verify the actual device result is forwarded.

            with RealtimeClient(config, set_volume=set_volume) as client:
                client.start_turn()
                initial_session = client.session
                client.append(pcm)
                list(client.finish())
                client.start_turn()
                self.assertEqual(client.session, initial_session)
                client.append(pcm)
                list(client.finish())
                client.last_turn_end -= 181
                client.start_turn()
                self.assertNotEqual(client.session, initial_session)
                client.append(pcm)
                events = list(client.finish())
            self.assertEqual(b"".join(received), pcm * 3)
            self.assertEqual(volume_changes, [30])
            self.assertEqual(tool_outputs, [{"ok": True, "percent": 29}])
            self.assertEqual(b"".join(e["pcm"] for e in events if e["type"] == "audio"), reply)
            self.assertEqual([e["text"] for e in events if e["type"] == "text"], ["收到"])
            self.assertTrue(upstream_closed.wait(3))
        finally:
            server.should_exit = True
            worker.join(5)
            listener.close()
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
