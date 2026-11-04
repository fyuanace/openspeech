import argparse
import json
import sys
import uuid
from pathlib import Path

from .config import initialize_config, load_config


def main():
    parser = argparse.ArgumentParser(description="独立语音平台")
    parser.add_argument("command", choices=["init-config", "serve", "listen", "devices", "doctor", "send-wav"])
    parser.add_argument("--config", default="config.local.json")
    parser.add_argument("--file", help="send-wav 的输入 WAV")
    args = parser.parse_args()
    try:
        if args.command == "init-config":
            initialize_config(args.config, "config.example.json")
            print("配置已生成，平台连接凭据已自动创建。")
            return
        if args.command == "devices":
            import sounddevice as sd
            print(sd.query_devices())
            return
        config = load_config(args.config)
        if args.command == "serve":
            import uvicorn
            from .server import create_app
            uvicorn.run(create_app(config), host=config["server"]["host"],
                        port=int(config["server"]["port"]), access_log=False)
        elif args.command == "listen":
            from .device import listen
            listen(config)
        elif args.command == "send-wav":
            from .device import play_result, send_turn
            from .audio import decode_wav
            if not args.file:
                raise ValueError("请使用 --file 指定 WAV 文件。")
            wav = Path(args.file).read_bytes()
            decode_wav(wav, input_audio=True)
            result = send_turn(config, wav, str(uuid.uuid4()), str(uuid.uuid4()))
            play_result(result, config["device"].get("output_device"))
        else:
            import sounddevice as sd
            stt = config["stt"]
            print(json.dumps({"wake_model_exists": Path(config["device"]["wake_model"]).is_dir(),
                              "stt_configured": bool(stt.get("api_key") or (stt.get("app_id") and stt.get("access_token"))),
                              "llm_configured": bool(config["llm"].get("api_key")),
                              "default_audio_devices": list(sd.default.device)}, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print("已停止。")
    except Exception as error:
        print(f"启动或执行失败：{error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
