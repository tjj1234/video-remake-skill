"""S1/D1 可替换媒体后端：把「生成」和「下载」收敛成两个接口。

设计目标：
- skill 其余部分只调 generate() / download()，不直接调 PT REST、不直接拼 kit CLI。
- 当前实现 KitMediaBackend（生成走 kit CLI，下载走 urllib + yt-dlp）；
  将来接其它媒体后端时，新增一个实现同样两个方法的 Backend 类即可，主流程零改动。
- 核心原则：不确定就一律不重发、不重生成（STATUS_UNKNOWN 保留现场）。

Key/cookie 只经环境变量注入（见 _common.KEY_ENV / COOKIE_ENV），本文件不落盘、不打印。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (COOKIE_ENV, KEY_ENV, dump_json, job_dir, load_config,
                     load_manifest, resolve_cookie_path, resolve_pt_key,
                     resolve_pt_video, resolve_python, resolve_tool, run,
                     save_manifest, venv_python_path, SKILL_DIR)

# ---- 三态结果（对外统一契约）----
OK = "OK"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"   # 任务成功、没下下来：只重下，禁止重生成
TASK_FAILED = "TASK_FAILED"           # 明确失败：唯一允许重新生成的分支
STATUS_UNKNOWN = "STATUS_UNKNOWN"     # 超时/断网/还在跑：保留现场，稍后再查


@dataclass
class Result:
    status: str
    path: str | None = None
    task_id: str | None = None
    detail: str = ""


class MediaBackend:
    """可替换基类接口。接其它媒体后端时实现同名方法即可。"""

    def generate(self, job: str, shot: dict, cfg: dict | None = None) -> Result:
        raise NotImplementedError

    def download(self, job: str, shot_id: str, dest: Path,
                 cfg: dict | None = None) -> Result:
        raise NotImplementedError


class KitMediaBackend(MediaBackend):
    """当前实现：生成走 powertokens-kit CLI，下载走 urllib；源视频走 yt-dlp。"""

    def __init__(self):
        self._pt_base = "https://api.powertokens.ai"
        self._poll_path = "/v1/videos/{id}"

    # ---------- 生成（提交 + 轮询，不下载）----------
    def generate(self, job: str, shot: dict, cfg: dict | None = None) -> Result:
        cfg = cfg or load_config()
        shot_id = shot["id"]
        manifest = load_manifest(job, cfg)
        rec = manifest["shots"].get(shot_id, {})

        # 已有 submitted/done 的镜头：不重复提交（提交保护，崩溃在中间也不重发）
        if rec.get("task_id") and rec.get("status") in ("submitted", "downloaded"):
            return Result(OK, task_id=rec["task_id"], detail="已有 task_id，跳过提交")

        pt = resolve_pt_video(cfg)
        if not pt:
            return Result(TASK_FAILED, detail="找不到 pt_video.py")
        py = resolve_python(cfg)
        model = shot.get("model", cfg.get("default_model", "wan3.0-video"))

        # 提交前先写 submitting（无 ID）——崩在中间下次能认出「提交状态未知」
        manifest["shots"][shot_id] = {
            "shot_id": shot_id, "model": model,
            "status": "submitting", "updated_at": _now(),
        }
        save_manifest(job, manifest, cfg)

        cmd = [py, str(pt), "generate",
               "-p", shot["prompt"],
               "-m", model,
               "-d", str(int(shot["duration"])),
               "-r", shot.get("resolution", cfg.get("default_resolution", "720p")),
               "--json", "--no-download"]
        ratio = shot.get("ratio") or cfg.get("vertical_ratio", "16:9")
        if ratio:
            cmd += ["--aspect-ratio", ratio]  # 注意：不是 --ratio（那是 Seedance 专用，会静默出错）
        if shot.get("seed") is not None:
            cmd += ["--seed", str(shot["seed"])]
        if shot.get("ref"):
            cmd += ["--ref", str(shot["ref"])]
        if shot.get("negative_prompt"):
            cmd += ["--negative-prompt", str(shot["negative_prompt"])]

        # kit 自己从环境变量读 Key；这里不把 Key 放进 cmd（避免命令回显泄露）
        try:
            cp = run(cmd, capture=True, check=False)
        except Exception as e:
            # 子进程都没起来 = 提交状态未知，保留 submitting，绝不重发
            return Result(STATUS_UNKNOWN, detail=f"生成子进程异常: {_scrub_short(str(e))}")

        # stdout 的 JSON 是权威结果；stderr 的 [submit] 行也能给出早期 task_id
        out = (cp.stdout or "").strip()
        tid = None
        url = None
        try:
            lines = [l for l in out.splitlines() if l.strip().startswith("{")]
            if lines:
                data = json.loads(lines[-1])
                tid = data.get("task_id")
                url = data.get("url")
                model = data.get("model") or model
        except (ValueError, TypeError):
            pass
        if not tid:
            tid = _tid_from_stderr(cp.stderr or "")

        if cp.returncode != 0 and not tid:
            # 明确失败 vs 未知：非 0 但拿不到任何 ID，按未知保守处理（PT 可能已收下）
            return Result(STATUS_UNKNOWN, detail=f"生成退出 {cp.returncode} 且无 task_id（提交状态未知，不重发）")

        if not tid:
            return Result(STATUS_UNKNOWN, detail="生成命令结束但拿不到 task_id（提交状态未知，不重发）")

        # 拿到 ID：刷新 manifest 为 submitted（结果链接可选、会过期）
        rec2 = {
            "shot_id": shot_id, "model": model, "task_id": tid,
            "status": "submitted", "updated_at": _now(),
        }
        if url:
            rec2["result_url"] = url
        manifest = load_manifest(job, cfg)
        manifest["shots"][shot_id] = rec2
        save_manifest(job, manifest, cfg)
        return Result(OK, task_id=tid, detail="submitted")

    # ---------- 下载（按 task_id 查状态 → 取回；三态）----------
    def download(self, job: str, shot_id: str, dest: Path,
                 cfg: dict | None = None) -> Result:
        cfg = cfg or load_config()
        manifest = load_manifest(job, cfg)
        rec = manifest["shots"].get(shot_id, {})
        tid = rec.get("task_id")
        if not tid:
            return Result(STATUS_UNKNOWN, detail=f"{shot_id} 无 task_id，不能下载（更不能重生成）")

        # 结果链接会过期：先按 task_id 查状态，拿新链接
        try:
            state, fresh_url = self._query_status(tid)
        except Exception as e:
            return Result(STATUS_UNKNOWN, detail=f"查状态失败（不重下/不重生成）: {_scrub_short(str(e))}")

        # 白名单：只有明确成功态才下载；PT 实际返回 in_progress/queued/completed，
        # 凡未明确成功的（含未识别状态）一律归 STATUS_UNKNOWN，保守不重下不重生成。
        if state in ("failed", "cancelled"):
            return Result(TASK_FAILED, task_id=tid, detail=f"PT 明确失败: {state}")
        if state not in ("succeeded", "completed"):
            return Result(STATUS_UNKNOWN, task_id=tid, detail=f"任务未完成/状态未知: {state}")

        url = fresh_url or rec.get("result_url")
        if not url:
            return Result(DOWNLOAD_FAILED, task_id=tid, detail="任务成功但无结果链接")

        dest.parent.mkdir(parents=True, exist_ok=True)
        if not self._fetch_binary(url, dest):
            return Result(DOWNLOAD_FAILED, task_id=tid, detail="下载失败（任务成功，只重下不重生成）")

        manifest = load_manifest(job, cfg)
        manifest["shots"].setdefault(shot_id, {}).update({"status": "downloaded", "updated_at": _now()})
        save_manifest(job, manifest, cfg)
        return Result(OK, path=str(dest), task_id=tid, detail="downloaded")

    # ---------- 源视频下载（fetch 用，D1）----------
    def download_source(self, job: str, url: str | None, local: str | None,
                        dest: Path, cfg: dict | None = None) -> Result:
        """yt-dlp 下载源视频到 dest（统一 mp4）。返回 OK / DOWNLOAD_FAILED / STATUS_UNKNOWN。"""
        import shutil
        cfg = cfg or load_config()
        dest.parent.mkdir(parents=True, exist_ok=True)

        if local:
            lp = Path(local).expanduser()
            if not lp.exists():
                return Result(DOWNLOAD_FAILED, detail=f"本地文件不存在 {lp}")
            shutil.copy2(lp, dest)
            return Result(OK, path=str(dest), detail="local copy")

        if not url:
            return Result(DOWNLOAD_FAILED, detail="无 url 也无 local")

        out_tmpl = str(dest.parent / (dest.stem + ".%(ext)s"))
        ff = resolve_tool("ffmpeg")
        cmd = _yt_cmd() + [
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
            "--merge-output-format", "mp4",
            "--socket-timeout", "60", "--retries", "10", "--fragment-retries", "10",
        ]
        if ff:
            cmd += ["--ffmpeg-location", ff]
        cookie = resolve_cookie_path()
        if cookie:
            cmd += ["--cookies", cookie]  # 路径不打印（run 会 scrub）
        cmd += ["-o", out_tmpl, url]

        # 只传干净环境给 yt-dlp：不带 Key / cookie 环境变量（cookie 已显式进 cmd 且被 scrub）
        try:
            cp = run(cmd, check=False, clean_env=True)
        except Exception as e:
            return Result(STATUS_UNKNOWN, detail=f"yt-dlp 子进程异常: {_scrub_short(str(e))}")

        if cp.returncode != 0:
            # 反爬/网络抖动属于「未知可重试」，但也可能明确失败；保守归未知，不自动重下
            return Result(STATUS_UNKNOWN, detail=f"yt-dlp 退出 {cp.returncode}（不自动重下）")

        if not dest.exists():
            cands = [p for p in dest.parent.glob(dest.stem + ".*")
                     if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
            if cands:
                big = max(cands, key=lambda p: p.stat().st_size)
                shutil.move(str(big), str(dest))
        if not dest.exists():
            return Result(DOWNLOAD_FAILED, detail="yt-dlp 成功但未找到视频文件")
        return Result(OK, path=str(dest), detail="downloaded")

    # ---------- 内部：查状态 / 下载二进制 ----------
    def _query_status(self, task_id: str) -> tuple[str, str | None]:
        key = resolve_pt_key()
        if not key:
            raise RuntimeError("无 PT Key（POWERTOKENS_API_KEY 未注入）")
        req = urllib.request.Request(
            self._pt_base + self._poll_path.format(id=task_id),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (408, 429, 500, 502, 503, 504):
                return "unknown", None
            raise
        status = (data.get("status") or "").lower()
        meta = data.get("metadata") or {}
        output = data.get("output")
        output_url = output.get("url") if isinstance(output, dict) else None
        # PT 成功响应：status=completed, metadata.url。兼容其它网关的嵌套字段。
        url = (meta.get("url") or meta.get("content_url")
               or data.get("url") or output_url)
        return status or "unknown", url

    def _fetch_binary(self, url: str, dest: Path) -> bool:
        key = resolve_pt_key()
        headers = {"Authorization": "Bearer " + key} if key else {}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            return dest.exists() and dest.stat().st_size > 0
        except urllib.error.HTTPError:
            # 结果地址可能无需鉴权，再裸试一次
            try:
                req2 = urllib.request.Request(url, headers={"User-Agent": "video-remake/1.0"})
                with urllib.request.urlopen(req2, timeout=120) as r, open(dest, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                return dest.exists() and dest.stat().st_size > 0
            except Exception:
                return False
        except Exception:
            return False


# ---------- 工具 ----------
def get_backend(name: str = "kit") -> MediaBackend:
    """backend 工厂：现在只有 kit；将来接其它媒体后端时在此加分支。"""
    if name == "kit":
        return KitMediaBackend()
    raise ValueError(f"未知 backend: {name}")


def _yt_cmd() -> list[str]:
    vp = venv_python_path()
    if vp:
        site = SKILL_DIR / ".venv" / "Lib" / "site-packages"
        if (site / "yt_dlp").exists():
            return [str(vp), "-m", "yt_dlp"]
    ytd = resolve_tool("yt-dlp")
    return [ytd] if ytd else ["python", "-m", "yt_dlp"]


def _tid_from_stderr(stderr: str) -> str | None:
    import re
    m = re.search(r"task_id=([0-9a-zA-Z_.\-]+)", stderr)
    return m.group(1) if m else None


def _scrub_short(s: str) -> str:
    from _common import _scrub
    return _scrub(s)[:300]


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
