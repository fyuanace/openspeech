import asyncio
import base64
import hashlib
import re
import secrets
import threading
import time
from collections import OrderedDict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .audio import MAX_AUDIO_BYTES, decode_wav
from .providers import ProviderError, build_providers
from .config import dialogue_mode, missing_credentials
from .realtime_server import register_realtime
from .realtime import build_realtime


def remove_wake_prefix(text: str, word: str) -> str:
    pattern = r"^\s*" + r"\s*".join(re.escape(c) for c in word if not c.isspace())
    if not word.strip():
        return text.strip()
    return re.sub(pattern + r"[\s，,。.!！?？：:]*", "", text, count=1).strip()


class TurnService:
    def __init__(self, config: dict, providers: tuple):
        self.config = config
        self.stt, self.llm, self.tts = providers
        self.lock = threading.Lock()
        self.active: set[tuple] = set()
        self.history: OrderedDict = OrderedDict()
        self.results: OrderedDict = OrderedDict()

    def run(self, device: str, session: str, turn: str, wav: bytes, wake_word: str) -> tuple[int, dict]:
        conversation = (device, session)
        key = (device, session, turn)
        digest = hashlib.sha256(wav + wake_word.encode()).hexdigest()
        with self.lock:
            now = time.monotonic()
            self.results = OrderedDict((k, v) for k, v in self.results.items() if now - v[0] < 300)
            if key in self.results:
                _, previous_digest, status, result = self.results[key]
                if previous_digest != digest:
                    return 409, {"error": "同一轮次 ID 对应的音频不同。", "stage": "protocol"}
                return status, result
            if conversation in self.active or len(self.active) >= 2:
                return 409, {"error": "会话处理中，请稍后再说。", "stage": "session"}
            self.active.add(conversation)
            messages = list(self.history.get(conversation, []))
        result = {"version": 1, "device_id": device, "session_id": session, "turn_id": turn}
        status = 200
        try:
            text = remove_wake_prefix(self.stt.transcribe(wav), wake_word)
            if not text:
                raise ProviderError("stt", "未识别到指令，请在唤醒词后说出内容。")
            if len(text) > 4000:
                raise ProviderError("stt", "指令过长，请缩短后重试。")
            messages.append({"role": "user", "content": text})
            prompt = {"role": "system", "content": self.config["llm"]["system_prompt"]}
            reply = self.llm.reply([prompt, *messages])
            if len(reply) > 4000:
                raise ProviderError("llm", "回复过长，请调整模型输出长度。")
            messages.append({"role": "assistant", "content": reply})
            result.update({"text": text, "reply": reply, "audio_format": "wav"})
            try:
                result["audio"] = base64.b64encode(self.tts.synthesize(reply)).decode("ascii")
            except ProviderError as error:
                result.update({"audio": None, "audio_error": str(error)})
            with self.lock:
                self.history[conversation] = messages[-12:]
                self.history.move_to_end(conversation)
                while len(self.history) > 64:
                    self.history.popitem(last=False)
        except ProviderError as error:
            status = 502
            result.update({"error": str(error), "stage": error.stage})
        except Exception:
            status = 500
            result.update({"error": "本轮处理失败，请检查模型配置。", "stage": "server"})
        finally:
            with self.lock:
                self.active.discard(conversation)
                self.results[key] = (time.monotonic(), digest, status, result)
                while len(self.results) > 32:
                    self.results.popitem(last=False)
        return status, result


def create_app(config: dict, providers: tuple | None = None, realtime_factory=build_realtime) -> FastAPI:
    app = FastAPI(title="Voice Platform", version="0.1.0", docs_url=None, redoc_url=None)
    mode = dialogue_mode(config)
    service = TurnService(config, providers or build_providers(config)) if mode == "cascade" else None
    register_realtime(app, config, realtime_factory)

    def authorize(request: Request) -> None:
        expected = "Bearer " + config["server"]["token"]
        if not secrets.compare_digest(request.headers.get("authorization", "").encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(401, "平台连接凭据不匹配，请同步设备端配置。")

    @app.get("/health")
    async def health():
        missing = missing_credentials(config)
        return {"status": "configuration_required" if missing else "configured",
                "missing": missing, "mode": mode, "version": 1}

    @app.get("/v1/capabilities")
    async def capabilities(request: Request):
        authorize(request)
        if mode == "s2s":
            return {"version": 1, "mode": mode, "modes": ["s2s", "cascade"],
                    "endpoint": "/v1/realtime", "streaming": True, "barge_in": False,
                    "input": {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1, "sample_width": 2},
                    "output": {"format": "pcm_s16le", "sample_rate": 24000, "channels": 1, "sample_width": 2},
                    "providers": {"s2s": config.get("s2s", {}).get("provider")}}
        return {"version": 1, "input": {"format": "wav", "sample_rate": 16000, "channels": 1,
                                        "sample_width": 2, "max_seconds": 35},
                "output": {"format": "wav"}, "streaming": False, "mode": mode,
                "modes": ["s2s", "cascade"], "endpoint": "/v1/turns", "barge_in": False,
                "providers": {key: config[key]["provider"] for key in ("stt", "llm", "tts")}}

    @app.post("/v1/turns")
    async def turn(request: Request):
        authorize(request)
        if service is None:
            raise HTTPException(409, "当前为 s2s 模式，请使用 /v1/realtime。")
        identifiers = [request.headers.get(name, "") for name in ("x-device-id", "x-session-id", "x-turn-id")]
        if any(not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", value) for value in identifiers):
            raise HTTPException(400, "需要有效的设备、会话和轮次 ID。")
        if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
            raise HTTPException(415, "需要 audio/wav 音频。")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_AUDIO_BYTES:
                raise HTTPException(413, "音频超过大小限制。")
        try:
            decode_wav(bytes(body), input_audio=True)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        # HTTP header values are ASCII; the device sends its wake phrase as UTF-8 hex.
        try:
            wake_word = bytes.fromhex(request.headers.get("x-wake-word", "")).decode("utf-8")
            if len(wake_word) > 40:
                raise ValueError("Wake phrase too long")
        except (ValueError, UnicodeError) as error:
            raise HTTPException(400, "唤醒词编码无效。") from error
        status, result = await asyncio.to_thread(service.run, *identifiers, bytes(body), wake_word)
        return JSONResponse(result, status_code=status)

    return app
