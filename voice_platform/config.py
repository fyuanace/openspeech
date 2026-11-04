import json
import os
import secrets
from pathlib import Path


def load_config(path: str) -> dict:
    source = Path(path).resolve()
    config = json.loads(source.read_text(encoding="utf-8-sig"))
    for section, field, variable in [
        ("stt", "api_key", "DOUBAO_API_KEY"),
        ("stt", "app_id", "DOUBAO_APP_ID"),
        ("stt", "access_token", "DOUBAO_ACCESS_TOKEN"),
        ("llm", "api_key", "DEEPSEEK_API_KEY"),
        ("server", "token", "VOICE_TOKEN"),
    ]:
        if os.environ.get(variable):
            config[section][field] = os.environ[variable]
    model = Path(config["device"]["wake_model"])
    config["device"]["wake_model"] = str((source.parent / model).resolve())
    if not config["server"].get("token"):
        raise ValueError("平台连接凭据缺失，请运行 init-config 生成配置。")
    return config


def initialize_config(path: str, example: str) -> None:
    config = json.loads(Path(example).read_text(encoding="utf-8-sig"))
    config["server"]["token"] = secrets.token_urlsafe(32)
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(config, output, ensure_ascii=False, indent=2)
