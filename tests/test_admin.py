import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from voice_platform.server import create_app


EXAMPLE = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
ORIGIN = {"Origin": "http://127.0.0.1:8765"}


class AdminTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config.local.json"
        config = copy.deepcopy(EXAMPLE)
        config["server"]["token"] = "local-platform-token"
        config["s2s"].update(api_key="secret-credential",
                             url="wss://maas.qianwenaiapi.com/api-ws/v1/realtime")
        self.path.write_text(json.dumps(config), encoding="utf-8")
        self.app = create_app(config, config_path=self.path)
        self.client = TestClient(self.app, base_url=ORIGIN["Origin"], client=("127.0.0.1", 30000))

    def test_save_updates_runtime_and_preserves_secret_and_unrelated_settings(self):
        view = self.client.get("/admin/api/config")
        self.assertEqual(view.status_code, 200)
        self.assertNotIn("secret-credential", view.text)
        self.assertNotIn("local-platform-token", view.text)
        self.assertTrue(view.json()["saved_credentials"]["s2s.api_key"])
        response = self.client.put("/admin/api/config", headers=ORIGIN, json={
            "mode": "cascade", "settings": {"llm": {"model": "test-model", "api_key": "new-secret"}},
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("new-secret", response.text)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["s2s"]["api_key"], "secret-credential")
        self.assertEqual(saved["llm"]["api_key"], "new-secret")
        self.assertEqual(self.client.get("/health").json()["mode"], "cascade")
        self.assertEqual(self.app.state.runtime[0]["llm"]["model"], "test-model")

    def test_admin_rejects_remote_forged_origin_and_credential_forwarding(self):
        remote = TestClient(self.app, base_url=ORIGIN["Origin"], client=("192.0.2.1", 30000))
        self.assertEqual(remote.get("/admin/api/config").status_code, 403)
        self.assertEqual(self.client.get("/admin", headers={"Host": "external.example"}).status_code, 403)
        payload = {"mode": "s2s", "settings": {"s2s": {"url": "wss://other.example/socket", "api_key": ""}}}
        self.assertEqual(self.client.put("/admin/api/config", headers={"Origin": "http://other.example"}, json=payload).status_code, 403)
        original = self.path.read_bytes()
        self.assertEqual(self.client.put("/admin/api/config", headers=ORIGIN, json=payload).status_code, 400)
        self.assertEqual(self.path.read_bytes(), original)

    def test_s2s_save_enables_connection_check_on_updated_configuration(self):
        config = copy.deepcopy(EXAMPLE)
        config["server"]["token"] = "local-platform-token"
        self.path.write_text(json.dumps(config), encoding="utf-8")
        peer = AsyncMock()
        used = []

        def factory(settings):
            used.append(settings["s2s"]["api_key"])
            return peer

        app = create_app(config, realtime_factory=factory, config_path=self.path)
        client = TestClient(app, base_url=ORIGIN["Origin"], client=("127.0.0.1", 30000))
        self.assertEqual(client.post("/admin/api/check", headers=ORIGIN).status_code, 400)
        saved = client.put("/admin/api/config", headers=ORIGIN, json={
            "mode": "s2s", "settings": {"s2s": {"url": "wss://maas.qianwenaiapi.com/api-ws/v1/realtime",
                                              "api_key": "test-new-key"}},
        })
        self.assertEqual(saved.json()["missing"], [])
        self.assertEqual(client.post("/admin/api/check", headers=ORIGIN).json(), {"connected": True})
        self.assertEqual(used, ["test-new-key"])
        peer.open.assert_awaited_once()
        peer.close.assert_awaited_once()

    def test_realtime_turn_is_persisted_and_visible_after_reopening_the_app(self):
        class Peer:
            async def open(self):
                pass

            async def append(self, pcm):
                pass

            async def respond(self):
                yield {"type": "input.transcript", "text": "用户指令"}
                yield {"type": "text.delta", "text": "小美答复"}
                yield {"type": "turn.done"}

            async def close(self):
                pass

        config = self.app.state.runtime[0]
        app = create_app(config, realtime_factory=lambda settings: Peer(), config_path=self.path)
        with TestClient(app, base_url=ORIGIN["Origin"], client=("127.0.0.1", 30000)) as client:
            with client.websocket_connect("/v1/realtime", headers={"Authorization": "Bearer local-platform-token"}) as ws:
                ws.send_json({"type": "session.start", "session_id": "s", "device_id": "test-device"})
                self.assertEqual(ws.receive_json()["type"], "session.ready")
                self.assertEqual(client.get("/admin/api/status").json()["active_sessions"], 1)
                ws.send_json({"type": "turn.start", "turn_id": "t"})
                ws.send_bytes(bytes(3200))
                ws.send_json({"type": "input.commit", "turn_id": "t"})
                self.assertEqual(ws.receive_json()["type"], "text.delta")
                self.assertEqual(ws.receive_json()["type"], "turn.done")
                ws.send_json({"type": "session.close"})
                self.assertEqual(ws.receive()["type"], "websocket.close")
        reopened = create_app(config, config_path=self.path)
        client = TestClient(reopened, base_url=ORIGIN["Origin"], client=("127.0.0.1", 30000))
        row, = client.get("/admin/api/conversations").json()["items"]
        self.assertEqual((row["user_text"], row["reply"], row["outcome"], row["device_id"]),
                         ("用户指令", "小美答复", "completed", "test-device"))
        self.assertEqual(client.get("/admin/api/status").json()["active_sessions"], 0)
