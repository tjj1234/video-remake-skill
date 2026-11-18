"""Step 6 (optional) · 把英文字幕烧进成片。

用法：
  python burn_subs.py --job <job> --srt <字幕.srt> [--font Arial] [--size 52]
  python burn_subs.py --job <job> --auto              # 用旁白文案 + 静音点自动生成 SRT（英文）

输出：final_subbed.mp4（保留原 final.mp4）

Windows 坑规避：subprocess list 参数 + cwd=job 目录 + 相对路径，无盘符冒号/反斜杠转义。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

from _common import load_config, job_dir, resolve_tool


def find_ffmpeg(cfg: dict) -> Path:
    # 优先 skill 私有 ffmpeg
    cand = Path(cfg.get("ffmpeg_path", "")) if cfg.get("ffmpeg_path") else None
    if cand and cand.exists():
        return cand
    base = Path(__file__).resolve().parent.parent / "tools"
    for p in base.glob("ffmpeg-*/bin/ffmpeg.exe"):
        return p
    return Path("ffmpeg")  # 回落 PATH


def ts(sec: float) -> str:
    h, m = int(sec // 3600), int((sec % 3600) // 60)
    s, ms = int(sec % 60), int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def detect_pauses(ff: Path, audio: Path) -> list:
    """检测音频静音段，返回 (start,end) 列表，供自动字幕对齐。"""
    p = subprocess.run([str(ff), "-hide_banner", "-i", str(audio),
                        "-af", "silencedetect=noise=-32dB:d=0.18", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", p.stderr)]
    return list(zip(starts, ends))


def auto_srt(job: Path, ff: Path) -> Path:
    """从旁白文案逐句 + 静音点生成 SRT（句级对齐）。"""
    import json
    meta = job / "audio" / "audio_meta.json"
    text = ""
    if meta.exists():
        text = json.loads(meta.read_text(encoding="utf-8")).get("narration_text", "")
    if not text:
        raise RuntimeError("audio_meta.json 无 narration_text，无法自动字幕；请手动提供 --srt")
    # 按句末标点切分
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    nar = job / "audio" / "narration.mp3"
    pauses = detect_pauses(ff, nar) if nar.exists() else []
    # 取较长的停顿(>=0.4s)作为句边界，不足则按句数均分
    bounds = [(s + e) / 2 for s, e in pauses if e - s >= 0.4]
    dur = 27.7
    if nar.exists():
        q = subprocess.run([str(ff), "-hide_banner", "-i", str(nar)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", q.stderr)
        if m:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    if len(bounds) >= len(sentences) - 1:
        cuts = [0.0] + bounds[:len(sentences) - 1] + [dur]
    else:
        step = dur / len(sentences)
        cuts = [i * step for i in range(len(sentences) + 1)]
    blocks = []
    for i, sent in enumerate(sentences):
        st, en = cuts[i] + 0.05, cuts[i + 1] - 0.05
        blocks.append(f"{i+1}\n{ts(st)} --> {ts(en)}\n{sent}\n")
    srt = job / "sub_auto.srt"
    srt.write_text("\n".join(blocks), encoding="utf-8")
    print(f"[auto-srt] {len(sentences)} 句 -> {srt.name}")
    return srt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--srt", help="字幕文件路径（.srt）")
    ap.add_argument("--auto", action="store_true", help="从旁白文案+静音点自动生成英文字幕")
    ap.add_argument("--font", default="Arial")
    ap.add_argument("--size", type=int, default=52)
    ap.add_argument("--margin-v", type=int, default=70)
    args = ap.parse_args()

    cfg = load_config()
    jd = job_dir(args.job, cfg)
    src = jd / "final.mp4"
    if not src.exists():
        print(f"错误：{src} 不存在，先跑 compose.py", file=sys.stderr)
        return 2
    ff = find_ffmpeg(cfg)

    if args.auto:
        srt = auto_srt(jd, ff)
    elif args.srt:
        srt = Path(args.srt)
        if not srt.exists():
            print(f"错误：字幕文件不存在 {srt}", file=sys.stderr)
            return 2
    else:
        print("错误：需要 --srt 或 --auto", file=sys.stderr)
        return 2

    # 拷字幕到 job 目录用相对名（规避路径转义）
    local_srt = jd / "sub_burn.srt"
    local_srt.write_text(srt.read_text(encoding="utf-8"), encoding="utf-8")
    style = (f"FontName={args.font},FontSize={args.size},PrimaryColour=&H00FFFFFF,"
             f"OutlineColour=&H00000000,BorderStyle=1,Outline=2.5,Shadow=1,"
             f"Alignment=2,MarginV={args.margin_v}")
    fc = f"subtitles=sub_burn.srt:force_style='{style}'"
    out = jd / "final_subbed.mp4"
    cmd = [str(ff), "-y", "-i", src.name, "-vf", fc,
           "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out.name]
    p = subprocess.run(cmd, cwd=str(jd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print("ffmpeg 失败:", (p.stderr or "")[-2000:], file=sys.stderr)
        return 1
    print(f"[done] {out.name} ({round(out.stat().st_size/1024/1024,2)} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
