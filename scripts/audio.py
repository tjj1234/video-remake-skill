"""Step 4b · 自生成音轨：Vidu audio1.0 生成 BGM + MiniMax speech TTS 生成旁白，混音成一条 30s 音轨。

用法:
  python audio.py --job <job> --narration "英文旁白文本" --target-sec 30
  python audio.py --job <job> --narration-file <txt> --target-sec 30

产物（写入 job 目录 audio/）:
  bgm.wav          30 秒背景音乐（3 段 × 10s 拼接）
  narration.mp3    英文旁白
  mixed.m4a        混音成品（BGM 压低 + 旁白突出），供 compose.py --music 使用
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dump_json, job_dir, load_config, resolve_pt_key, resolve_tool, run

BGM_URL = "https://api.powertokens.ai/vidu/ent/v2/text2audio"
BGM_QUERY = "https://api.powertokens.ai/vidu/ent/v2/tasks/{tid}/creations"
TTS_URL = "https://api.powertokens.ai/v1/audio/speech"

# 三段风格递进，拼成有层次的 30s BGM
BGM_PROMPTS = [
    "Cinematic ambient background music, soft ethereal pads and gentle strings, "
    "sense of wonder and space, calm and dreamy, no vocals, clean mix",
    "Cinematic ambient music with building tension, subtle pulsing synth, "
    "warm strings rising, space odyssey atmosphere, no vocals, clean mix",
    "Epic cinematic ambient music, soaring strings, hopeful crescendo, "
    "triumphant and emotional, space exploration mood, no vocals, clean mix",
]


def _hdr(key: str) -> dict:
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def _post(url: str, body: dict, key: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=_hdr(key), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url: str, key: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers=_hdr(key))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url: str, dst: Path, key: str | None = None, timeout: int = 120):
    headers = _hdr(key) if key else {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dst, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dst


def gen_bgm(key: str, outdir: Path, seg_sec: int = 10) -> list[Path]:
    """生成 N 段 BGM（各 seg_sec 秒），返回下载好的 wav 列表。"""
    segments: list[Path] = []
    for i, prompt in enumerate(BGM_PROMPTS):
        print(f"[bgm] 提交第 {i+1}/{len(BGM_PROMPTS)} 段 ({seg_sec}s): {prompt[:50]}...")
        sub = _post(BGM_URL, {"model": "audio1.0", "prompt": prompt,
                              "duration": seg_sec, "seed": 0}, key)
        tid = sub.get("task_id") or (sub.get("data") or {}).get("task_id")
        if not tid:
            raise RuntimeError(f"BGM 提交未返回 task_id: {sub}")
        qurl = BGM_QUERY.format(tid=tid)
        audio_url = None
        for attempt in range(60):  # 最多 ~3 分钟
            time.sleep(3)
            try:
                st = _get(qurl, key)
            except Exception as e:
                print(f"[bgm] 轮询 {attempt} 异常：{e}")
                continue
            state = st.get("state")
            if state == "success":
                creations = st.get("creations") or []
                if creations and creations[0].get("url"):
                    audio_url = creations[0]["url"]
                    break
            elif state == "failed":
                raise RuntimeError(f"BGM 任务失败: {st.get('err_msg')}")
            if attempt % 5 == 0:
                print(f"[bgm]   state={state} progress={st.get('progress')}")
        if not audio_url:
            raise RuntimeError(f"BGM 任务超时未出音频: {tid}")
        seg = outdir / f"bgm_seg_{i+1}.wav"
        _download(audio_url, seg)
        print(f"[bgm]   下载完成 {seg.name} ({seg.stat().st_size//1024}KB)")
        segments.append(seg)
    return segments


def concat_audio(ff: str, segments: list[Path], dst: Path):
    """用 ffmpeg concat 拼接多段音频为单条（统一转 aac）。"""
    lst = dst.parent / "_bgm_concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments), encoding="utf-8")
    # 段源是 pcm_s16le；必须仍以 PCM 输出，切勿 aac→wav（容器采样率标识不匹配会毁掉后续解码）
    run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c:a", "pcm_s16le", "-ar", "48000", str(dst)])


def gen_narration(key: str, text: str, dst: Path, voice: str = "English_Graceful_Lady") -> Path:
    print(f"[tts] 生成旁白 ({len(text)} 字符, voice={voice})...")
    resp = _post(TTS_URL, {
        "model": "speech-2.8-turbo",
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "metadata": {"output_format": "url"},
    }, key)
    url = (resp.get("data") or {}).get("audio")
    if not url:
        raise RuntimeError(f"TTS 未返回音频 URL: {resp}")
    _download(url, dst)
    info = resp.get("extra_info") or {}
    print(f"[tts] 完成 {dst.name} ({dst.stat().st_size//1024}KB, "
          f"时长约 {info.get('audio_length',0)/1000:.1f}s)")
    return dst


def mix(ff: str, bgm: Path, narration: Path, dst: Path, target_sec: int,
        bgm_vol: float = 0.28, voice_vol: float = 1.25):
    """BGM 压低 + 旁白突出，裁到 target_sec，输出供 compose 用的音轨。"""
    fc = (f"[0:a]volume={bgm_vol}[b];[1:a]volume={voice_vol}[v];"
          f"[b][v]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[out]")
    run([ff, "-y", "-i", str(bgm), "-i", str(narration),
         "-filter_complex", fc, "-map", "[out]", "-t", str(target_sec),
         "-c:a", "aac", "-b:a", "192k", str(dst)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--narration", help="英文旁白文本")
    ap.add_argument("--narration-file", help="英文旁白文本文件（UTF-8）")
    ap.add_argument("--target-sec", type=int, default=30)
    ap.add_argument("--voice", default="English_Graceful_Lady")
    ap.add_argument("--bgm-only", action="store_true", help="只生成 BGM（试听）")
    ap.add_argument("--reuse-bgm", action="store_true", help="复用已生成的 bgm.wav，不重新生成")
    ap.add_argument("--rebuild-mix", action="store_true",
                    help="复用已生成的 BGM 段与旁白，只重做拼接+混音（修复容器问题/调音量，不花钱）")
    ap.add_argument("--bgm-vol", type=float, default=0.28, help="BGM 音量（默认 0.28）")
    ap.add_argument("--voice-vol", type=float, default=1.25, help="旁白音量（默认 1.25）")
    args = ap.parse_args()

    cfg = load_config()
    jd = job_dir(args.job, cfg)
    adir = jd / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    key = resolve_pt_key()
    if not key:
        print("错误：找不到 PT key", file=sys.stderr)
        return 2
    ff = resolve_tool("ffmpeg")
    if not ff:
        print("错误：找不到 ffmpeg", file=sys.stderr)
        return 2

    bgm = adir / "bgm.wav"
    if args.rebuild_mix:
        segs = sorted(adir.glob("bgm_seg_*.wav"))
        if not segs:
            print("错误：--rebuild-mix 需要已有 bgm_seg_*.wav", file=sys.stderr)
            return 2
        print(f"[bgm] 复用 {len(segs)} 段已有 BGM，重做拼接")
        concat_audio(ff, segs, bgm)
        print(f"[bgm] BGM 成品 {bgm.name} ({bgm.stat().st_size//1024}KB)")
    elif args.reuse_bgm and bgm.exists():
        print(f"[bgm] 复用已有 {bgm.name}")
    else:
        segs = gen_bgm(key, adir)
        concat_audio(ff, segs, bgm)
        print(f"[bgm] BGM 成品 {bgm.name} ({bgm.stat().st_size//1024}KB)")

    if args.bgm_only:
        dump_json(adir / "audio_meta.json", {"bgm": str(bgm), "target_sec": args.target_sec})
        print(f"[done] BGM-only: {bgm}")
        return 0

    nar = adir / "narration.mp3"
    text = args.narration
    if args.narration_file:
        text = Path(args.narration_file).read_text(encoding="utf-8").strip()
    if args.rebuild_mix and nar.exists():
        print(f"[tts] 复用已有 {nar.name}")
    else:
        if not text:
            print("错误：需要 --narration 或 --narration-file", file=sys.stderr)
            return 2
        nar = gen_narration(key, text, nar, args.voice)
    mixed = adir / "mixed.m4a"
    mix(ff, bgm, nar, mixed, args.target_sec, args.bgm_vol, args.voice_vol)
    dump_json(adir / "audio_meta.json", {
        "bgm": str(bgm), "narration": str(nar), "mixed": str(mixed),
        "narration_text": text, "voice": args.voice, "target_sec": args.target_sec,
    })
    print(f"\n[done] 混音音轨：{mixed} ({mixed.stat().st_size//1024}KB)")
    print(f"       compose 时用：--music \"{mixed}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
