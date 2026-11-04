"""Download the official small Chinese Vosk model into the independent project."""
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "vosk-model-small-cn-0.22"
URL = f"https://alphacephei.com/vosk/models/{NAME}.zip"


def main():
    destination = ROOT / "models" / NAME
    if destination.is_dir():
        print("模型目录已存在。")
        return
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        folder = Path(temporary)
        archive = folder / "model.zip"
        print("正在下载中文唤醒模型…")
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with zipfile.ZipFile(archive) as model:
            for member in model.infolist():
                if not (folder / member.filename).resolve().is_relative_to(folder.resolve()):
                    raise ValueError("模型压缩包路径无效。")
            model.extractall(folder)
        (folder / NAME).rename(destination)
    print("模型已就绪。")


if __name__ == "__main__":
    main()
