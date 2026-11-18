"""Step 3 · 配方转参数：rea-video breakdown.json -> shots.json（生成参数表）。

rea-video 的 breakdown.json 结构会随版本演进，这里做「尽力解析 + 绝不吞数据」：
- 多候选字段路径探测镜头数组；
- 每镜提取 start/end/duration、prompt、render_type、字幕；
- 解析不到的原样塞进 _raw，并在 _source 指回原文件，供人工补齐（见 prompt-mapping.md）。
生成结果必须经人工确认才进入 generate.py。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dump_json, job_dir, load_config, mark_done, read_json


def pick(d: dict, *keys, default=None):
    """按顺序取第一个存在的键。"""
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return default


def find_shots(data) -> list[dict]:
    """从多种可能的结构里找镜头数组。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("shots", "segments", "timeline", "breakdown", "clips", "units"):
        v = data.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    for v in data.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            if any(k in v[0] for k in ("start", "index", "id", "shot", "prompt", "render")):
                return v
    return []


def seconds(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            if ":" in v:
                parts = [float(x) for x in v.split(":")]
                acc = 0.0
                for p in parts:
                    acc = acc * 60 + p
                return acc
    return None


def _shot_label(shot: dict) -> str:
    """rea_video.breakdown.v1: 制作方式在 shot.base.label / shot.replication.label。"""
    for path in (("base", "label"), ("replication", "label"), ("material", "origin")):
        node = shot
        ok = True
        for k in path:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                ok = False
                break
        if ok and isinstance(node, str):
            return node
    # 旧格式扁平字段回退
    return str(pick(shot, "render_type", "render", "type", "kind", default="gen"))


def _overlay_texts(shot: dict) -> list[str]:
    """从 shot.text.tracks[].texts 抽字幕/花字。"""
    texts: list[str] = []
    t = shot.get("text") if isinstance(shot, dict) else None
    if isinstance(t, dict):
        for track in t.get("tracks", []) or []:
            if isinstance(track, dict):
                for s in track.get("texts", []) or []:
                    if s and str(s).strip():
                        texts.append(str(s).strip())
    return texts


def _unit_draft_for_shot(data: dict, shot_idx_1based: int) -> str:
    """
    rea-video 的提示词在 production_plan.units[].draft（一个单元覆盖多镜）。
    若某 unit 的 shots 含本镜，取它的 draft；否则回退到兜底 draft。
    """
    pp = data.get("production_plan", {}) if isinstance(data, dict) else {}
    units = pp.get("units", []) or []
    fallback = ""
    for u in units:
        if not isinstance(u, dict):
            continue
        d = str(u.get("draft", "") or "").strip()
        if not fallback and d:
            fallback = d
        member = u.get("shots", []) or []
        # shots 可能是 [1,2] 这类 1-based 序号
        if shot_idx_1based in [int(x) for x in member if str(x).isdigit()]:
            # 一个 unit 覆盖多镜时，draft 里是「镜头1：…」「镜头2：…」 concatenated，
            # 必须按本镜序号切出对应段落，否则每镜都用整段剧本 → 生成内容重复。
            return _slice_draft_for_shot(d, member, shot_idx_1based)
    return fallback


_SHOT_MARK = re.compile(r"镜头\s*(\d+)\s*[：:]")


def _slice_draft_for_shot(draft: str, member_shots: list, shot_idx_1based: int) -> str:
    """从 unit.draft 中切出本镜段落 + 保留开头总述。找不到分段标记则原样返回。"""
    marks = list(_SHOT_MARK.finditer(draft))
    if not marks:
        return draft
    header = draft[:marks[0].start()].strip()
    # 每个镜号对应的正文（到下一个标记前）
    blocks: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(draft)
        blocks[int(m.group(1))] = draft[m.start():end].strip()
    # unit 的 member_shots 是全局镜号列表，draft 里的「镜头N」N 是 1-based 局部序号
    try:
        local_n = [int(x) for x in member_shots].index(shot_idx_1based) + 1
    except ValueError:
        local_n = shot_idx_1based
    seg = blocks.get(local_n) or blocks.get(shot_idx_1based)
    if not seg:
        return draft
    return (header + "\n" + seg).strip() if header else seg


def shot_to_params(shot: dict, idx: int, cfg: dict, data: dict | None = None) -> dict:
    start = seconds(pick(shot, "start", "start_time", "startTime", "t_start"))
    end = seconds(pick(shot, "end", "end_time", "endTime", "t_end"))
    dur = seconds(pick(shot, "duration", "dur", "length"))
    if dur is None and start is not None and end is not None:
        dur = end - start
    if dur is None:
        dur = cfg.get("default_duration", 5)

    # 提示词：优先 breakdown 的 production_plan.units[].draft；再回退旧扁平字段
    prompt = ""
    if data:
        prompt = _unit_draft_for_shot(data, idx + 1)
    if not prompt:
        prompt = pick(shot, "prompt", "description", "desc", "caption", "visual", "what", default="")
        if isinstance(prompt, dict):
            prompt = pick(prompt, "en", "zh", "text", "content", default="")
    prompt = str(prompt).strip()

    label = _shot_label(shot)
    render = label.lower()
    # 分流：code/模板/录屏 -> 代码复刻；ai/gen -> 走 PT；实拍/影视 -> 重拍
    route = "skip"
    if any(k in render for k in ("code_render", "code", "template", "screen", "motion_graph", "mg")):
        route = "code_remake"
    elif any(k in render for k in ("ai_video", "gen", "ai", "synthetic", "t2v", "i2v", "avatar", "cg_3d")):
        route = "pt_generate"
    elif any(k in render for k in ("footage", "live", "real", "film", "still_motion", "stop_motion")):
        route = "reshoot"

    ref = pick(shot, "ref", "first_frame", "firstFrame", "reference", "image", default=None)
    if isinstance(ref, dict):
        ref = pick(ref, "path", "url", "asset", default=None)

    overlays = _overlay_texts(shot)

    return {
        "id": f"shot_{idx + 1:03d}",
        "index": idx + 1,
        "start": start,
        "end": end,
        "duration": round(float(dur), 2),
        "route": route,
        "render_type": render,
        "prompt": prompt,
        "overlay_texts": overlays,
        "model": cfg.get("default_model", "wan3.0-video"),
        "resolution": cfg.get("default_resolution", "720p"),
        "ratio": cfg.get("vertical_ratio", "16:9"),
        "ref": str(ref) if ref else None,
        "_raw": shot,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--max-shots", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config()
    jd = job_dir(args.job, cfg)
    bd_path = jd / "ref" / "breakdown.json"
    if not bd_path.exists():
        print(f"错误：找不到 {bd_path}，先跑 decompose.py", file=sys.stderr)
        return 2

    data = read_json(bd_path, {})
    raw_shots = find_shots(data)
    max_shots = args.max_shots or cfg.get("max_shots", 12)
    shots = [shot_to_params(s, i, cfg, data) for i, s in enumerate(raw_shots[:max_shots])]

    result = {
        "_source": str(bd_path),
        "_notice": "生成参数由 breakdown.json 自动映射，render/字幕/运镜需人工复核；未解析字段见每镜 _raw。",
        "job": args.job,
        "total_found": len(raw_shots),
        "shots": shots,
    }
    out = jd / "shots.json"
    dump_json(out, result)
    mark_done(args.job, "recipe_to_prompt", f"shots={len(shots)}", cfg)

    print(f"[done] 生成参数表：{out}")
    print(f"       拆解共 {len(raw_shots)} 镜，纳入 {len(shots)} 镜（上限 {max_shots}）")
    print("\n=== 镜头表摘要（人工确认）===")
    print(f"{'id':<10} {'route':<14} {'dur':>5}  prompt")
    for s in shots:
        p = s["prompt"].replace("\n", " ")[:52]
        print(f"{s['id']:<10} {s['route']:<14} {s['duration']:>5}  {p}")
    n_pt = sum(1 for s in shots if s["route"] == "pt_generate")
    print(f"\n其中走 PT 生成 {n_pt} 镜；code/实拍类 {len(shots) - n_pt} 镜需另行处理（见 prompt-mapping.md）。")
    print("确认无误后跑 generate.py；要改直接编辑 shots.json。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
