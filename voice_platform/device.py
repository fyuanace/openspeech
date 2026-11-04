import base64
import queue
import threading
import uuid

import httpx
import numpy as np
import sounddevice as sd

from .audio import FRAME_SAMPLES, SAMPLE_RATE, UtteranceCapture, decode_wav, encode_wav
from .wake import VoskWakeDetector


def send_turn(config: dict, wav: bytes, session: str, turn: str) -> dict:
    settings = config["device"]
    headers = {"Authorization": "Bearer " + config["server"]["token"],
               "Content-Type": "audio/wav", "X-Device-Id": settings["id"],
               "X-Session-Id": session, "X-Turn-Id": turn,
               "X-Wake-Word": settings["wake_word"].encode("utf-8").hex()}
    try:
        response = httpx.post(settings["server_url"].rstrip("/") + "/v1/turns",
                              headers=headers, content=wav, timeout=300)
        result = response.json()
        if response.is_error:
            raise ValueError(result.get("error") or result.get("detail") or "语音服务请求失败。")
        if (result.get("session_id"), result.get("turn_id")) != (session, turn):
            raise ValueError("回复与当前会话不匹配。")
        return result
    except (httpx.HTTPError, ValueError) as error:
        if isinstance(error, httpx.HTTPError):
            raise ValueError("无法连接语音服务，请检查地址和服务状态。") from error
        raise


def play_result(result: dict, output_device=None) -> None:
    print("识别", result["text"])
    print("回复", result["reply"])
    if result.get("audio"):
        pcm, rate, channels = decode_wav(base64.b64decode(result["audio"], validate=True))
        samples = np.frombuffer(pcm, dtype="<i2").reshape(-1, channels)
        sd.play(samples, rate, device=output_device, blocking=True)
    elif result.get("audio_error"):
        print(result["audio_error"])


def listen(config: dict) -> None:
    settings = config["device"]
    detector = VoskWakeDetector(settings["wake_model"], settings["wake_word"])
    capture = UtteranceCapture(detector, settings)
    frames: queue.Queue = queue.Queue(maxsize=250)
    overflow = threading.Event()
    session = str(uuid.uuid4())

    def callback(data, count, timing, status):
        if status.input_overflow:
            overflow.set()
        try:
            frames.put_nowait(bytes(data))
        except queue.Full:
            overflow.set()

    print(f"等待唤醒：{settings['wake_word']}。可直接连说内容，Ctrl+C 退出。")
    while True:
        capture.reset()
        while not frames.empty():
            frames.get_nowait()
        overflow.clear()
        # Close capture during generation/playback to prevent self-wake. Barge-in is not enabled.
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES,
                               device=settings.get("input_device"), channels=1, dtype="int16",
                               callback=callback):
            while True:
                try:
                    frame = frames.get(timeout=5)
                except queue.Empty as error:
                    raise ValueError("麦克风未返回音频，请检查设备连接与系统权限。") from error
                if overflow.is_set():
                    print("采音缓冲溢出，请重新唤醒。")
                    break
                was_waiting = capture.frames is None
                utterance = capture.feed(frame)
                if was_waiting and capture.frames is not None:
                    print("已唤醒，继续说。")
                if utterance is not None:
                    break
        if overflow.is_set():
            continue
        print("正在处理…")
        try:
            result = send_turn(config, encode_wav(utterance), session, str(uuid.uuid4()))
            play_result(result, settings.get("output_device"))
        except (ValueError, sd.PortAudioError) as error:
            print(str(error))
        print("等待唤醒。")
