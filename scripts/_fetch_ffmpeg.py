"""一次性工具：带断点续传 + 校验的 ffmpeg 下载，替代易断的单次 urlretrieve。"""
import os
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
TOOLS = Path(__file__).resolve().parent.parent / "tools"
ZIP = TOOLS / "_ffmpeg.zip"


def valid_zip(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        with zipfile.ZipFile(p) as zf:
            return bool([n for n in zf.namelist() if n.endswith("ffmpeg.exe")])
    except Exception:
        return False


def main():
    TOOLS.mkdir(parents=True, exist_ok=True)
    if valid_zip(ZIP):
        print(f"[skip] zip already valid: {ZIP}", flush=True)
    else:
        # 断点续传：已有字节则从该 offset 继续
        for attempt in range(1, 9):
            have = ZIP.stat().st_size if ZIP.exists() else 0
            # 若已下载的部分恰好是完整包，先验证
            if valid_zip(ZIP):
                break
            print(f"[try {attempt}] resume from {have/1024/1024:.1f} MB ...", flush=True)
            req = urllib.request.Request(URL, headers={"User-Agent": "video-remake-installer/1.0"})
            if have > 0:
                req.add_header("Range", f"bytes={have}-")
            try:
                with urllib.request.urlopen(req, timeout=120) as r, open(ZIP, "ab") as f:
                    total = int(r.headers.get("Content-Length", 0)) + have
                    got = have
                    while True:
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                    print(f"[ok] got {got/1024/1024:.1f} / {total/1024/1024:.1f} MB", flush=True)
            except Exception as e:
                print(f"[retry] {e}", flush=True)
                time.sleep(3)
            if valid_zip(ZIP):
                print("[ok] zip complete & valid", flush=True)
                break
        if not valid_zip(ZIP):
            print("[fail] could not get a valid ffmpeg zip after retries", flush=True)
            return 1

    print("[extract] 解压 ffmpeg ...", flush=True)
    with zipfile.ZipFile(ZIP) as zf:
        zf.extractall(TOOLS)
    found = next(TOOLS.rglob("ffmpeg.exe"), None)
    if not found:
        print("[fail] ffmpeg.exe not found after extract", flush=True)
        return 1
    ZIP.unlink()
    print(f"[done] ffmpeg -> {found}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
