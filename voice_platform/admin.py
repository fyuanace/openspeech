"""Local management UI and atomic updates to the server configuration."""

import asyncio
import copy
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .config import CREDENTIAL_ENV, dialogue_mode, doubao_uses_api_key, missing_credentials, resolve_config
from .providers import ProviderError
from .realtime import qwen_url

UI = Path(__file__).with_name("admin_ui")
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
}
FIELDS = {
    "s2s": {"model", "region", "workspace_id", "url", "voice", "instructions", "api_key"},
    "stt": {"provider", "url", "resource_id", "auth_mode", "api_key", "app_id", "access_token"},
    "llm": {"url", "model", "api_key"},
    "tts": {"voice"},
}
PRIVATE = {"api_key", "app_id", "access_token"}
SECTIONS = {"s2s": {"s2s"}, "cascade": {"stt", "llm", "tts"}}


def local_only(request: Request) -> None:
    host = request.headers.get("host", "").lower()
    if (request.client is None or request.client.host not in {"127.0.0.1", "::1"}
            or not re.fullmatch(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?", host)):
        raise HTTPException(403, "请在服务端电脑上打开本机管理页。")
    if request.method != "GET" and request.headers.get("origin", "").lower() != f"{request.url.scheme}://{host}":
        raise HTTPException(403, "请从管理网页提交配置。")


def public_config(config):
    missing = missing_credentials(config)
    return {
        "mode": dialogue_mode(config),
        "missing": missing,
        "settings": {
            section: {key: config.get(section, {}).get(key, "") for key in sorted(fields - PRIVATE)}
            for section, fields in FIELDS.items()
        },
        "saved_credentials": {
            f"{section}.{key}": bool(config.get(section, {}).get(key))
            for section, fields in FIELDS.items() for key in fields & PRIVATE
        },
        "environment_fields": [f"{section}.{field}" for section, field, variable in CREDENTIAL_ENV
                               if section in FIELDS and os.getenv(variable)],
    }


def endpoint_origin(url, schemes):
    parts = urlsplit(url)
    if (parts.scheme not in schemes or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.fragment):
        raise ValueError("服务地址格式无效，请使用完整地址。")
    return parts.scheme, parts.hostname, parts.port


def apply_changes(config, payload):
    mode = payload.get("mode")
    settings = payload.get("settings")
    if mode not in ("s2s", "cascade") or not isinstance(settings, dict) or set(settings) - SECTIONS[mode]:
        raise ValueError("请选择有效的对话模式和对应配置。")
    config.setdefault("dialogue", {})["mode"] = mode
    for section, fields in settings.items():
        if not isinstance(fields, dict) or set(fields) - FIELDS[section]:
            raise ValueError("配置含有不支持的字段。")
        target = config.setdefault(section, {})
        for name, value in fields.items():
            if not isinstance(value, str) or len(value) > 4000:
                raise ValueError("配置值格式无效或过长。")
            value = value.strip()
            if value or name not in PRIVATE:
                target[name] = value

    if mode == "s2s":
        s2s = config.setdefault("s2s", {})
        s2s.setdefault("provider", "qwen-realtime")
        if not s2s.get("model") or not s2s.get("voice"):
            raise ValueError("请填写 S2S 模型和音色。")
        if s2s.get("url"):
            endpoint_origin(s2s["url"], {"ws", "wss"})
        if s2s.get("url") or s2s.get("workspace_id"):
            qwen_url(s2s)
    else:
        stt, llm, tts = (config.get(key, {}) for key in ("stt", "llm", "tts"))
        if stt.get("provider") not in {"vosk", "doubao"}:
            raise ValueError("请选择语音识别提供方。")
        if stt["provider"] == "doubao":
            if stt.get("auth_mode", "key") not in {"key", "legacy"}:
                raise ValueError("请选择有效的豆包鉴权方式。")
            endpoint_origin(stt.get("url", ""), {"http", "https"})
            if not stt.get("resource_id"):
                raise ValueError("请填写豆包资源 ID。")
        endpoint_origin(llm.get("url", ""), {"http", "https"})
        if not llm.get("model") or not tts.get("voice"):
            raise ValueError("请填写对话模型和 Windows 音色。")


def write_config(path: Path, config: dict):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".voice-config-", delete=False) as output:
            temporary = Path(output.name)
            json.dump(config, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def credential_origin(settings, section):
    if section == "s2s":
        return endpoint_origin(qwen_url(settings), {"ws", "wss"})
    return endpoint_origin(settings.get("url", ""), {"http", "https"})


def check_endpoint_changes(previous, candidate, changes):
    for section in {"s2s", "stt", "llm"} & changes.keys():
        before, after = previous.get(section, {}), candidate.get(section, {})
        if section == "stt" and after.get("provider") != "doubao":
            continue
        keys = ("app_id", "access_token") if section == "stt" and not doubao_uses_api_key(after) else ("api_key",)
        try:
            old_origin = credential_origin(before, section)
            new_origin = credential_origin(after, section)
        except (ValueError, ProviderError, KeyError):
            continue
        if old_origin != new_origin:
            for key in keys:
                if after.get(key) and (not changes[section].get(key, "").strip() or any(
                    (group, field) == (section, key) and os.getenv(variable)
                    for group, field, variable in CREDENTIAL_ENV
                )):
                    raise ValueError("服务地址已更换，请为新地址重新填写凭据。环境变量管理的凭据需同步更换。")


def register_admin(app, config_path, prepare, realtime_factory):
    path = Path(config_path).resolve()
    saving = asyncio.Lock()
    checking = asyncio.Lock()

    @app.get("/admin", include_in_schema=False)
    async def page(request: Request):
        local_only(request)
        return FileResponse(UI / "index.html", headers=HEADERS)

    @app.get("/admin/assets/{asset}", include_in_schema=False)
    async def asset(request: Request, asset: str):
        local_only(request)
        if asset not in {"admin.css", "admin.js"}:
            raise HTTPException(404)
        return FileResponse(UI / asset, headers=HEADERS)

    @app.get("/admin/api/config", include_in_schema=False)
    async def get_config(request: Request):
        local_only(request)
        return JSONResponse(public_config(app.state.runtime[0]), headers=HEADERS)

    def save(payload):
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        previous = resolve_config(copy.deepcopy(raw), path)
        apply_changes(raw, payload)
        candidate = resolve_config(copy.deepcopy(raw), path)
        check_endpoint_changes(previous, candidate, payload["settings"])
        runtime = prepare(candidate)
        write_config(path, raw)
        return runtime

    @app.put("/admin/api/config", include_in_schema=False)
    async def set_config(request: Request):
        local_only(request)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 32768:
                raise HTTPException(413, "配置内容过长。")
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except ValueError as error:
            raise HTTPException(400, "配置格式无效。") from error
        if not isinstance(payload, dict):
            raise HTTPException(400, "配置格式无效。")
        async with saving:
            try:
                app.state.runtime = await asyncio.to_thread(save, payload)
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
            except OSError as error:
                raise HTTPException(500, "保存失败，请检查本地配置的写入权限。") from error
            except Exception as error:
                raise HTTPException(400, "配置未能加载，请检查模型和服务地址。") from error
        return JSONResponse(public_config(app.state.runtime[0]), headers=HEADERS)

    @app.post("/admin/api/check", include_in_schema=False)
    async def check(request: Request):
        local_only(request)
        config = app.state.runtime[0]
        if dialogue_mode(config) != "s2s" or missing_credentials(config):
            raise HTTPException(400, "请先保存完整的 S2S 配置。")
        if checking.locked():
            raise HTTPException(409, "正在测试连接，请稍候。")
        async with checking:
            peer = None
            try:
                async with asyncio.timeout(15):
                    peer = realtime_factory(config)
                    await peer.open()
                    await peer.close()
                    peer = None
            except Exception as error:
                raise HTTPException(502, "连接失败，请检查地址、API Key 和模型权限。") from error
            finally:
                if peer:
                    try:
                        await asyncio.wait_for(peer.close(), 3)
                    except Exception:
                        pass
        return JSONResponse({"connected": True}, headers=HEADERS)
