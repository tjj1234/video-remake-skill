---
name: video-remake
description: 把一条 YouTube / TikTok / 短视频链接拆解后，用 PowerTokens 模型重做成一条全新的竖屏 AI 短视频。仅当用户【贴出视频链接】并说 remake / 复刻 / 拆解后重做 / 照这个做一条 / 分析这个视频怎么做的然后重现时才用；也用于本 skill 的依赖安装/卸载管理。纯文生视频、把图片变视频等请求不要用本 skill，交给 powertokens-video。覆盖：yt-dlp 下载 → rea-video 逐镜头拆解 → 配方转生成参数 → PT 批量生成 → FFmpeg 合成。依赖缺失时可通过 install.py 在用户逐类授权后装到 skill 私有目录。
---

# Video Remake Skill

用户给一条视频链接，产出一条「结构参考、画面全新生成」的 9:16 短视频。
本 skill 是一个**编排器**：把 rea-video（拆解）和 powertokens-kit 的 `pt_video.py`（生成）当脚本调用，
用户只需贴链接 + 确认镜头表。

## 硬边界（先读，别跳过）

- 只学**结构 / 节奏 / 镜头语言**，不搬运原片画面、音乐、人物、形象。
- 下载的原片仅用于本地拆解；`keep_source=false` 时合成完自动删除。
- 发布前按 `references/compliance.md` 做 AI 内容标注。

## 环境约定

- 所有脚本在 skill 根目录运行：`cd <skill_dir>`。
- 产物落盘到 `out/<job_id>/`，每步可单独重跑，已完成的步骤自动跳过。
- 用本机 Python：`python scripts/<脚本>.py`（Windows 用 `py -3` 或绝对路径亦可）。
- `config.json` 从 `config.example.json` 复制而来；Key **不写在这里**，由 `pt_video.py` 自己管理。

## 依赖自动安装（缺失时走这里，授权闸门）

依赖装到 **skill 私有目录**（`.venv/` + `tools/`），不碰系统 Python、不写系统 PATH、不碰运行中的服务，
卸载即删目录。**绝不静默安装，必须逐类授权。**

1. preflight 报缺失后，先跑 `python scripts/install.py --plan`，把清单（组件/体积/落点/是否需管理员）原样展示给用户。
2. 用户明确同意某一类后，才逐类执行：
   ```bash
   python scripts/install.py --authorize yt_dlp     # 记录用户已授权该类
   python scripts/install.py --apply yt_dlp         # 才真正安装
   ```
   四类组件名：`yt_dlp` / `ffmpeg` / `rea_video` / `model_67mb`。
3. **模型（~67MB）单独一步**：装完 rea-video 后单独问用户，同意才 `--authorize model_67mb` + `--apply model_67mb`；
   模型在首次 decompose 时才由 rea-video 下载到隔离目录 `tools/models`。
4. 每类装完重跑 `python scripts/install.py --status` 确认，再回 preflight 复检。
5. 未授权时 `--apply` 会直接拒绝（exit 3）。`--uninstall` 默认只预览不删，加 `--yes` 才删。
6. 安装失败（网络/仓库不可达）不要卡死：告诉用户可改用 `--local` 手动放视频绕过下载。

## 工作流（严格按序）

### Step 0 · 预检
```bash
python scripts/preflight.py --job JOB
```
检测 Python / ffmpeg / yt-dlp / rea-video / `pt_video.py` / PT Key。
任何一项缺失 → 停下，把 `out/_preflight.json` 里的 `install_hints` 转述给用户，不要硬跑。

### Step 1 · 下载
```bash
python scripts/fetch.py --job JOB --url "https://..."
# 或用户已放好本地文件：
python scripts/fetch.py --job JOB --local /path/to/video.mp4
```
产物：`out/JOB/source.mp4` + `out/JOB/subs.vtt` + `out/JOB/meta.json`。

### Step 2 · 拆解
```bash
python scripts/decompose.py --job JOB            # 离线取证模式（不花钱）
python scripts/decompose.py --job JOB --vlm      # 加视觉模型（重压缩源建议开；烧 chat token）
```
产物：`out/JOB/ref/breakdown.json`、`VIDEO.md`、`report.html`、`packaging/*.srt`。
提醒：平台下载的是重压缩源，官方建议开 `--vlm` 才准。

**VLM 用 PT 自家的模型（默认已配好，零重复配置）：**
- `config.json` 的 `vlm` 段默认 `base_url=https://api.powertokens.ai/v1`、`model=qwen3.8-flash`、`media=frames`。
- Key **自动复用** powertokens-video skill 已配的 `api_key`（不重复配置、不写进本 skill 产物）；也可用环境变量 `POWER_TOKENS_API_KEY` 覆盖。
- 模型选择依据真机探测（2026-11-17）：`qwen3.8-flash` 能正确看图、最省钱；deepseek-v4-pro / seed-2-0-pro / MiniMax-M3 均 vision 可用但更贵；`glm-5.3-flash` 返回空内容，别用。要换模型改 `vlm.model`。
- **前提：PT Key 的 chat/token 额度充足。** 额度耗尽时 rea-video 会记 `VLM 部分生效/失败` 并自动回退到纯像素取证（不中断任务）——看到报告里 "VLM 部分生效" 且多数镜头标"待确认①/②"，就是额度问题，去 powertokens.ai 充值 token 额度后重跑即可。
- `--no-stems` 只跳过声音分离（~67MB 模型），与 VLM 无关，可同时使用省钱。

### Step 3 · 人工卡点（必须）
```bash
python scripts/recipe_to_prompt.py --job JOB
cat out/JOB/shots.json
```
把 `shots.json` 的镜头表摘要给用户看，**等用户确认**再生成。
如解析不全，`shots.json` 会带 `_source` 指向原始 `breakdown.json`，按 `references/prompt-mapping.md` 手工补齐。

### Step 4 · 批量生成
```bash
python scripts/generate.py --job JOB
```
逐镜头调 `pt_video.py generate`，产物 `out/JOB/clips/shot_001.mp4` …。异步轮询由 pt_video.py 负责。

### Step 5 · 合成
```bash
python scripts/compose.py --job JOB --burn-subs
```
按原时间线拼接 + 烧字幕 + 归一化到 config 目标画幅，产物 `out/JOB/final.mp4`。

### Step 6 · 烧录英文字幕（可选，独立步骤）
旁白是自生成时，把旁白文案逐句烧成字幕（白字黑描边、底部居中）：
```bash
python scripts/burn_subs.py --job JOB --auto                    # 从旁白文案+静音点自动生成 SRT 并烧录
python scripts/burn_subs.py --job JOB --srt path/to/sub.srt     # 用自定义 SRT 烧录
```
产物 `out/JOB/final_subbed.mp4`（保留无字幕版）。自动模式读 `audio/audio_meta.json`
的 `narration_text`，用 silencedetect 停顿点做句级对齐；自定义 SRT 用 UTF-8。
Windows 下用 subprocess list + cwd 相对路径，规避盘符冒号/反斜杠转义坑。

## 一键编排
```bash
python scripts/pipeline.py --job JOB --url "https://..."          # 到 Step 3 停，等确认
python scripts/pipeline.py --job JOB --resume-from generate        # 确认后继续
python scripts/pipeline.py --job JOB --url "..." --auto            # 跳过人工卡点（慎用）
```
每步产物落盘 + `state.json` 记录状态，任何一步失败都能从该步重跑。
失败时看 `out/JOB/state.json` 的 `detail` 字段定位原因。

## 参考文档（按需加载，别一次全读）

- `references/workflow.md`：五步详细流程、产物清单、断点重跑、失败处理。
- `references/prompt-mapping.md`：breakdown.json 字段 → 生成参数映射、render_type 分流规则。
- `references/compliance.md`：版权红线、AI 标注、下载即删。
