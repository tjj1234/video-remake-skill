"""Step 4 · 批量生成：逐镜头调 powertokens-kit 的 pt_video.py generate。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (dump_json, is_done, job_dir, load_config, mark_done,
                     read_json, resolve_pt_video, resolve_python, run)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--only", help="只生成指定镜头，如 shot_003 或 1,3,5")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令不执行")
    args = ap.parse_args()

    cfg = load_config()
    jd = job_dir(args.job, cfg)
    shots_path = jd / "shots.json"
    if not shots_path.exists():
        print(f"错误：找不到 {shots_path}，先跑 recipe_to_prompt.py", file=sys.stderr)
        return 2

    pt = resolve_pt_video(cfg)
    if not pt:
        print("错误：找不到 pt_video.py，检查 config.json 或 preflight.py", file=sys.stderr)
        return 2

    py = resolve_python(cfg)
    clips_dir = jd / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    plan = read_json(shots_path, {})
    shots = [s for s in plan.get("shots", []) if s.get("route") == "pt_generate"]

    if args.only:
        wanted = set(args.only.split(","))
        wanted = {w if w.startswith("shot_") else f"shot_{int(w):03d}" for w in wanted
                  if w.isdigit() or w.startswith("shot_")}
        shots = [s for s in shots if s["id"] in wanted]

    if not shots:
        print("没有需要 PT 生成的镜头（可能都是 code/实拍类，或 --only 过滤掉了）")
        return 0

    results = []
    for s in shots:
        out_file = clips_dir / f"{s['id']}.mp4"
        if out_file.exists() and out_file.stat().st_size > 0:
            print(f"[skip] {s['id']} 已存在：{out_file.name}")
            results.append({"id": s["id"], "status": "skipped", "path": str(out_file)})
            continue
        if not s.get("prompt"):
            print(f"[skip] {s['id']} 无 prompt，跳过（检查 shots.json）")
            results.append({"id": s["id"], "status": "no_prompt"})
            continue

        cmd = [py, str(pt), "generate",
               "-p", s["prompt"],
               "-o", str(out_file),
               "-d", str(int(s["duration"])),
               "-r", s.get("resolution", cfg.get("default_resolution", "720p")),
               "-m", s.get("model", cfg.get("default_model", "wan3.0-video"))]
        # 画幅：prompt 内已写"横屏 16:9"，这里再把 shots.json 的 ratio 显式透传给 pt CLI
        ratio = s.get("ratio") or cfg.get("vertical_ratio", "16:9")
        if ratio:
            cmd += ["--aspect-ratio", ratio]
        if s.get("ref"):
            cmd += ["--ref", str(s["ref"])]
        if s.get("negative_prompt"):
            cmd += ["--negative-prompt", s["negative_prompt"]]
        if s.get("seed") is not None:
            cmd += ["--seed", str(s["seed"])]

        print(f"\n--- {s['id']} ({s['duration']}s · {s.get('model')}) ---")
        if args.dry_run:
            print("[dry-run] " + " ".join(cmd))
            results.append({"id": s["id"], "status": "dry_run"})
            continue
        try:
            run(cmd)
            ok = out_file.exists() and out_file.stat().st_size > 0
            results.append({"id": s["id"], "status": "ok" if ok else "no_output",
                            "path": str(out_file) if ok else None})
        except RuntimeError as e:
            print(f"[warn] {s['id']} 生成失败：{e}", file=sys.stderr)
            results.append({"id": s["id"], "status": "failed", "error": str(e)[:500]})
            time.sleep(2)

    dump_json(jd / "_generate_results.json", {"results": results})
    ok_n = sum(1 for r in results if r["status"] in ("ok", "skipped"))
    mark_done(args.job, "generate", f"ok={ok_n}/{len(shots)}", cfg)
    print(f"\n[done] 生成完成 {ok_n}/{len(shots)}，成功/跳过镜头已落盘到 {clips_dir}")
    failed = [r["id"] for r in results if r["status"] == "failed"]
    if failed:
        print(f"[warn] 失败镜头：{failed}；修好后用 --only 单独重跑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
