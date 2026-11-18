"""共用工具：路径、配置、子进程、产物落盘、断点状态。仅用标准库。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"
EXAMPLE_PATH = SKILL_DIR / "config.example.json"


def load_config() -> dict:
    src = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    with open(src, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_pt_key() -> str:
    """复用 powertokens-video skill 已配置的 PT Key，避免重复配置。

    优先级：环境变量 POWER_TOKENS_API_KEY > powertokens-video/config.json 的 api_key。
    Key 只做进程内传递，不写入本 skill 的任何产物。
    """
    env = os.environ.get("POWER_TOKENS_API_KEY", "").strip()
    if env:
        return env
    for raw in (
        "~/.workbuddy/skills/powertokens-video/config.json",
        "~/.agents/skills/powertokens-video/config.json",
        "~/.config/powertokens-video/config.json",
    ):
        p = Path(raw).expanduser()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                key = data.get("api_key", "")
                if key:
                    return key
            except (OSError, ValueError):
                pass
    return ""


def job_dir(job: str, cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    workdir = Path(cfg.get("workdir", "out"))
    if not workdir.is_absolute():
        workdir = SKILL_DIR / workdir
    safe = "".join(c for c in job if c.isalnum() or c in "-_") or "job"
    return workdir / safe


def state_path(job: str, cfg: dict | None = None) -> Path:
    return job_dir(job, cfg) / "state.json"


def load_state(job: str, cfg: dict | None = None) -> dict:
    p = state_path(job, cfg)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"steps": {}}


def mark_done(job: str, step: str, detail: str = "", cfg: dict | None = None) -> None:
    st = load_state(job, cfg)
    st["steps"][step] = {"status": "done", "detail": detail}
    p = state_path(job, cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def is_done(job: str, step: str, cfg: dict | None = None) -> bool:
    return load_state(job, cfg)["steps"].get(step, {}).get("status") == "done"


def dump_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def resolve_python(cfg: dict) -> str:
    py = cfg.get("python") or ""
    if py and Path(py).exists():
        return py
    return sys.executable


def venv_python_path() -> Path | None:
    """隔离 venv 的 python 可执行文件；未创建则 None。"""
    p = SKILL_DIR / ".venv" / _VENV_BIN / ("python.exe" if _IS_WIN else "python")
    return p if p.exists() else None


def resolve_pt_video(cfg: dict) -> Path | None:
    explicit = cfg.get("pt_video_script", "")
    candidates = ([explicit] if explicit else []) + cfg.get("pt_video_candidates", [])
    for raw in candidates:
        p = Path(raw).expanduser()
        if p.exists():
            return p
    return None


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


_IS_WIN = sys.platform.startswith("win")
_VENV_BIN = "Scripts" if _IS_WIN else "bin"
_EXE = ".exe" if _IS_WIN else ""


def resolve_tool(name: str) -> str | None:
    """解析可执行文件：优先 skill 私有隔离目录（.venv / tools），再回退系统 PATH。

    隔离安装后无需改系统环境变量，这里统一负责找到它们。
    - yt-dlp / rea-video：.venv/Scripts 或 .venv/bin
    - ffmpeg：tools/ 下递归查找（gyan zip 解压后有多层目录）
    """
    # 1) venv 的 Scripts/bin
    venv_exe = SKILL_DIR / ".venv" / _VENV_BIN / (name + _EXE)
    if venv_exe.exists():
        return str(venv_exe)

    # 2) tools/ 下递归（ffmpeg 等二进制）
    tools = SKILL_DIR / "tools"
    if tools.exists():
        for candidate in tools.rglob(name + _EXE):
            if candidate.is_file():
                return str(candidate)

    # 3) 系统 PATH
    return shutil.which(name)


def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """统一子进程封装：打印命令、可捕获输出、失败抛异常。"""
    print("[cmd] " + " ".join(str(x) for x in cmd), flush=True)
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    cp = subprocess.run(
        [str(x) for x in cmd],
        cwd=str(cwd) if cwd else None,
        env=run_env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )
    if check and cp.returncode != 0:
        tail = (cp.stdout or "") + (cp.stderr or "")
        raise RuntimeError(f"命令失败(exit {cp.returncode}): {cmd[0]}\n{tail[-1500:]}")
    return cp
