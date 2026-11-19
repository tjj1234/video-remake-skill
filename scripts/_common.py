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


# 全链路统一：调用方只传这一个环境变量；kit CLI 原生读它（无下划线）。
KEY_ENV = "POWERTOKENS_API_KEY"
COOKIE_ENV = "REMAKE_COOKIE_PATH"
# 脱敏替换表：任何进入日志/命令回显的地方都不允许出现这些值
_SENSITIVE_ENV = (KEY_ENV, "POWER_TOKENS_API_KEY", "REA_VLM_API_KEY", COOKIE_ENV)


def resolve_pt_key() -> str:
    """只认调用方传入的环境变量 POWERTOKENS_API_KEY，没有就返回空。

    S3 硬规则：不读任何其他 skill 的配置文件、不落盘、不打印。
    """
    return os.environ.get(KEY_ENV, "").strip()


def resolve_cookie_path() -> str:
    """只认调用方传入的环境变量 REMAKE_COOKIE_PATH（cookie 文件路径）。

    与 Key 同等安全处理：不落盘、不打印、不进 manifest；无则空串。
    """
    return os.environ.get(COOKIE_ENV, "").strip()


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


def dump_json_atomic(path: Path, data) -> None:
    """原子写：先写同目录临时文件再 os.replace，避免崩在中途留下坏 manifest。"""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


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


def _scrub(text: str) -> str:
    """把环境变量里的敏感值从任意文本中抹掉（Key / cookie 路径），用于日志与异常回显。"""
    if not text:
        return text
    for name in _SENSITIVE_ENV:
        val = os.environ.get(name, "").strip()
        if val:
            text = text.replace(val, "***REDACTED***")
    return text


def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        env: dict | None = None, cwd: Path | None = None,
        clean_env: bool = False) -> subprocess.CompletedProcess:
    """统一子进程封装：脱敏打印命令、可捕获输出、失败抛异常。

    S3 最小传递：
    - 默认继承父进程环境（调用方注入的 Key 靠 kit 自己读环境变量）。
    - clean_env=True 时，构造一份**不含任何敏感环境变量**的干净环境再叠加显式 env，
      给不需要鉴权的子进程（ffmpeg / yt-dlp 等）用，杜绝 Key 被整套复制下去。
    - 打印与异常回显一律经 _scrub 脱敏。
    """
    print("[cmd] " + _scrub(" ".join(str(x) for x in cmd)), flush=True)
    if clean_env:
        run_env = {k: v for k, v in os.environ.items() if k not in _SENSITIVE_ENV}
    else:
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
        tail = _scrub((cp.stdout or "") + (cp.stderr or ""))
        raise RuntimeError(f"命令失败(exit {cp.returncode}): {cmd[0]}\n{tail[-1500:]}")
    return cp


def _probe_ytdlp_version() -> str:
    """探测当前实际使用的 yt-dlp 版本（优先 skill 私有 venv）。"""
    import subprocess as _sp
    vp = venv_python_path()
    try:
        if vp:
            cp = _sp.run([str(vp), "-m", "yt_dlp", "--version"],
                         capture_output=True, text=True, timeout=20)
        else:
            cp = _sp.run(["python", "-m", "yt_dlp", "--version"],
                         capture_output=True, text=True, timeout=20)
        v = (cp.stdout or "").strip().splitlines()
        if cp.returncode == 0 and v:
            return v[-1].strip()
    except Exception:
        pass
    return "unknown"


def load_manifest(job: str, cfg: dict | None = None) -> dict:
    """读取 job 的 manifest.json（生成/下载任务的权威记录），不存在返回带版本号的空壳。"""
    m = read_json(job_dir(job, cfg) / "manifest.json", None)
    if not isinstance(m, dict):
        m = {}
    m.setdefault("ytdlp_version", _probe_ytdlp_version())
    m.setdefault("shots", {})
    return m


def save_manifest(job: str, manifest: dict, cfg: dict | None = None) -> None:
    """原子写 manifest.json；写入前强制剔除任何疑似 Key / cookie 字段。"""
    safe = {k: v for k, v in manifest.items()
            if k.lower() not in ("key", "api_key", "apikey", "pt_key", "cookie", "cookie_path")}
    if "shots" in safe and isinstance(safe["shots"], dict):
        safe["shots"] = {
            sid: {sk: sv for sk, sv in rec.items()
                  if sk.lower() not in ("key", "api_key", "apikey", "pt_key", "cookie", "cookie_path")}
            for sid, rec in safe["shots"].items() if isinstance(rec, dict)
        }
    dump_json_atomic(job_dir(job, cfg) / "manifest.json", safe)
