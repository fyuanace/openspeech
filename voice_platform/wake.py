import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from .audio import SAMPLE_RATE


def is_conversation_exit(model, chunks: Iterable[bytes]) -> bool:
    from vosk import KaldiRecognizer

    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    segments = []
    for pcm in chunks:
        if recognizer.AcceptWaveform(pcm):
            segments.append(json.loads(recognizer.Result()).get("text", ""))
    segments.append(json.loads(recognizer.FinalResult()).get("text", ""))
    text = "".join("".join(segments).split())
    return text in {"退出", "请退出", "结束对话"}


def wake_tokens(model, phrase: str) -> list[str]:
    """Split a Chinese phrase into words present in this model's lexicon."""
    @lru_cache(maxsize=None)
    def split(start):
        if start == len(phrase):
            return []
        for end in range(len(phrase), start, -1):
            token = phrase[start:end]
            if model.vosk_model_find_word(token) < 0:
                continue
            rest = split(end)
            if rest is not None:
                return [token, *rest]
        return None

    tokens = split(0)
    if not tokens:
        raise ValueError("唤醒词包含当前模型不支持的词，请更换唤醒词或模型。")
    return tokens


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
        grammar = json.dumps([" ".join(wake_tokens(self.model, self.word)), "[unk]"], ensure_ascii=False)
        self.recognizer = KaldiRecognizer(self.model, 16000, grammar)
        # Use stable partial words: speculative text can append the wake suffix to a greeting.
        self.recognizer.SetPartialWords(True)
        self.last_text = ""

    def process(self, pcm: bytes) -> bool:
        if self.recognizer.AcceptWaveform(pcm):
            text = json.loads(self.recognizer.Result()).get("text", "")
        else:
            text = json.loads(self.recognizer.PartialResult()).get("partial", "")
        self.last_text = text
        return self.word in "".join(text.split()).lower()

    def reset(self) -> None:
        self.recognizer.Reset()
        self.last_text = ""
