"""S4 音频接口化：把 audio.py 的自实现包成可替换后端。

当前 LocalAudioBackend（skill 自实现：audio1.0 BGM + speech TTS + ffmpeg 混音）。
未来可切 KitAudioBackend 等其它实现，主流程零改动。
Key 仍经环境变量 POWERTOKENS_API_KEY 注入（audio.py 内部用 resolve_pt_key）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio as _impl
from _common import resolve_tool


class AudioBackend:
    def generate_music(self, outdir: Path, seg_sec: int = 10) -> list[Path]:
        raise NotImplementedError

    def generate_speech(self, text: str, dst: Path, voice: str = "English_Graceful_Lady") -> Path:
        raise NotImplementedError

    def mix(self, bgm: Path, narration: Path, dst: Path, target_sec: int,
            bgm_vol: float = 0.28, voice_vol: float = 1.25) -> None:
        raise NotImplementedError


class LocalAudioBackend(AudioBackend):
    """当前实现：直接复用 audio.py 的既有函数，行为不变。"""

    def __init__(self, key: str, ffmpeg: str | None = None):
        self.key = key
        self.ffmpeg = ffmpeg or resolve_tool("ffmpeg")

    def generate_music(self, outdir: Path, seg_sec: int = 10) -> list[Path]:
        return _impl.gen_bgm(self.key, outdir, seg_sec)

    def generate_speech(self, text: str, dst: Path, voice: str = "English_Graceful_Lady") -> Path:
        return _impl.gen_narration(self.key, text, dst, voice)

    def mix(self, bgm: Path, narration: Path, dst: Path, target_sec: int,
            bgm_vol: float = 0.28, voice_vol: float = 1.25) -> None:
        if not self.ffmpeg:
            raise RuntimeError("找不到 ffmpeg")
        _impl.mix(self.ffmpeg, bgm, narration, dst, target_sec, bgm_vol, voice_vol)


def get_audio_backend(key: str, name: str = "local") -> AudioBackend:
    if name == "local":
        return LocalAudioBackend(key)
    raise ValueError(f"未知音频后端: {name}")
