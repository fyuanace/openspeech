import asyncio
import json
import re
import secrets
import time
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from .config import dialogue_mode
from .device_tools import SET_VOLUME, valid_percent
from .providers import ProviderError
from .realtime import INPUT_RATE, OUTPUT_RATE, MAX_TURN_BYTES, build_realtime


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", value)


def control(message):
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect()
    text = message.get("text")
    if not text or len(text) > 4096:
        raise ProviderError("protocol", "需要有效的会话控制事件。")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ProviderError("protocol", "控制事件必须为 JSON 对象。")
    return data


def register_realtime(app, get_config, factory=build_realtime):
    active = set()
    app.state.realtime_sessions = active

    @app.websocket("/v1/realtime")
    async def realtime(socket: WebSocket):
        config = get_config()
        expected = "Bearer " + config["server"]["token"]
        if not secrets.compare_digest(socket.headers.get("authorization", "").encode(), expected.encode()):
            await socket.close(code=1008)
            return
        await socket.accept()
        peer = None
        key = None
        try:
            if dialogue_mode(config) != "s2s":
                raise ProviderError("mode", "当前为 cascade 模式，请使用 /v1/turns。")
            start = control(await asyncio.wait_for(socket.receive(), 10))
            if start.get("type") != "session.start" or not all(valid_id(start.get(k)) for k in ("device_id", "session_id")):
                raise ProviderError("protocol", "需要 session.start 和有效的设备、会话 ID。")
            candidate = (start["device_id"], start["session_id"])
            if candidate in active or len(active) >= 2:
                raise ProviderError("session", "实时会话已连接或连接数已满。")
            active.add(candidate)
            key = candidate
            offers_volume = isinstance(start.get("tools"), list) and SET_VOLUME in start["tools"]
            tool_request = None

            async def set_volume(percent):
                nonlocal tool_request
                request_id = secrets.token_hex(16)
                result = asyncio.get_running_loop().create_future()
                tool_request = request_id, result
                try:
                    await socket.send_json({"type": "tool.call", "session_id": key[1], "turn_id": turn,
                                            "request_id": request_id, "name": SET_VOLUME,
                                            "arguments": {"percent": percent}})
                    return await asyncio.wait_for(result, 8)
                except TimeoutError:
                    return {"ok": False, "error": "设备未在时限内确认音量。"}
                finally:
                    tool_request = None

            peer = factory(config)
            if offers_volume:
                await peer.open(set_volume=set_volume)
            else:
                await peer.open()
            await socket.send_json({"type": "session.ready", "version": 1, "mode": "s2s",
                                    "session_id": key[1], "input_rate": INPUT_RATE,
                                    "output_rate": OUTPUT_RATE, "channels": 1, "sample_width": 2,
                                    "barge_in": False})
            seen = set()
            pending = None
            session_deadline = time.monotonic() + 1800
            while True:
                remaining = session_deadline - time.monotonic()
                if remaining <= 0 or len(seen) >= 128:
                    raise ProviderError("session", "实时会话已到期，请重启设备端建立新会话。")
                message = pending if pending is not None else await asyncio.wait_for(socket.receive(), min(180, remaining))
                pending = None
                event = control(message)
                if event.get("type") == "session.close":
                    return
                turn = event.get("turn_id")
                if event.get("type") != "turn.start" or not valid_id(turn) or turn in seen:
                    raise ProviderError("protocol", "需要新的 turn.start，实时轮次不支持重放。")
                seen.add(turn)
                size = 0
                async with asyncio.timeout(45):
                    while True:
                        message = await socket.receive()
                        if message["type"] == "websocket.disconnect":
                            raise WebSocketDisconnect()
                        pcm = message.get("bytes")
                        if pcm is not None:
                            size += len(pcm)
                            if not pcm or len(pcm) % 2 or len(pcm) > 6400 or size > MAX_TURN_BYTES:
                                raise ProviderError("protocol", "音频需为 PCM16，单片不超过 200ms，单轮不超过 35 秒。")
                            await peer.append(pcm)
                            continue
                        event = control(message)
                        if event.get("type") == "session.close":
                            return
                        if event.get("type") != "input.commit" or event.get("turn_id") != turn or size < 3200:
                            raise ProviderError("protocol", "提交需匹配当前轮次，并包含至少 100ms 音频。")
                        break

                started_at, started = time.time(), time.monotonic()
                user_text = ""
                reply = []
                outcome, stage = "interrupted", ""

                async def pump():
                    nonlocal user_text, outcome
                    async for response in peer.respond():
                        kind = response["type"]
                        if kind == "input.transcript":
                            user_text = response["text"]
                            continue
                        if kind == "text.delta":
                            reply.append(response["text"])
                        await socket.send_json({**response, "session_id": key[1], "turn_id": turn})
                        if kind == "turn.done":
                            outcome = "completed"
                        if kind == "session.ended":
                            outcome = "ended"
                            return True
                    return False

                async def watch_device():
                    while True:
                        message = await socket.receive()
                        if not message.get("text"):
                            return message
                        event = control(message)
                        if event.get("type") != "tool.result":
                            return message
                        if (tool_request is None or tool_request[1].done()
                                or (event.get("session_id"), event.get("turn_id"), event.get("request_id"))
                                != (key[1], turn, tool_request[0])):
                            continue
                        percent = event.get("percent")
                        if event.get("ok") is True and valid_percent(percent):
                            tool_request[1].set_result({"ok": True, "percent": percent})
                        else:
                            tool_request[1].set_result({"ok": False, "error": "设备无法确认音量调整结果。"})

                # Watch disconnects during generation; never keep a paid upstream running after departure.
                output = asyncio.create_task(pump())
                incoming = asyncio.create_task(watch_device())
                app.state.active_turns += 1
                try:
                    done, _ = await asyncio.wait({output, incoming}, return_when=asyncio.FIRST_COMPLETED)
                    if output in done:
                        if await output:
                            return
                        if incoming.done():
                            pending = incoming.result()
                    else:
                        message = control(incoming.result())
                        if message.get("type") == "session.close":
                            return
                        raise ProviderError("protocol", "请等待本轮完成后再发送下一轮。")
                except WebSocketDisconnect:
                    raise
                except Exception as error:
                    outcome = "failed"
                    stage = error.stage if isinstance(error, ProviderError) else "timeout" if isinstance(error, TimeoutError) else "s2s"
                    raise
                finally:
                    for task in (output, incoming):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(output, incoming, return_exceptions=True)
                    app.state.active_turns -= 1
                    if app.state.journal:
                        await asyncio.to_thread(
                            app.state.journal.record, started_at=started_at, mode="s2s",
                            model=config["s2s"]["model"], identifiers=(*key, turn),
                            text=user_text, reply="".join(reply), outcome=outcome, stage=stage,
                            duration_ms=round((time.monotonic() - started) * 1000),
                        )
        except WebSocketDisconnect:
            pass
        except Exception as error:
            if isinstance(error, ProviderError):
                stage, message = error.stage, str(error)
            elif isinstance(error, (TimeoutError, asyncio.TimeoutError)):
                stage, message = "timeout", "实时会话超时，请重新连接。"
            else:
                stage, message = "s2s", "实时连接失败，请检查服务地址、网络和百炼配置。"
            with suppress(Exception):
                await socket.send_json({"type": "error", "stage": stage, "error": message})
        finally:
            if peer:
                with suppress(Exception):
                    await peer.close()
            if key:
                active.discard(key)
            with suppress(Exception):
                await socket.close()
