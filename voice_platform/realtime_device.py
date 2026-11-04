"""Native device client for the platform's S2S protocol, with no vendor credentials."""
import base64
import json
import queue
import threading
import time
import uuid
from contextlib import closing
from urllib.parse import urlsplit, urlunsplit

import sounddevice as sd
from websockets.sync.client import connect

from .audio import FRAME_SAMPLES, SAMPLE_RATE, UtteranceCapture
from .listener_control import ListenerStopped
from .realtime import MAX_OUTPUT_BYTES
from .wake import VoskWakeDetector


class RealtimeClient:
    def __init__(self, config, control=None):
        self.config = config
        self.session = str(uuid.uuid4())
        self.turn = None
        self.ws = None
        self.connection = None
        self.control = control

    def receive(self, timeout=130):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待实时服务超时。")
            if self.control:
                self.control.check()
            try:
                event = json.loads(self.ws.recv(timeout=min(remaining, 0.2) if self.control else remaining))
                break
            except TimeoutError:
                if self.control is None:
                    raise
        if event.get("type") == "error":
            raise ValueError(event.get("error", "实时语音处理失败。"))
        return event

    def __enter__(self):
        parts = urlsplit(self.config["device"]["server_url"])
        if parts.scheme not in ("http", "https"):
            raise ValueError("设备服务地址必须使用 http 或 https。")
        url = urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc,
                         parts.path.rstrip("/") + "/v1/realtime", "", ""))
        try:
            self.connection = connect(url, additional_headers={"Authorization": "Bearer " + self.config["server"]["token"]},
                              open_timeout=10, close_timeout=3, max_size=2**20, max_queue=16)
            self.ws = self.connection.__enter__()
            self.ws.send(json.dumps({"type": "session.start", "device_id": self.config["device"]["id"],
                                     "session_id": self.session}))
            ready = self.receive(timeout=70)
            if (ready.get("type"), ready.get("session_id"), ready.get("input_rate"), ready.get("output_rate"),
                    ready.get("channels"), ready.get("sample_width")) != ("session.ready", self.session, 16000, 24000, 1, 2):
                raise ValueError("实时服务返回了不支持的会话或音频规格。")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.ws is not None:
            self.connection.__exit__(*args)
            self.ws = None

    def begin(self):
        self.turn = str(uuid.uuid4())
        self.ws.send(json.dumps({"type": "turn.start", "turn_id": self.turn}))

    def append(self, pcm):
        for offset in range(0, len(pcm), 3200):
            if self.control:
                self.control.check()
            self.ws.send(pcm[offset:offset + 3200])

    def finish(self):
        self.ws.send(json.dumps({"type": "input.commit", "turn_id": self.turn}))
        deadline = time.monotonic() + 130
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("实时回复超时，请重新连接。")
            event = self.receive(timeout=remaining)
            if (event.get("session_id"), event.get("turn_id")) != (self.session, self.turn):
                raise ValueError("实时回复与当前轮次不匹配。")
            if event["type"] == "audio.delta":
                pcm = base64.b64decode(event["audio"], validate=True)
                size += len(pcm)
                if len(pcm) % 2 or size > MAX_OUTPUT_BYTES:
                    raise ValueError("回复音频无效或过长。")
                yield {"type": "audio", "pcm": pcm}
            elif event["type"] == "text.delta":
                yield {"type": "text", "text": event["text"]}
            elif event["type"] == "turn.done":
                if not size:
                    raise ValueError("实时服务未返回语音。")
                return
            else:
                raise ValueError("实时服务返回了未知事件。")


def captured_chunks(config, detector, control=None):
    """Yield pre-roll exactly once, then live PCM; capture continues during sends."""
    settings = config["device"]
    capture = UtteranceCapture(detector, settings)
    capture.reset()
    frames = queue.Queue(maxsize=250)
    overflow = threading.Event()

    def callback(data, count, timing, status):
        if status.input_overflow:
            overflow.set()
        try:
            frames.put_nowait(bytes(data))
        except queue.Full:
            overflow.set()

    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES,
                           device=settings.get("input_device"), channels=1, dtype="int16", callback=callback):
        last_frame = time.monotonic()
        while True:
            if control:
                control.check()
            try:
                frame = frames.get(timeout=0.2 if control else 5)
            except queue.Empty as error:
                if control and time.monotonic() - last_frame < 5:
                    continue
                raise ValueError("麦克风未返回音频，请检查设备。") from error
            last_frame = time.monotonic()
            if overflow.is_set():
                raise ValueError("采音缓冲溢出，本轮已取消，请重启设备端。")
            was_waiting = capture.frames is None
            completed = capture.feed(frame)
            if was_waiting:
                if capture.frames is not None or completed is not None:
                    yield completed if completed is not None else b"".join(capture.frames)
            else:
                yield frame
            if completed is not None:
                return


def play_stream(client, output_device, control=None):
    reply = ""
    playing = False
    with sd.RawOutputStream(samplerate=24000, channels=1, dtype="int16", device=output_device) as output:
        try:
            for event in client.finish():
                if event["type"] == "audio":
                    pcm = event["pcm"]
                    if control and not playing:
                        control.emit("playing")
                        playing = True
                    for offset in range(0, len(pcm), 4800):
                        if control:
                            control.check()
                        output.write(pcm[offset:offset + 4800])
                else:
                    print(event["text"], end="", flush=True)
                    if control:
                        reply = (reply + event["text"])[:4000]
            if control and reply:
                control.emit("reply", reply)
            print()
        except BaseException:
            output.abort()
            raise


def listen_realtime(config, control=None):
    settings = config["device"]
    detector = VoskWakeDetector(settings["wake_model"], settings["wake_word"])
    try:
        with RealtimeClient(config, control) as client:
            print(f"S2S 已连接，等待唤醒：{settings['wake_word']}。可直接连说内容，Ctrl+C 退出。", flush=True)
            while True:
                if control:
                    control.emit("waiting")
                started = False
                with closing(captured_chunks(config, detector, control)) as chunks:
                    for pcm in chunks:
                        if not started:
                            if control:
                                control.emit("awakened", settings["wake_word"])
                            client.begin()
                            started = True
                            print("已唤醒，正在传送语音。", flush=True)
                        client.append(pcm)
                print("正在回复…", flush=True)
                if control:
                    control.emit("processing")
                play_stream(client, settings.get("output_device"), control)
                print("等待唤醒。", flush=True)
    except (ValueError, sd.PortAudioError, ListenerStopped):
        raise
    except Exception as error:
        raise ValueError("实时连接已断开，请检查网络和服务状态后重启设备端。") from error


def send_pcm(config, pcm):
    try:
        with RealtimeClient(config) as client:
            client.begin()
            client.append(pcm)
            play_stream(client, config["device"].get("output_device"))
    except (ValueError, sd.PortAudioError):
        raise
    except Exception as error:
        raise ValueError("实时连接失败，请检查网络和服务状态。") from error
