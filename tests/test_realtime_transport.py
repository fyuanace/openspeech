"""Real loopback sockets: native device -> platform -> simulated Qwen service."""
import base64
import json
import socket
import threading
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import WebSocket, WebSocketDisconnect
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from voice_platform.device import server_mode
from voice_platform.device_tools import create_device_tools
from voice_platform.realtime_device import RealtimeClient
from voice_platform.server import create_app


class LookupResult(BaseModel):
    location: str


class RealtimeTransportTests(unittest.TestCase):
    def test_native_client_through_platform_to_upstream(self):
        config = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
        config["server"]["token"] = "local-platform-test"
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config["device"]["server_url"] = f"http://127.0.0.1:{port}"
        config["s2s"].update(url=f"ws://127.0.0.1:{port}/fake-qwen", api_key="local-provider-test")
        config["mcp_servers"] = {
            "device": {"url": f"http://127.0.0.1:{port}/device-tools/mcp", "tools": "lookup set_volume"},
            "warehouse": {"url": f"http://127.0.0.1:{port}/warehouse/mcp", "tools": "lookup"},
        }
        app = create_app(config)
        received = []
        reply = b"\x11\x00" * 500
        upstream_closed = threading.Event()
        volume_changes = []
        tool_outputs = []
        lookups = []

        def set_volume(percent):
            volume_changes.append(percent)
            return 29  # Verify the actual device result is forwarded.

        mcp = create_device_tools(set_volume)
        warehouse = FastMCP("warehouse", stateless_http=True, json_response=True)

        @mcp.tool()
        def lookup(name: str) -> LookupResult:
            lookups.append(("device", name))
            return LookupResult(location="PC")

        @warehouse.tool(name="lookup")
        def warehouse_lookup(name: str) -> LookupResult:
            lookups.append(("warehouse", name))
            return LookupResult(location="A-03")

        @mcp.tool()
        def unapproved() -> str:
            return "not enabled"

        @asynccontextmanager
        async def lifespan(_):
            async with mcp.session_manager.run(), warehouse.session_manager.run():
                yield

        app.mount("/device-tools", mcp.streamable_http_app())
        app.mount("/warehouse", warehouse.streamable_http_app())
        app.router.lifespan_context = lifespan

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
                        assert {tool["name"] for tool in event["session"]["tools"]} == {
                            "end_conversation", "mcp_device_t_set_volume", "mcp_device_t_lookup",
                            "mcp_warehouse_t_lookup",
                        }
                        await ws.send_json({"type": "session.updated"})
                    elif kind == "input_audio_buffer.append":
                        received.append(base64.b64decode(event["audio"]))
                    elif kind == "input_audio_buffer.commit":
                        await ws.send_json({"type": "input_audio_buffer.committed"})
                    elif kind == "response.create":
                        if not tool_outputs:
                            await ws.send_json({"type": "response.created", "response": {"id": "tool-response"}})
                            await ws.send_json({"type": "response.function_call_arguments.done", "response_id": "tool-response",
                                                "name": "mcp_device_t_set_volume", "call_id": "call-volume", "arguments": '{"percent":30}'})
                            for _ in range(2):
                                await ws.send_json({
                                    "type": "response.function_call_arguments.done", "response_id": "tool-response",
                                    "name": "mcp_warehouse_t_lookup", "call_id": "call-mcp", "arguments": '{"name":"M3"}',
                                })
                            await ws.send_json({"type": "response.done", "response": {"id": "tool-response", "status": "completed"}})
                            continue
                        await ws.send_json({"type": "response.created", "response": {"id": "r1"}})
                        await ws.send_json({"type": "response.audio.delta", "response_id": "r1",
                                            "delta": base64.b64encode(reply).decode()})
                        await ws.send_json({"type": "response.audio_transcript.delta", "delta": "收到"})
                        await ws.send_json({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
                    elif kind == "conversation.item.create":
                        item = event["item"]
                        assert item["type"] == "function_call_output"
                        tool_outputs.append((item["call_id"], json.loads(item["output"])))
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
            with RealtimeClient(config) as client:
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
            self.assertEqual(lookups, [("warehouse", "M3")])
            self.assertEqual(tool_outputs, [
                ("call-volume", {"ok": True, "data": {"percent": 29}}),
                ("call-mcp", {"ok": True, "data": {"location": "A-03"}}),
            ])
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
