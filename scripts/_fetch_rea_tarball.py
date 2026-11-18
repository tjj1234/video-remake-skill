"""一次性工具：用 tarball 下载 rea-video（替代易断的 git clone），带重试。"""
import io
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

URL = "https://codeload.github.com/JuneYaooo/rea-video/zip/refs/heads/main"
TOOLS = Path(__file__).resolve().parent.parent / "tools"
DEST = TOOLS / "rea-video"


def main():
    TOOLS.mkdir(parents=True, exist_ok=True)
    if DEST.exists():
        shutil.rmtree(DEST)
    data = None
    for attempt in range(1, 6):
        try:
            print(f"[try {attempt}] download rea-video tarball ...", flush=True)
            req = urllib.request.Request(URL, headers={"User-Agent": "video-remake-installer/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            print(f"[ok] got {len(data)/1024:.0f} KB", flush=True)
            break
        except Exception as e:
            print(f"[retry] {e}", flush=True)
            time.sleep(3)
    if not data:
        print("[fail] could not download tarball", flush=True)
        return 1
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(TOOLS)
    # 解压出 rea-video-main/，重命名为 rea-video/
    extracted = TOOLS / "rea-video-main"
    if extracted.exists():
        extracted.rename(DEST)
    if not (DEST / "pyproject.toml").exists():
        print("[fail] pyproject.toml not found after extract", flush=True)
        return 1
    print(f"[done] rea-video source -> {DEST}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
