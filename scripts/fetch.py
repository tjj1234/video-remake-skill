"""Step 1 · 下载视频：yt-dlp（URL）或直接接收本地文件。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (SKILL_DIR, dump_json, is_done, job_dir, load_config,
                     mark_done, resolve_tool, run, venv_python_path)


def yt_cmd(cfg: dict) -> list[str]:
    # 优先隔离 venv 的 python -m（模块装在 venv，exe 入口不一定生成）
    vp = venv_python_path()
    if vp:
        site = SKILL_DIR / ".venv" / "Lib" / "site-packages"
        if (site / "yt_dlp").exists() or (site / "yt_dlp" / "__init__.py").exists():
            return [str(vp), "-m", "yt_dlp"]
    ytd = resolve_tool("yt-dlp")
    if ytd:
        return [ytd]
    return ["python", "-m", "yt_dlp"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--url", help="视频链接（YouTube/TikTok 等）")
    ap.add_argument("--local", help="本地视频路径（跳过下载）")
    ap.add_argument("--no-subs", action="store_true", help="不下载字幕")
    args = ap.parse_args()

    if not args.url and not args.local:
        print("错误：必须给 --url 或 --local 之一", file=sys.stderr)
        return 2

    cfg = load_config()
    jd = job_dir(args.job, cfg)
    jd.mkdir(parents=True, exist_ok=True)
    src = jd / "source.mp4"

    if is_done(args.job, "fetch", cfg) and src.exists():
        print(f"[skip] fetch 已完成：{src}")
        return 0

    if args.local:
        local = Path(args.local).expanduser()
        if not local.exists():
            print(f"错误：本地文件不存在 {local}", file=sys.stderr)
            return 2
        import shutil
        shutil.copy2(local, src)
        print(f"[copy] {local} -> {src}")
        meta = {"source": str(local), "mode": "local", "url": None}
    else:
        out_tmpl = str(jd / "source.%(ext)s")
        # ffmpeg 装在 skill 私有目录、不在系统 PATH，需显式告诉 yt-dlp 去哪找，否则无法合并音视频流
        ff = resolve_tool("ffmpeg")
        # 主命令只下视频：字幕失败（429 / 反爬）不应拖垮视频下载
        cmd = yt_cmd(cfg) + [
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
            "--merge-output-format", "mp4",
            # X/Twitter 等站点的 CDN/metadata 接口常间歇性抖动，加大超时与重试
            "--socket-timeout", "60",
            "--retries", "10",
            "--fragment-retries", "10",
        ]
        if ff:
            cmd += ["--ffmpeg-location", ff]
        cmd += ["-o", out_tmpl, args.url]
        run(cmd)
        # 字幕单独尽力下载，失败只警告不影响主流程
        if not args.no_subs:
            sub_cmd = yt_cmd(cfg) + [
                "--skip-download", "--write-subs", "--write-auto-subs",
                "--sub-langs", "zh-Hans,zh-CN,zh,en", "--sub-format", "vtt",
                "-o", out_tmpl, args.url,
            ]
            try:
                run(sub_cmd, check=False)
            except Exception as e:
                print(f"[warn] 字幕下载失败（不影响视频）：{e}")
        # yt-dlp 可能产出 source.mp4 或其他扩展名，统一找出来（选最大的=视频流，别误选音频）
        if not src.exists():
            cands = [p for p in jd.glob("source.*") if p.suffix.lower() in
                     (".mp4", ".mkv", ".webm", ".mov")]
            if cands:
                max(cands, key=lambda p: p.stat().st_size).rename(src)
        if not src.exists():
            raise RuntimeError("下载后未找到 source 视频文件（可能被平台反爬拦截）")
        meta = {"source": args.url, "mode": "ytdlp", "url": args.url}

    dump_json(jd / "meta.json", meta)
    mark_done(args.job, "fetch", f"source={src.name}", cfg)
    print(f"\n[done] 视频就绪：{src}")
    for vtt in sorted(jd.glob("source*.vtt")):
        print(f"       字幕：{vtt.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
