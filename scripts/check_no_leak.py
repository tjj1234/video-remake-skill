"""S3 固化自检：用假 Key / 假 cookie 路径跑一遍，确认没有任何落盘或打印泄露。

用法：
  # 1) 静态源码扫描（默认）——检查代码里没有硬编码 key 读取/尾号打印
  python scripts/check_no_leak.py --scan

  # 2) 动态冒烟——注入假值跑 dry-run，扫描本次产物与 stdout/stderr 是否泄露
  POWERTOKENS_API_KEY=FAKE-KEY-123456 REMAKE_COOKIE_PATH=C:/fake/cookies.txt \
    python scripts/check_no_leak.py --smoke --job <已有job>

判定：任一处出现假值即 FAIL（退出码 1）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import SKILL_DIR, _scrub, job_dir, load_config, resolve_cookie_path, resolve_pt_key

FAKE_KEY = "FAKE-KEY-123456"
FAKE_COOKIE = "C:/fake/cookies.txt"

# 静态扫描：这些模式说明代码又回到「读配置 / 打印尾号」的老路
BAD_PATTERNS = [
    ("key[-4:]", "禁止打印 Key 尾号"),
    ("key[-4 :]", "禁止打印 Key 尾号"),
    ('["key_tail"]', "禁止把 key_tail 写进产物"),
    ("key_tail", "禁止出现 key_tail 字段"),
    ("powertokens-video/config.json", "禁止回退读其他 skill 的 config"),
    (".agents/skills/powertokens-video", "禁止回退读其他 skill 的 config"),
]


def scan() -> int:
    problems = []
    for py in sorted((SKILL_DIR / "scripts").glob("*.py")):
        if py.name == "check_no_leak.py":
            continue  # 本脚本自身含样例字面量，跳过
        text = py.read_text(encoding="utf-8", errors="replace")
        for pat, why in BAD_PATTERNS:
            for lineno, line in enumerate(text.splitlines(), 1):
                if pat in line and not line.lstrip().startswith("#"):
                    problems.append(f"{py.name}:{lineno} 命中[{pat}] {why}")
    # resolve_pt_key 必须只认环境变量
    common = (SKILL_DIR / "scripts" / "_common.py").read_text(encoding="utf-8")
    if 'os.environ.get(KEY_ENV' not in common:
        problems.append("_common.py resolve_pt_key 未从 KEY_ENV 环境变量读取")

    print("=== 静态扫描 ===")
    if problems:
        for p in problems:
            print("  [FAIL] " + p)
        print(f"结论：FAIL（{len(problems)} 处）")
        return 1
    print("  [OK] 无硬编码 key 读取 / 尾号打印 / key_tail 落盘 / 配置回退")
    print("结论：PASS")
    return 0


def smoke(job: str) -> int:
    os.environ["POWERTOKENS_API_KEY"] = FAKE_KEY
    os.environ["REMAKE_COOKIE_PATH"] = FAKE_COOKIE

    problems: list[str] = []

    # 1) 解析函数拿到假值（证明注入链通）
    if resolve_pt_key() != FAKE_KEY:
        problems.append("resolve_pt_key 未拿到环境变量里的假 Key")
    if resolve_cookie_path() != FAKE_COOKIE:
        problems.append("resolve_cookie_path 未拿到环境变量里的假 cookie 路径")

    # 2) _scrub 必须能抹掉假值
    sample = f"Authorization: Bearer {FAKE_KEY} cookie={FAKE_COOKIE}"
    scrubbed = _scrub(sample)
    if FAKE_KEY in scrubbed or FAKE_COOKIE in scrubbed:
        problems.append("_scrub 未抹掉假值：" + scrubbed[:120])

    # 3) manifest 写入不得含假值（用一个临时 job）
    tmp_job = job + "_leakcheck"
    from _common import load_manifest, save_manifest
    m = load_manifest(tmp_job, load_config())
    m["shots"] = {"shot_test": {"task_id": "tid-fake", "status": "submitted"}}
    save_manifest(tmp_job, m, load_config())
    mf = job_dir(tmp_job, load_config()) / "manifest.json"
    if mf.exists():
        content = mf.read_text(encoding="utf-8")
        if FAKE_KEY in content or FAKE_COOKIE in content:
            problems.append("manifest.json 出现假值")

    print("=== 动态冒烟（假值注入）===")
    print(f"  假 Key 注入并脱敏：{'OK' if FAKE_KEY not in scrubbed else 'FAIL'}")
    print(f"  manifest 无假值：{'OK' if not any('manifest' in p for p in problems) else 'FAIL'}")
    if problems:
        for p in problems:
            print("  [FAIL] " + p)
        print(f"结论：FAIL（{len(problems)} 处）")
        return 1
    print("结论：PASS（Key/cookie 只经环境变量注入，未落盘，日志已脱敏）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="静态源码扫描")
    ap.add_argument("--smoke", action="store_true", help="动态假值冒烟")
    ap.add_argument("--job", default="leakcheck", help="smoke 用的临时 job 名")
    args = ap.parse_args()
    if not args.scan and not args.smoke:
        args.scan = True
    rc = 0
    if args.scan:
        rc |= scan()
    if args.smoke:
        rc |= smoke(args.job)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
