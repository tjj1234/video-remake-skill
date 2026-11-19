"""Step 4 · 批量生成：只调 media_backend 的 generate()/download() 两个接口。

S1/S2：不直接调 PT REST、不直接拼 kit CLI；提交保护 + 三态下载在这里落地。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (dump_json, is_done, job_dir, load_config, mark_done,
                     read_json)
from media_backend import (DOWNLOAD_FAILED, OK, STATUS_UNKNOWN, TASK_FAILED,
                           get_backend)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--only", help="只生成指定镜头，如 shot_003 或 1,3,5")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不执行（不提交、不花钱）")
    ap.add_argument("--backend", default="kit", help="媒体后端：kit（当前）；可扩展其它实现")
    args = ap.parse_args()

    cfg = load_config()
    jd = job_dir(args.job, cfg)
    shots_path = jd / "shots.json"
    if not shots_path.exists():
        print(f"错误：找不到 {shots_path}，先跑 recipe_to_prompt.py", file=sys.stderr)
        return 2

    backend = get_backend(args.backend)
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

    if args.dry_run:
        print("[dry-run] 计划生成以下镜头（不提交、不花钱）：")
        for s in shots:
            print(f"  - {s['id']}  {s.get('model', cfg.get('default_model'))}  "
                  f"{s['duration']}s  ratio={s.get('ratio') or cfg.get('vertical_ratio')}")
        print("[dry-run] 实际提交请去掉 --dry-run")
        return 0

    results = []
    for s in shots:
        sid = s["id"]
        out_file = clips_dir / f"{sid}.mp4"
        print(f"\n--- {sid} ({s['duration']}s · {s.get('model', cfg.get('default_model'))}) ---")

        # 已有成片：跳过（下载兜底也靠这个判断）
        if out_file.exists() and out_file.stat().st_size > 0:
            print(f"[skip] {sid} 已存在：{out_file.name}")
            results.append({"id": sid, "status": "skipped", "path": str(out_file)})
            continue
        if not s.get("prompt"):
            print(f"[skip] {sid} 无 prompt，跳过（检查 shots.json）")
            results.append({"id": sid, "status": "no_prompt"})
            continue

        # 1) 提交（内部写 manifest=submitting→submitted，崩溃在中间不重发）
        gr = backend.generate(args.job, s, cfg)
        if gr.status == STATUS_UNKNOWN:
            print(f"[warn] {sid} 提交状态未知：{gr.detail}（保留现场，不重发，稍后人工确认）", file=sys.stderr)
            results.append({"id": sid, "status": "submit_unknown", "detail": gr.detail})
            continue
        if gr.status == TASK_FAILED:
            print(f"[warn] {sid} 提交明确失败：{gr.detail}", file=sys.stderr)
            results.append({"id": sid, "status": "submit_failed", "detail": gr.detail})
            continue

        # 2) 下载（按 task_id 查状态→取回；三态）
        dr = backend.download(args.job, sid, out_file, cfg)
        if dr.status == OK:
            results.append({"id": sid, "status": "ok", "task_id": gr.task_id, "path": dr.path})
        elif dr.status == DOWNLOAD_FAILED:
            # 任务成功、只是没下下来：记录，允许稍后单独重下，绝不重生成
            print(f"[warn] {sid} 下载失败但任务成功：{dr.detail}（只重下，不重生成）", file=sys.stderr)
            results.append({"id": sid, "status": "download_failed", "task_id": gr.task_id, "detail": dr.detail})
        elif dr.status == TASK_FAILED:
            # PT 明确失败：唯一允许重新生成的分支
            print(f"[warn] {sid} 任务明确失败：{dr.detail}（可重生成）", file=sys.stderr)
            results.append({"id": sid, "status": "task_failed", "task_id": gr.task_id, "detail": dr.detail})
        else:  # STATUS_UNKNOWN
            print(f"[warn] {sid} 状态未知：{dr.detail}（保留 task_id，稍后再查，不重生成）", file=sys.stderr)
            results.append({"id": sid, "status": "status_unknown", "task_id": gr.task_id, "detail": dr.detail})
        time.sleep(1)

    dump_json(jd / "_generate_results.json", {"results": results})
    ok_n = sum(1 for r in results if r["status"] in ("ok", "skipped"))
    mark_done(args.job, "generate", f"ok={ok_n}/{len(shots)}", cfg)
    print(f"\n[done] 生成完成 {ok_n}/{len(shots)}，成功/跳过镜头已落盘到 {clips_dir}")
    bad = [r["id"] for r in results if r["status"] not in ("ok", "skipped")]
    if bad:
        print(f"[warn] 需关注镜头：{bad}；详见 out/<job>/manifest.json 与 _generate_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
