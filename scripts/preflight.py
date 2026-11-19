"""Step 0 · 依赖预检：Python / ffmpeg / yt-dlp / rea-video / pt_video.py / PT Key。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (SKILL_DIR, dump_json, job_dir, load_config,
                     resolve_pt_video, resolve_python, resolve_tool, run, which)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="preflight")
    args = ap.parse_args()

    cfg = load_config()
    py = resolve_python(cfg)
    report = {"ok": True, "checks": {}, "install_hints": []}

    # Python
    cp = run([py, "--version"], capture=True, check=False)
    ver = (cp.stdout or cp.stderr or "").strip()
    report["checks"]["python"] = {"ok": cp.returncode == 0, "detail": ver}
    if cp.returncode != 0:
        report["ok"] = False
        report["install_hints"].append("安装 Python 3.10+：https://www.python.org/downloads/")

    # ffmpeg（优先 skill 私有隔离目录）
    ff = resolve_tool("ffmpeg")
    report["checks"]["ffmpeg"] = {"ok": bool(ff), "detail": ff or "未找到"}
    if not ff:
        report["ok"] = False
        report["install_hints"].append("ffmpeg：python scripts/install.py --plan 查看，授权后 --apply ffmpeg 自动装到私有目录")

    # yt-dlp（优先 skill 私有隔离目录）
    ytd = resolve_tool("yt-dlp")
    if not ytd:
        cp2 = run([py, "-m", "yt_dlp", "--version"], capture=True, check=False)
        ytd = f"{py} -m yt_dlp" if cp2.returncode == 0 else None
    report["checks"]["yt_dlp"] = {"ok": bool(ytd), "detail": ytd or "未找到"}
    if not ytd:
        report["ok"] = False
        report["install_hints"].append("yt-dlp：python scripts/install.py --plan 查看，授权后 --apply yt_dlp 自动装到私有目录")

    # rea-video（优先 skill 私有隔离目录）
    rea = resolve_tool("rea-video")
    report["checks"]["rea_video"] = {"ok": bool(rea), "detail": rea or "未找到"}
    if not rea:
        report["ok"] = False
        report["install_hints"].append(
            "rea-video：python scripts/install.py --plan 查看，授权后 --apply rea_video 自动 clone 到 tools/ 并装进私有 venv"
        )

    # pt_video.py
    pt = resolve_pt_video(cfg)
    report["checks"]["pt_video"] = {"ok": bool(pt), "detail": str(pt) if pt else "未找到"}
    if not pt:
        report["ok"] = False
        report["install_hints"].append(
            "pt_video.py：把 powertokens-video skill 放到 ~/.workbuddy/skills/，"
            "或在 config.json 的 pt_video_script 填其绝对路径"
        )

    # PT Key（调 pt_video.py check）
    # S3 零打印：check 的原始输出含 key_masked 尾号，绝不透传/落盘；只保留布尔结论。
    if pt:
        cp3 = run([py, str(pt), "check"], capture=True, check=False)
        key_ok = cp3.returncode == 0
        detail = "已配置（校验通过）" if key_ok else "未配置或校验失败（详见 install_hints）"
        report["checks"]["pt_key"] = {"ok": key_ok, "detail": detail}
        if not key_ok:
            report["ok"] = False
            report["install_hints"].append(
                "PT Key：打开 https://powertokens.ai/zh-Hans/dashboard 创建 sk-pt-xxxx，"
                f"然后 `{py} {pt} config --set-key \"sk-pt-xxxx\"`"
            )

    out = job_dir(args.job, cfg) / "_preflight.json"
    dump_json(out, report)

    print("\n=== 预检结果 ===")
    for k, v in report["checks"].items():
        mark = "OK " if v["ok"] else "NO "
        print(f"  [{mark}] {k}: {v['detail']}")
    if report["install_hints"]:
        print("\n=== 需要补齐 ===")
        for i, h in enumerate(report["install_hints"], 1):
            print(f"  {i}. {h}")
        print("\n  自动安装（推荐）：先 `python scripts/install.py --plan` 看清单，"
              "用户逐类确认后 `--authorize <组件>` + `--apply <组件>`，全部装到 skill 私有目录。")
    print(f"\n报告：{out}")
    print("结论：" + ("全部就绪，可以继续 Step 1" if report["ok"] else "有缺失，先补齐再继续"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
