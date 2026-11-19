"""真机探测：用 rea-video 同款 data URI 格式，测试 PT 网关哪些 vision 模型真的能吃图。
只读探测，不写任何 skill 配置；消耗极少量 chat token。"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.powertokens.ai"
IMG = (Path.home() / ".workbuddy" / "skills" / "video-remake" / "out" /
       "yt_GnsiYACM5ng" / "ref" / "shots" / "shot_001.jpg")

# 候选：catalog 中 capabilities.multimodal.vision=true 的 chat 模型，覆盖不同渠道
CANDIDATES = [
    "qwen3.8-flash",            # ali-qwen3（官方 vision=true，探测曾假阴性）
    "deepseek-v4-pro",          # deepseek（用户默认模型）
    "seed-2-0-pro-260328",      # BytePlus-seed（vision + video_in）
    "glm-5.3-flash",            # zhipu
    "MiniMax-M3",               # minimax
]

PROMPT = "这张图片里主要有什么内容？请用一句中文回答。"


def load_key() -> str:
    """S3：只认调用方传入的 POWERTOKENS_API_KEY，不读任何配置文件。"""
    return os.environ.get("POWERTOKENS_API_KEY", "").strip()


def data_uri(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def probe(model: str, key: str, uri: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": uri}},
            {"type": "text", "text": PROMPT},
        ]}],
        "max_tokens": 128,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        BASE + "/v1/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "User-Agent": "video-remake-vlm-probe/1.0"},
    )
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return {"model": model, "ok": True, "sec": round(time.time() - t, 1),
                "answer": str(content)[:200]}
    except urllib.error.HTTPError as e:
        why = ""
        try:
            why = e.read()[:200].decode(errors="ignore").strip()
        except Exception:
            pass
        return {"model": model, "ok": False, "http": e.code,
                "err": why[:200], "sec": round(time.time() - t, 1)}
    except Exception as e:
        return {"model": model, "ok": False, "err": repr(e)[:200],
                "sec": round(time.time() - t, 1)}


def main() -> int:
    key = load_key()
    if not key:
        print("NO KEY"); return 2
    if not IMG.exists():
        print("NO IMAGE", IMG); return 2
    uri = data_uri(IMG)
    print(f"image={IMG.name} ({IMG.stat().st_size//1024}KB)  [key sourced from env, not printed]")
    print("=" * 60)
    results = []
    for m in CANDIDATES:
        r = probe(m, key, uri)
        results.append(r)
        if r["ok"]:
            print(f"[OK ] {m:26s} {r['sec']}s -> {r['answer'][:120]}")
        else:
            print(f"[FAIL] {m:26s} {r.get('http','')} {r['err'][:120]}")
    print("=" * 60)
    ok = [r["model"] for r in results if r["ok"]]
    print("WORKING:", ", ".join(ok) if ok else "(none)")
    out = Path(__file__).parent.parent / "out" / "vlm_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"results": results, "working": ok},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("saved:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
