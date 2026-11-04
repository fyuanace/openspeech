import base64
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Protocol

import httpx

from .audio import decode_wav


class ProviderError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


class STTProvider(Protocol):
    def transcribe(self, wav: bytes) -> str: ...


class LLMProvider(Protocol):
    def reply(self, messages: list[dict]) -> str: ...


class TTSProvider(Protocol):
    def synthesize(self, text: str) -> bytes: ...


def request_json(stage: str, url: str, headers: dict, payload: dict) -> httpx.Response:
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=90)
        response.raise_for_status()
        response.json()
        return response
    except (httpx.HTTPError, ValueError) as error:
        raise ProviderError(stage, f"{stage} 服务请求失败，请检查网络、密钥和服务权限。") from error


class DoubaoSTT:
    def __init__(self, config: dict):
        self.config = config

    def transcribe(self, wav: bytes) -> str:
        config = self.config
        headers = {"X-Api-Resource-Id": config["resource_id"],
                   "X-Api-Request-Id": str(uuid.uuid4()), "X-Api-Sequence": "-1"}
        if config.get("api_key"):
            headers["X-Api-Key"] = config["api_key"]
        elif config.get("app_id") and config.get("access_token"):
            headers.update({"X-Api-App-Key": config["app_id"],
                            "X-Api-Access-Key": config["access_token"]})
        else:
            raise ProviderError("stt", "豆包凭据缺失，请配置 DOUBAO_API_KEY 或旧版 APP ID 和 Access Token。")
        payload = {"user": {"uid": "voice-platform"},
                   "audio": {"data": base64.b64encode(wav).decode("ascii")},
                   "request": {"model_name": "bigmodel"}}
        response = request_json("stt", config["url"], headers, payload)
        code = response.headers.get("X-Api-Status-Code")
        if code == "20000003":
            return ""
        if code != "20000000":
            raise ProviderError("stt", f"豆包识别失败，状态码 {code or '缺失'}，请检查资源权限。")
        result = response.json().get("result", {})
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise ProviderError("stt", "豆包返回了无效识别结果。")
        return text.strip()


class VoskSTT:
    """A local STT alternative exercising the same provider contract."""

    def __init__(self, model_path: str):
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        self.model = Model(model_path)

    def transcribe(self, wav: bytes) -> str:
        from vosk import KaldiRecognizer
        pcm, rate, _ = decode_wav(wav, input_audio=True)
        recognizer = KaldiRecognizer(self.model, rate)
        pieces = []
        for offset in range(0, len(pcm), 8000):
            if recognizer.AcceptWaveform(pcm[offset:offset + 8000]):
                pieces.append(json.loads(recognizer.Result()).get("text", ""))
        pieces.append(json.loads(recognizer.FinalResult()).get("text", ""))
        return " ".join(pieces).strip()


class ChatCompletionsLLM:
    def __init__(self, config: dict):
        self.config = config

    def reply(self, messages: list[dict]) -> str:
        config = self.config
        if not config.get("api_key"):
            raise ProviderError("llm", "对话模型凭据缺失，请配置 DEEPSEEK_API_KEY。")
        payload = {**config.get("options", {}), "model": config["model"],
                   "messages": messages, "stream": False}
        response = request_json("llm", config["url"],
                                {"Authorization": f"Bearer {config['api_key']}"}, payload)
        try:
            choice = response.json()["choices"][0]
            text = choice["message"]["content"]
            if choice.get("finish_reason") != "stop" or not isinstance(text, str) or not text.strip():
                raise ValueError("Incomplete response")
            return text.strip()
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError("llm", "对话模型未返回完整回复，请调整模型或输出长度。") from error


class WindowsTTS:
    def __init__(self, voice: str = ""):
        self.voice = voice

    def synthesize(self, text: str) -> bytes:
        if os.name != "nt":
            raise ProviderError("tts", "当前 TTS 适配器需要 Windows，请替换 TTS 提供方。")
        with tempfile.TemporaryDirectory(prefix="voice-tts-") as directory:
            source = Path(directory) / "text.txt"
            target = Path(directory) / "speech.wav"
            source.write_text(text, encoding="utf-8")
            environment = {**os.environ, "VOICE_TTS_INPUT": str(source),
                           "VOICE_TTS_OUTPUT": str(target), "VOICE_TTS_VOICE": self.voice}
            powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            try:
                subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-File",
                                str(Path(__file__).with_name("synthesize.ps1"))],
                               env=environment, check=True, capture_output=True, timeout=90,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                audio = target.read_bytes()
                decode_wav(audio)
                return audio
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                raise ProviderError("tts", "语音合成失败，请检查 Windows 已安装的音色。") from error


def build_providers(config: dict) -> tuple[STTProvider, LLMProvider, TTSProvider]:
    stt_factories = {"doubao": lambda: DoubaoSTT(config["stt"]),
                     "vosk": lambda: VoskSTT(config["device"]["wake_model"])}
    llm_factories = {"chat-completions": lambda: ChatCompletionsLLM(config["llm"])}
    tts_factories = {"windows": lambda: WindowsTTS(config["tts"].get("voice", ""))}
    try:
        return (stt_factories[config["stt"]["provider"]](),
                llm_factories[config["llm"]["provider"]](),
                tts_factories[config["tts"]["provider"]]())
    except KeyError as error:
        raise ValueError("配置引用了尚未实现的模型提供方。") from error
