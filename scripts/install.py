"""依赖隔离安装器：plan / authorize / apply / status / uninstall。

设计原则：
- 所有依赖装进 skill 私有目录（.venv + tools/），不碰系统 Python、不写系统 PATH、不碰运行中的 dsh。
- 逐类授权：--apply 某类前必须先 --authorize 该类（用户口头同意后由 agent 调用）。
- 模型下载单独授权；真正下载由 rea-video 首次 decompose 触发，落在隔离目录 tools/models。
- 可回滚：--uninstall 只删本 skill 创建的 .venv / tools / installed.json，不碰其他任何东西。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import SKILL_DIR, resolve_python, resolve_tool, venv_python_path, which


def module_ok(module: str, exe_name: str) -> str | None:
    """验证 pip 包类工具是否可用：优先 venv python -m，回退 exe 入口。
    返回可展示的调用串，不可用返回 None。不依赖 exe 入口是否生成。"""
    vp = venv_python_path()
    if vp:
        cp = subprocess.run([str(vp), "-m", module, "--version"],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if cp.returncode == 0:
            return f"{vp} -m {module}"
    exe = resolve_tool(exe_name)
    return exe


VENV = SKILL_DIR / ".venv"
TOOLS = SKILL_DIR / "tools"
STATE = SKILL_DIR / "installed.json"

IS_WIN = platform.system() == "Windows"
VENV_BIN = "Scripts" if IS_WIN else "bin"
EXE = ".exe" if IS_WIN else ""

COMPONENTS = ["yt_dlp", "ffmpeg", "rea_video", "model_67mb"]
LABELS = {
    "yt_dlp": "yt-dlp（视频下载）",
    "ffmpeg": "ffmpeg（视频合成，二进制）",
    "rea_video": "rea-video（视频拆解，git 仓库）",
    "model_67mb": "声音分离模型（~67MB，含人声/音乐/音效，首次拆解自动下载）",
}
SIZES = {
    "yt_dlp": "~5 MB",
    "ffmpeg": "~90 MB（zip）",
    "rea_video": "~30 MB + 依赖",
    "model_67mb": "~67 MB",
}
ADMIN = {"yt_dlp": "否", "ffmpeg": "否（解压到私有目录）",
         "rea_video": "否", "model_67mb": "否"}
DEFAULT_FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def venv_python() -> Path:
    return VENV / VENV_BIN / ("python.exe" if IS_WIN else "python")


def load_state() -> dict:
    if STATE.exists():
        try:
            with open(STATE, "r", encoding="utf-8") as f:
                st = json.load(f)
        except (json.JSONDecodeError, OSError):
            st = {}
    else:
        st = {}
    for c in COMPONENTS:
        st.setdefault(c, {"authorized": False, "installed": False, "detected": None, "path": None})
    return st


def save_state(st: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def detect(cfg: dict) -> dict:
    """检测每类依赖：系统已有 or 隔离已装 or 缺失。不安装、不下载。"""
    out = {}
    # yt-dlp
    p = module_ok("yt_dlp", "yt-dlp")
    out["yt_dlp"] = {"found": bool(p), "path": p,
                     "scope": "isolated" if p and str(VENV) in str(p) else ("system" if p else None)}
    # ffmpeg
    ff = resolve_tool("ffmpeg")
    out["ffmpeg"] = {"found": bool(ff), "path": ff,
                     "scope": "isolated" if ff and str(TOOLS) in str(ff) else ("system" if ff else None)}
    # rea-video
    rea = module_ok("rea_video", "rea-video")
    out["rea_video"] = {"found": bool(rea), "path": rea,
                        "scope": "isolated" if rea and str(VENV) in str(rea) else ("system" if rea else None)}
    # model：看隔离目录和默认缓存
    model_dirs = [TOOLS / "models", Path.home() / ".cache" / "rea-video" / "models"]
    found_model = next((str(d) for d in model_dirs if d.exists() and any(d.iterdir())), None)
    out["model_67mb"] = {"found": bool(found_model), "path": found_model,
                         "scope": "isolated" if found_model and str(TOOLS) in found_model else ("system" if found_model else None)}
    return out


def cmd_plan(args, cfg) -> int:
    det = detect(cfg)
    st = load_state()
    for c in COMPONENTS:
        st[c]["detected"] = det[c]
        st[c]["path"] = det[c]["path"]
        if det[c]["found"] and not st[c]["installed"]:
            st[c]["installed"] = True
    save_state(st)

    print("=== 依赖清单（只查看，不安装）===\n")
    print(f"{'组件':<22}{'状态':<12}{'体积':<16}{'需管理员':<10}{'落点'}")
    for c in COMPONENTS:
        d = det[c]
        status = "已就绪" if d["found"] else "缺失"
        scope = d["scope"] or "-"
        place = d["path"] if d["path"] else (str(VENV / VENV_BIN) if c in ("yt_dlp", "rea_video")
                                             else str(TOOLS / "ffmpeg") if c == "ffmpeg"
                                             else str(TOOLS / "models"))
        print(f"{LABELS[c]:<22}{(status + '/' + scope):<12}{SIZES[c]:<16}{ADMIN[c]:<10}{place}")
    print("\n安装位置：全部在 skill 私有目录 .venv/ 和 tools/，不进系统 Python、不写系统 PATH。")
    print("下一步：对需要装的组件，用户确认后由 agent 逐个执行：")
    print("  python scripts/install.py --authorize <组件>     # 记录用户已授权")
    print("  python scripts/install.py --apply <组件>         # 执行安装")
    print("组件名：yt_dlp / ffmpeg / rea_video / model_67mb")
    return 0


def cmd_authorize(args, cfg) -> int:
    c = args.component
    if c not in COMPONENTS:
        print(f"未知组件 {c}，可选：{COMPONENTS}", file=sys.stderr)
        return 2
    st = load_state()
    st[c]["authorized"] = True
    save_state(st)
    print(f"[auth] 已记录授权：{LABELS[c]}（--apply 现在可执行）")
    return 0


def cmd_status(args, cfg) -> int:
    st = load_state()
    print("=== 安装与授权状态 ===\n")
    print(f"{'组件':<22}{'授权':<8}{'已装':<8}{'路径'}")
    for c in COMPONENTS:
        print(f"{LABELS[c]:<22}{'是' if st[c]['authorized'] else '否':<8}"
              f"{'是' if st[c]['installed'] else '否':<8}{st[c].get('path') or '-'}")
    return 0


def ensure_venv() -> bool:
    if venv_python().exists():
        return True
    print(f"[venv] 创建隔离环境 {VENV}")
    cp = subprocess.run([resolve_python({}), "-m", "venv", str(VENV)],
                        text=True, encoding="utf-8", errors="replace")
    return cp.returncode == 0 and venv_python().exists()


def pip_install(pkgs: list[str], cwd: Path | None = None, extra: list[str] | None = None) -> bool:
    cmd = [str(venv_python()), "-m", "pip", "install", "-U"] + (extra or []) + pkgs
    print("[cmd] " + " ".join(cmd))
    cp = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                        text=True, encoding="utf-8", errors="replace")
    return cp.returncode == 0


def download(url: str, dest: Path) -> bool:
    print(f"[download] {url}\n          -> {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "video-remake-installer/1.0"})
    last = [0.0]

    def hook(block, bsize, total):
        if total:
            pct = min(100, block * bsize * 100 // total)
            if pct - last[0] >= 10:
                print(f"  ...{pct}%")
                last[0] = pct
    try:
        urllib.request.urlretrieve(url, str(dest), reporthook=hook)
        return True
    except Exception as e:
        print(f"[error] 下载失败：{e}", file=sys.stderr)
        return False


def apply_yt_dlp(cfg) -> bool:
    if not ensure_venv():
        print("[error] 创建 venv 失败", file=sys.stderr)
        return False
    # 调用链已改用 venv python -m，不依赖 exe 入口，普通安装即可
    if not pip_install(["yt-dlp"]):
        return False
    p = module_ok("yt_dlp", "yt-dlp")
    print(f"[ok] yt-dlp -> {p}")
    return bool(p)


def apply_ffmpeg(cfg) -> bool:
    if not IS_WIN:
        print("[提示] 非 Windows 平台的 ffmpeg 隔离安装未自动化，请用包管理器安装后重跑 --plan：\n"
              "  macOS: brew install ffmpeg\n  Ubuntu: apt install ffmpeg", file=sys.stderr)
        return False
    url = cfg.get("ffmpegSource") or DEFAULT_FFMPEG_URL
    zpath = TOOLS / "_ffmpeg.zip"
    if not download(url, zpath):
        return False
    print("[extract] 解压 ffmpeg ...")
    try:
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(TOOLS)
        zpath.unlink()
    except (zipfile.BadZipFile, OSError) as e:
        print(f"[error] 解压失败：{e}", file=sys.stderr)
        return False
    # 找到 ffmpeg.exe（gyan build 在 <dir>/bin/ffmpeg.exe）
    found = next(TOOLS.rglob("ffmpeg.exe"), None)
    if not found:
        print("[error] 解压后未找到 ffmpeg.exe", file=sys.stderr)
        return False
    print(f"[ok] ffmpeg -> {found}")
    return True


REA_TARBALL = "https://codeload.github.com/JuneYaooo/rea-video/zip/refs/heads/main"


def fetch_rea_source(repo: Path) -> bool:
    """拉 rea-video 源码：tarball 优先（urllib 单连接，抗 schannel 断连），git clone 兜底。"""
    import io
    import zipfile
    TOOLS.mkdir(parents=True, exist_ok=True)
    # 1) tarball，带重试
    for attempt in range(1, 4):
        try:
            print(f"[tarball try {attempt}] 下载 rea-video ...", flush=True)
            req = urllib.request.Request(REA_TARBALL,
                                         headers={"User-Agent": "video-remake-installer/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(TOOLS)
            extracted = TOOLS / "rea-video-main"
            if extracted.exists():
                if repo.exists():
                    shutil.rmtree(repo)
                extracted.rename(repo)
            if (repo / "pyproject.toml").exists():
                print(f"[ok] tarball 解压 -> {repo}", flush=True)
                return True
        except Exception as e:
            print(f"[retry] tarball 失败：{e}", flush=True)
            time.sleep(3)
    # 2) git clone 兜底
    print("[fallback] 改用 git clone ...", flush=True)
    cp = subprocess.run(["git", "clone", "--depth", "1",
                         "https://github.com/JuneYaooo/rea-video.git", str(repo)],
                        text=True, encoding="utf-8", errors="replace")
    return cp.returncode == 0 and (repo / "pyproject.toml").exists()


def apply_rea_video(cfg) -> bool:
    if not ensure_venv():
        return False
    repo = TOOLS / "rea-video"
    if not (repo / "pyproject.toml").exists():
        if not fetch_rea_source(repo):
            print("[error] rea-video 源码拉取失败（tarball 与 git 均失败）", file=sys.stderr)
            return False
    print("[pip] 安装 rea-video[ocr] 到隔离 venv ...")
    cmd = [str(venv_python()), "-m", "pip", "install", "-U", "-e", ".[ocr]"]
    print("[cmd] " + " ".join(cmd))
    cp = subprocess.run(cmd, cwd=str(repo), text=True, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        print("[error] rea-video 安装失败", file=sys.stderr)
        return False
    # 修复 opencv-python 与 headless 抢 cv2 目录的已知冲突（README 明示）
    print("[fix] 修复 opencv 冲突 ...")
    subprocess.run([str(venv_python()), "-m", "pip", "uninstall", "-y", "opencv-python"],
                   text=True, encoding="utf-8", errors="replace")
    subprocess.run([str(venv_python()), "-m", "pip", "install", "--force-reinstall", "--no-deps",
                    "opencv-python-headless>=4.8,<5"],
                   text=True, encoding="utf-8", errors="replace")
    p = module_ok("rea_video", "rea-video")
    print(f"[ok] rea-video -> {p}")
    return bool(p)


def apply_model(cfg) -> bool:
    # 不主动下载：rea-video 首次 decompose 时按 REA_VIDEO_MODELS 下载到隔离目录。
    # 这里只建目录并标记授权，真正体积提示已在 --plan 展示。
    (TOOLS / "models").mkdir(parents=True, exist_ok=True)
    print(f"[ok] 模型目录就绪：{TOOLS / 'models'}")
    print("[note] 67MB 模型将在首次 decompose 时由 rea-video 自动下载到该目录（隔离、可卸载）。")
    return True


def cmd_apply(args, cfg) -> int:
    c = args.component
    if c not in COMPONENTS:
        print(f"未知组件 {c}，可选：{COMPONENTS}", file=sys.stderr)
        return 2
    st = load_state()
    if not st[c]["authorized"] and not args.yes:
        print(f"[拒绝] {LABELS[c]} 尚未授权。请先让用户确认，然后：\n"
              f"  python scripts/install.py --authorize {c}\n"
              f"再执行 --apply {c}。（--yes 仅用于已获授权的自动化场景）", file=sys.stderr)
        return 3
    print(f"=== 安装 {LABELS[c]} ===\n体积 {SIZES[c]} · 落点 skill 私有目录 · 需管理员：{ADMIN[c]}")
    fn = {"yt_dlp": apply_yt_dlp, "ffmpeg": apply_ffmpeg,
          "rea_video": apply_rea_video, "model_67mb": apply_model}[c]
    ok = fn(cfg)
    if ok:
        st[c]["authorized"] = True
        st[c]["installed"] = True
        det = detect(cfg)
        st[c]["path"] = det[c]["path"]
        save_state(st)
        print(f"\n[done] {LABELS[c]} 安装完成。")
        return 0
    print(f"\n[fail] {LABELS[c]} 安装失败，状态未标记为已装，可重试。", file=sys.stderr)
    return 1


def cmd_uninstall(args, cfg) -> int:
    st = load_state()
    targets = []
    if args.component in (None, "yt_dlp", "rea_video"):
        targets.append(VENV)
    if args.component in (None, "ffmpeg", "model_67mb", "rea_video"):
        targets.append(TOOLS)
    print("=== 卸载预览（将删除以下 skill 私有目录）===")
    for t in targets:
        print(f"  {t}")
    if not args.yes:
        print("\n未加 --yes，未做任何删除。确认后加 --yes 执行卸载。")
        return 0
    for t in targets:
        if t.exists():
            shutil.rmtree(t)
            print(f"[removed] {t}")
    for c in COMPONENTS:
        st[c]["installed"] = False
        st[c]["path"] = None
    save_state(st)
    print("[done] 已卸载（installed.json 状态已复位）。系统环境未被改动。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="video-remake 依赖隔离安装器")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true", help="列出依赖清单，不安装")
    g.add_argument("--status", action="store_true", help="查看授权与安装状态")
    ap.add_argument("--authorize", dest="component", metavar="COMP", help="记录用户已授权某组件")
    ap.add_argument("--apply", dest="apply_component", metavar="COMP", help="安装某组件")
    ap.add_argument("--uninstall", dest="uninstall_component", nargs="?", const="all", metavar="COMP",
                    help="卸载（可指定组件，默认全部）")
    ap.add_argument("--yes", action="store_true", help="确认执行（自动化用；正常流程靠 --authorize）")
    args = ap.parse_args()

    from _common import load_config
    cfg = load_config()

    if args.plan:
        return cmd_plan(args, cfg)
    if args.status:
        return cmd_status(args, cfg)
    if args.component:
        return cmd_authorize(args, cfg)
    if args.apply_component:
        args.component = args.apply_component
        return cmd_apply(args, cfg)
    if args.uninstall_component is not None:
        args.component = args.uninstall_component if args.uninstall_component != "all" else None
        return cmd_uninstall(args, cfg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
