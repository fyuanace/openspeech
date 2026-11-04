import base64
import queue
import threading
import time
import uuid

import httpx
import numpy as np
import sounddevice as sd

from .audio import FRAME_SAMPLES, SAMPLE_RATE, UtteranceCapture, decode_wav, encode_wav
from .listener_control import ListenerControl
from .wake import VoskWakeDetector


def server_mode(config: dict) -> str:
    try:
        response = httpx.get(config["device"]["server_url"].rstrip("/") + "/v1/capabilities",
                             headers={"Authorization": "Bearer " + config["server"]["token"]}, timeout=10)
        response.raise_for_status()
        mode = response.json().get("mode", "cascade")
        if mode not in ("s2s", "cascade"):
            raise ValueError("服务端返回了不支持的对话模式。")
        return mode
    except httpx.HTTPError as error:
        raise ValueError("无法查询语音服务，请检查服务地址、启动状态及平台凭据。") from error


class BackendUnavailable(ValueError):
    pass


def backend_health(config: dict) -> dict:
    try:
        response = httpx.get(config["device"]["server_url"].rstrip("/") + "/health", timeout=2)
        response.raise_for_status()
        health = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise BackendUnavailable("语音服务端不可达，请检查是否已启动。") from error
    if not isinstance(health, dict):
        raise BackendUnavailable("服务端健康检查返回无效，请检查服务地址。")
    return health


def ready_backend_mode(config: dict) -> str:
    health = backend_health(config)
    if health.get("status") == "configuration_required":
        provider = "百炼 API Key 和业务空间 ID" if health.get("mode") == "s2s" else "串联模式的模型凭据"
        raise BackendUnavailable(f"服务端配置未完成，请检查{provider}。")
    if health.get("status") != "configured" or health.get("mode", "cascade") not in ("s2s", "cascade"):
        raise BackendUnavailable("服务端未就绪，请检查运行状态。")
    return health.get("mode", "cascade")


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


def play_result(result: dict, output_device=None, control=None) -> None:
    print("识别", result["text"])
    print("回复", result["reply"])
    if control:
        control.emit("transcript", result["text"])
        control.emit("reply", result["reply"])
    if result.get("audio"):
        pcm, rate, channels = decode_wav(base64.b64decode(result["audio"], validate=True))
        samples = np.frombuffer(pcm, dtype="<i2").reshape(-1, channels)
        if control is None:
            sd.play(samples, rate, device=output_device, blocking=True)
        else:
            control.emit("playing")
            sd.play(samples, rate, device=output_device, blocking=False)
            try:
                stream = sd.get_stream()
                while stream.active and not control.stop.wait(0.05):
                    pass
                control.check()
            finally:
                sd.stop()
    elif result.get("audio_error"):
        print(result["audio_error"])
        if control:
            control.emit("error", result["audio_error"])


def listen(config: dict, *, wake_only: bool = False, seconds: float = 60, control=None) -> None:
    managed = control is not None
    control = control or ListenerControl()
    mode = None
    if not wake_only:
        try:
            mode = ready_backend_mode(config)
        except BackendUnavailable:
            pass  # Local wake stays available and reports backend status after capture.
    if mode == "s2s":
        from .realtime_device import listen_realtime
        control.emit("connecting")
        return listen_realtime(config, control=control if managed else None)
    control.check()
    settings = config["device"]
    detector = VoskWakeDetector(settings["wake_model"], settings["wake_word"])
    capture = UtteranceCapture(detector, settings)
    frames: queue.Queue = queue.Queue(maxsize=250)
    overflow = threading.Event()
    session = str(uuid.uuid4())
    deadline = time.monotonic() + seconds if wake_only else float("inf")
    peak_rms = 0.0
    wakes = 0
    last_text = ""

    def callback(data, count, timing, status):
        if status.input_overflow:
            overflow.set()
        try:
            frames.put_nowait(bytes(data))
        except queue.Full:
            overflow.set()

    print(f"等待唤醒：{settings['wake_word']}。可直接连说内容，Ctrl+C 退出。")
    if wake_only:
        print(f"本地唤醒测试，最长 {seconds:g} 秒，完成一条录音后结束。", flush=True)
    while time.monotonic() < deadline:
        control.emit("waiting")
        capture.reset()
        while not frames.empty():
            frames.get_nowait()
        overflow.clear()
        # Close capture during generation/playback to prevent self-wake. Barge-in is not enabled.
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES,
                               device=settings.get("input_device"), channels=1, dtype="int16",
                               callback=callback):
            last_frame = time.monotonic()
            while True:
                control.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    print(f"测试结束。唤醒次数 {wakes}，最高音量 RMS {peak_rms:.0f}。", flush=True)
                    return
                try:
                    frame = frames.get(timeout=min(0.2 if managed else 5, remaining))
                except queue.Empty as error:
                    if time.monotonic() >= deadline:
                        print(f"测试结束。唤醒次数 {wakes}，最高音量 RMS {peak_rms:.0f}。", flush=True)
                        return
                    if managed and time.monotonic() - last_frame < 5:
                        continue
                    raise ValueError("麦克风未返回音频，请检查设备连接与系统权限。") from error
                last_frame = time.monotonic()
                if overflow.is_set():
                    print("采音缓冲溢出，请重新唤醒。")
                    control.emit("error", "采音缓冲溢出，请重新唤醒。")
                    break
                was_waiting = capture.frames is None
                if wake_only:
                    samples = np.frombuffer(frame, dtype="<i2").astype(np.float32)
                    peak_rms = max(peak_rms, float(np.sqrt(np.mean(samples * samples))))
                utterance = capture.feed(frame)
                if wake_only and was_waiting:
                    recognized = detector.last_text
                    if recognized and recognized != last_text:
                        print(f"本地识别：{recognized}", flush=True)
                        last_text = recognized
                if was_waiting and capture.frames is not None:
                    wakes += 1
                    control.emit("awakened", settings["wake_word"])
                    print("已唤醒，正在收集本轮语音。", flush=True)
                if utterance is not None:
                    break
        if overflow.is_set():
            continue
        if wake_only:
            duration = len(utterance) / (SAMPLE_RATE * 2)
            print(f"唤醒测试通过，已收集 {duration:.2f} 秒音频，包含唤醒前缓冲。", flush=True)
            print(f"最高音量 RMS {peak_rms:.0f}。可以关闭窗口，下一步验证模型接口。", flush=True)
            return
        print("正在处理…")
        control.emit("processing")
        try:
            if ready_backend_mode(config) == "s2s":
                from .realtime_device import send_pcm
                send_pcm(config, utterance, control=control if managed else None)
            else:
                result = send_turn(config, encode_wav(utterance), session, str(uuid.uuid4()))
                play_result(result, settings.get("output_device"), control if managed else None)
        except BackendUnavailable as error:
            print(str(error), flush=True)
            control.emit("backend_unavailable", str(error))
        except (ValueError, sd.PortAudioError) as error:
            print(str(error))
            control.emit("error", str(error))
        print("等待唤醒。")
