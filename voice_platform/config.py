import json
import os
import secrets
from pathlib import Path


CREDENTIAL_ENV = (
    ("stt", "api_key", "DOUBAO_API_KEY"),
    ("stt", "app_id", "DOUBAO_APP_ID"),
    ("stt", "access_token", "DOUBAO_ACCESS_TOKEN"),
    ("llm", "api_key", "DEEPSEEK_API_KEY"),
    ("server", "token", "VOICE_TOKEN"),
    ("s2s", "api_key", "DASHSCOPE_API_KEY"),
    ("s2s", "workspace_id", "DASHSCOPE_WORKSPACE_ID"),
)


def resolve_config(config: dict, source: Path) -> dict:
    for section, field, variable in CREDENTIAL_ENV:
        if os.environ.get(variable):
            config.setdefault(section, {})[field] = os.environ[variable]
    model = Path(config["device"]["wake_model"])
    config["device"]["wake_model"] = str((source.parent / model).resolve())
    if not config["server"].get("token"):
        raise ValueError("平台连接凭据缺失，请运行 init-config 生成配置。")
    return config


def load_config(path: str) -> dict:
    source = Path(path).resolve()
    return resolve_config(json.loads(source.read_text(encoding="utf-8-sig")), source)


def dialogue_mode(config: dict) -> str:
    mode = config.get("dialogue", {}).get("mode", "cascade")
    if mode not in ("s2s", "cascade"):
        raise ValueError("dialogue.mode 必须为 s2s 或 cascade。")
    return mode


def doubao_uses_api_key(settings: dict) -> bool:
    return settings.get("auth_mode", "key" if settings.get("api_key") else "legacy") == "key"


def missing_credentials(config: dict) -> list[str]:
    if dialogue_mode(config) == "s2s":
        settings = config.get("s2s", {})
        missing = []
        if not settings.get("api_key"):
            missing.append("dashscope_api_key")
        if not settings.get("url") and not settings.get("workspace_id"):
            missing.append("dashscope_workspace_id")
        return missing
    stt = config["stt"]
    missing = []
    stt_ready = stt.get("api_key") if doubao_uses_api_key(stt) else stt.get("app_id") and stt.get("access_token")
    if stt["provider"] == "doubao" and not stt_ready:
        missing.append("doubao_credentials")
    if not config["llm"].get("api_key"):
        missing.append("llm_credentials")
    return missing


def initialize_config(path: str, example: str) -> None:
    config = json.loads(Path(example).read_text(encoding="utf-8-sig"))
    config["server"]["token"] = secrets.token_urlsafe(32)
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(config, output, ensure_ascii=False, indent=2)
