"""Step 1 · 下载源视频：只调 media_backend.download_source()（D1）。

S3：cookie 只经环境变量 REMAKE_COOKIE_PATH 传入，不落盘、不打印、不进 manifest。
D1：按 job 命名、统一 mp4、启动时清理残留、三态返回。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (SKILL_DIR, dump_json, is_done, job_dir, load_config,
                     load_manifest, mark_done, resolve_tool, save_manifest)
from media_backend import OK, STATUS_UNKNOWN, get_backend, _yt_cmd


def _cleanup_residue(jd: Path, dest: Path) -> None:
    """D1：启动时清理上次崩在下载中途留下的半成品（同名非 mp4 临时文件 / .part）。"""
    patterns = [dest.stem + ".part", dest.stem + ".ytdl", dest.stem + ".temp"]
    for pat in patterns:
        for p in jd.glob(pat + "*"):
            try:
                p.unlink()
                print(f"[cleanup] 残留 {p.name}")
            except OSError:
                pass


def _download_subs(url: str, jd: Path, cfg: dict) -> None:
    """字幕单独尽力下载，失败只警告不影响主流程。"""
    dest_stem = "source"
    out_tmpl = str(jd / (dest_stem + ".%(ext)s"))
    ff = resolve_tool("ffmpeg")
    cmd = _yt_cmd() + [
        "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", "zh-Hans,zh-CN,zh,en", "--sub-format", "vtt",
    ]
    if ff:
        cmd += ["--ffmpeg-location", ff]
    from _common import resolve_cookie_path
    cookie = resolve_cookie_path()
    if cookie:
        cmd += ["--cookies", cookie]
    cmd += ["-o", out_tmpl, url]
    try:
        from _common import run
        run(cmd, check=False, clean_env=True)
    except Exception as e:
        print(f"[warn] 字幕下载失败（不影响视频）：{e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--url", help="视频链接（YouTube/TikTok 等）")
    ap.add_argument("--local", help="本地视频路径（跳过下载）")
    ap.add_argument("--no-subs", action="store_true", help="不下载字幕")
    ap.add_argument("--backend", default="kit", help="媒体后端（源视频下载当前都用 kit/yt-dlp）")
    args = ap.parse_args()

    if not args.url and not args.local:
        print("错误：必须给 --url 或 --local 之一", file=sys.stderr)
        return 2

    cfg = load_config()
    jd = job_dir(args.job, cfg)
    jd.mkdir(parents=True, exist_ok=True)
    # D1：按 job_id 命名（job 名），不用视频标题；统一 mp4
    src = jd / "source.mp4"

    if is_done(args.job, "fetch", cfg) and src.exists():
        print(f"[skip] fetch 已完成：{src}")
        return 0

    _cleanup_residue(jd, src)

    backend = get_backend(args.backend)
    result = backend.download_source(args.job, args.url, args.local, src, cfg)

    if result.status == STATUS_UNKNOWN:
        print(f"[warn] 下载状态未知：{result.detail}", file=sys.stderr)
        print("       保留现场，不自动重下；稍后检查网络/cookie 后重跑本步。", file=sys.stderr)
        return 3
    if result.status != OK:
        print(f"错误：源视频下载失败：{result.detail}", file=sys.stderr)
        print("       可让用户手动下载后走 --local <path> 继续。", file=sys.stderr)
        return 2

    # 字幕尽力下载（本地文件不需要）
    if not args.no_subs and args.url:
        _download_subs(args.url, jd, cfg)

    meta = {
        "source": args.url or str(args.local),
        "mode": "local" if args.local else "ytdlp",
        "url": args.url,
        "path": result.path,
        # 不记录 cookie 路径 / Key；yt-dlp 版本已由 media_backend 写入 manifest
    }
    dump_json(jd / "meta.json", meta)
    # D1⑤：manifest 记录 yt-dlp 版本（便于复现/排错，extractor 行为随版本变）
    mf = load_manifest(args.job, cfg)
    from _common import _probe_ytdlp_version
    mf["ytdlp_version"] = _probe_ytdlp_version()
    mf["source"] = {"mode": meta["mode"], "path": result.path, "status": "downloaded"}
    save_manifest(args.job, mf, cfg)
    mark_done(args.job, "fetch", f"source={src.name}", cfg)
    print(f"\n[done] 视频就绪：{src}")
    for vtt in sorted(jd.glob("source*.vtt")):
        print(f"       字幕：{vtt.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
