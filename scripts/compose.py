"""Step 5 · FFmpeg 合成：按时间线拼接镜头 + 烧字幕 + 统一到 config 目标画幅。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (is_done, job_dir, load_config, mark_done, read_json, resolve_tool, run)


def find_subtitle(jd: Path) -> Path | None:
    cands = list(jd.glob("ref/packaging/*.srt")) + list(jd.glob("ref/*.srt")) + list(jd.glob("*.vtt"))
    if not cands:
        return None
    # 优先 packaging 里的成片字幕
    for p in cands:
        if "packaging" in str(p):
            return p
    return cands[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--burn-subs", action="store_true", help="烧录字幕")
    ap.add_argument("--music", help="配乐/混音音轨路径（可选）。传入整段音轨时不再循环，直接铺到成片")
    ap.add_argument("--timeline-trim", action="store_true",
                    help="按 shots.json 的 end-start 精确裁切每段，忠实还原原片时间线（生成时长为整数时会短于/长于原片）")
    args = ap.parse_args()

    cfg = load_config()
    jd = job_dir(args.job, cfg)
    clips_dir = jd / "clips"
    plan = read_json(jd / "shots.json", {})
    shots = plan.get("shots", [])
    final = jd / "final.mp4"

    if is_done(args.job, "compose", cfg) and final.exists():
        print(f"[skip] compose 已完成：{final}")
        return 0

    ff = resolve_tool("ffmpeg")
    if not ff:
        print("错误：未找到 ffmpeg，先跑 preflight.py 或 install.py", file=sys.stderr)
        return 2

    clips = []
    for s in shots:
        p = clips_dir / f"{s['id']}.mp4"
        if p.exists() and p.stat().st_size > 0:
            clips.append(p)
    if not clips:
        print("错误：clips/ 下没有可用片段，先跑 generate.py", file=sys.stderr)
        return 2

    W = cfg.get("target_width", 1080)
    H = cfg.get("target_height", 1920)

    # 用 concat demuxer，先归一化每段到目标画幅（缩放 + 居中补边），避免尺寸不一致拼接失败
    # clip -> 对应 shot 的精确时长（按 end-start）
    dur_by_clip = {}
    if args.timeline_trim:
        for s in shots:
            if s.get("start") is not None and s.get("end") is not None:
                dur_by_clip[s["id"]] = round(float(s["end"]) - float(s["start"]), 3)

    norm_dir = jd / "_norm"
    norm_dir.mkdir(exist_ok=True)
    norm_files = []
    for i, clip in enumerate(clips):
        out = norm_dir / f"n_{i:03d}.mp4"
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1")
        cmd_n = [ff, "-y", "-i", str(clip)]
        seg_dur = dur_by_clip.get(clip.stem) if args.timeline_trim else None
        if seg_dur and seg_dur > 0:
            cmd_n += ["-t", str(seg_dur)]  # 裁到原片该镜时长
        cmd_n += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                  "-pix_fmt", "yuv420p", "-an", str(out)]
        run(cmd_n)
        norm_files.append(out)

    list_file = jd / "_concat.txt"
    list_file.write_text("".join(f"file '{p.as_posix()}'\n" for p in norm_files), encoding="utf-8")

    # 拼字幕滤镜
    vf_final = []
    if args.burn_subs:
        sub = find_subtitle(jd)
        if sub:
            # Windows 路径冒号需转义，用相对路径 + 从 jd 运行规避
            rel = sub.name if sub.parent == jd else sub.relative_to(jd).as_posix()
            sub_param = rel.replace(":", "\\:").replace("'", "\\'")
            vf_final.append(f"subtitles='{sub_param}'")
        else:
            print("[warn] 未找到字幕文件，跳过烧字幕")

    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if args.music and Path(args.music).exists():
        # 传入的是整段成品音轨（BGM+旁白）：直接铺、-shortest 对齐，不再 aloop 循环
        cmd += ["-i", str(args.music), "-map", "0:v", "-map", "1:a", "-shortest"]
    if vf_final:
        cmd += ["-vf", ",".join(vf_final)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(final)]
    run(cmd, cwd=jd)

    # 下载即删：清理原片与中间归一化文件
    if not cfg.get("keep_source", False):
        src = jd / "source.mp4"
        if src.exists():
            src.unlink()
            print(f"[cleanup] 已删除原片 {src.name}")
    for p in norm_files:
        try:
            p.unlink()
        except OSError:
            pass

    mark_done(args.job, "compose", f"final={final.name}", cfg)
    print(f"\n[done] 成片：{final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
