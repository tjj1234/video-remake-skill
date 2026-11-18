"""一键编排：串起 preflight -> fetch -> decompose -> recipe -> [人工卡点] -> generate -> compose。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import SKILL_DIR, load_config, resolve_python

STEPS = ["preflight", "fetch", "decompose", "recipe_to_prompt", "generate", "compose"]


def call(py: str, script: str, extra: list[str]) -> int:
    cmd = [py, str(SKILL_DIR / "scripts" / f"{script}.py")] + extra
    print("\n" + "=" * 60)
    print("[pipeline] " + " ".join(cmd))
    print("=" * 60)
    return subprocess.run(cmd).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--url", help="视频链接（fetch 阶段需要）")
    ap.add_argument("--local", help="本地视频路径")
    ap.add_argument("--vlm", action="store_true", help="拆解开视觉模型")
    ap.add_argument("--auto", action="store_true", help="跳过人工卡点，自动往下跑")
    ap.add_argument("--burn-subs", action="store_true", help="合成时烧字幕")
    ap.add_argument("--resume-from", choices=STEPS, help="从指定步骤继续")
    ap.add_argument("--only", help="透传给 generate 的 --only")
    args = ap.parse_args()

    cfg = load_config()
    py = resolve_python(cfg)
    start = STEPS.index(args.resume_from) if args.resume_from else 0

    for i in range(start, len(STEPS)):
        step = STEPS[i]
        extra = ["--job", args.job]

        if step == "fetch":
            if args.url:
                extra += ["--url", args.url]
            elif args.local:
                extra += ["--local", args.local]
            else:
                print("错误：fetch 阶段需要 --url 或 --local", file=sys.stderr)
                return 2
        if step == "decompose" and args.vlm:
            extra.append("--vlm")
        if step == "generate" and args.only:
            extra += ["--only", args.only]
        if step == "compose" and args.burn_subs:
            extra.append("--burn-subs")

        rc = call(py, step, extra)
        if rc != 0:
            print(f"[pipeline] 步骤 {step} 失败(exit {rc})，停止。修好后用 --resume-from {step} 继续。", file=sys.stderr)
            return rc

        # 人工卡点：recipe 之后停下，等用户确认 shots.json
        if step == "recipe_to_prompt" and not args.auto:
            jd = SKILL_DIR / cfg.get("workdir", "out") / args.job
            print("\n" + "#" * 60)
            print("# 人工卡点：请查看并确认镜头表")
            print(f"#   {jd / 'shots.json'}")
            print("# 要改直接编辑该文件，然后：")
            print(f"#   python scripts/pipeline.py --job {args.job} --resume-from generate")
            print("# 确认无误、要全自动则加 --auto 重跑。")
            print("#" * 60)
            return 0

    print("\n[pipeline] 全部完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
