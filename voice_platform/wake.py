import json
from pathlib import Path


class VoskWakeDetector:
    """Local streaming keyword detection; remote STT handles the actual utterance."""

    def __init__(self, model_path: str, wake_word: str):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        if not Path(model_path).is_dir():
            raise ValueError("唤醒模型缺失，请运行 scripts/download_model.py。")
        self.word = "".join(wake_word.split()).lower()
        if not self.word:
            raise ValueError("唤醒词不能为空。")
        SetLogLevel(-1)
        self.model = Model(model_path)
        self.recognizer = KaldiRecognizer(self.model, 16000)

    def process(self, pcm: bytes) -> bool:
        if self.recognizer.AcceptWaveform(pcm):
            text = json.loads(self.recognizer.Result()).get("text", "")
        else:
            text = json.loads(self.recognizer.PartialResult()).get("partial", "")
        return self.word in "".join(text.split()).lower()

    def reset(self) -> None:
        self.recognizer.Reset()
