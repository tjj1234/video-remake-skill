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
- `config.json` 从 `config.example.json` 复制而来；**Key 与 cookie 都不写在这里**。

### 敏感信息注入（调用方负责，skill 零落盘零打印）
- **PT Key**：调用方设环境变量 `POWERTOKENS_API_KEY`（无下划线，kit CLI 原生读它）。skill 不读任何其他 skill 的配置、不落盘、不打印（连尾号都不打印）。没有就报错。
- **cookie**：TikTok/Instagram 需要登录态时，调用方设 `REMAKE_COOKIE_PATH=<cookies.txt 路径>`。同样只经环境变量、不进 skill 目录、不打印、不进 manifest。
- 验证：`python scripts/check_no_leak.py --scan --smoke`（假 Key/cookie 注入后扫所有产物与日志，任一处泄露即 FAIL）。每次改代码都要重跑。

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

## 用户交互卡片（必须遵守）

**卡片不是脚本产物，也不是某个客户端的 UI 组件**——它是 AI 执行本 skill 时，按
`references/cards.md` 规范在当前对话里现渲染的交互层。所以无论 WorkBuddy / dsh / 其它 Agent，
加载本 skill 后都必须在对应节点弹卡片（详见 `references/cards.md`）。

四张卡与工作流的挂接点（**门控卡弹出后必须停下等用户确认，不得自动越过花钱步骤**）：

| 卡片 | 何时弹 | 门控 |
|---|---|---|
| 1 需求确认卡 | Step 2 拆解完成后 | 确认画幅 / VLM / 生成范围 |
| 3 镜头表卡 | Step 3 人工卡点（本节之内先于估价） | 确认逐镜方案 |
| 2 成本预估卡 | Step 4 生成提交前 | 确认预估花费 |
| 4 成片交付卡 | Step 5 合成完成后 | 无（交付收尾） |

- 卡片要写清「花多少钱、调什么模型、要确认什么」，不能只丢技术日志。
- 价格只报有据可查的价（视频用 `pt_video.py estimate` 真跑），未公开的标「按实际消费」，不编数字。
- 开始执行前先读 `references/cards.md`，按其中的字段清单渲染每张卡。

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
下载走 `media_backend.download_source()`：按 job 命名、统一 mp4、启动清残留、三态返回（OK / 明确失败 / STATUS_UNKNOWN）；状态未知时**不自动重下**，保留现场稍后再查。`out/JOB/manifest.json` 记录 yt-dlp 版本便于排错。

**两种并列入口**（都汇入 `source.mp4`，后续流程一致）：① 发链接 `--url`；② 上传本地视频 `--local <path>`。上传文件只作临时输入，分析完按「下载即删」删除、不缓存不分发。

**下载失败降级**：只有**明确失败**（平台拒绝/解析报错/格式不支持）才提示用户「换链接」或「自行下载后上传」——此时从 `config.json` 的 `download_helpers` 读取在线服务清单展示给用户（可随时增删替换，别硬编码域名）。`STATUS_UNKNOWN`（超时/断网/还在跑）**不提示重下/重生成/换服务**，保留 ID 稍后再查。详见 `references/cards.md` 的「输入方式与下载失败降级」。

### Step 2 · 拆解
```bash
python scripts/decompose.py --job JOB            # 离线取证模式（不花钱）
python scripts/decompose.py --job JOB --vlm      # 加视觉模型（重压缩源建议开；烧 chat token）
```
产物：`out/JOB/ref/breakdown.json`、`VIDEO.md`、`report.html`、`packaging/*.srt`。
提醒：平台下载的是重压缩源，官方建议开 `--vlm` 才准。
**拆解完成后 → 弹「卡片 1 需求确认卡」**，等用户确认画幅 / VLM / 生成范围（见 cards.md）。

**VLM 用 PT 自家的模型：**
- `config.json` 的 `vlm` 段默认 `base_url=https://api.powertokens.ai/v1`、`model=qwen3.8-flash`、`media=frames`。
- Key 只认环境变量 `POWERTOKENS_API_KEY`（由调用方注入；skill 不读任何其他 skill 的配置、不落盘、不打印）。
- 模型选择依据真机探测（2026-11-17）：`qwen3.8-flash` 能正确看图、最省钱；deepseek-v4-pro / seed-2-0-pro / MiniMax-M3 均 vision 可用但更贵；`glm-5.3-flash` 返回空内容，别用。要换模型改 `vlm.model`。
- **前提：PT Key 的 chat/token 额度充足。** 额度耗尽时 rea-video 会记 `VLM 部分生效/失败` 并自动回退到纯像素取证（不中断任务）——看到报告里 "VLM 部分生效" 且多数镜头标"待确认①/②"，就是额度问题，去 powertokens.ai 充值 token 额度后重跑即可。
- `--no-stems` 只跳过声音分离（~67MB 模型），与 VLM 无关，可同时使用省钱。

### Step 3 · 人工卡点（必须）
```bash
python scripts/recipe_to_prompt.py --job JOB
cat out/JOB/shots.json
```
**弹「卡片 3 镜头表卡」**（逐镜：镜头号 / 时长 / 描述 / route / 模型 / 单镜估价），**等用户确认**再生成。
如解析不全，`shots.json` 会带 `_source` 指向原始 `breakdown.json`，按 `references/prompt-mapping.md` 手工补齐。

### Step 4 · 批量生成
**提交前 → 弹「卡片 2 成本预估卡」**（模型清单 + 单价 + 预估总价，视频用 `pt_video.py estimate` 真跑，
标注"仅估算，以账单为准"），**等用户确认花费**再提交。
```bash
python scripts/generate.py --job JOB              # 提交 + 下载
python scripts/generate.py --job JOB --dry-run     # 只列计划，不提交、不花钱
python scripts/generate.py --job JOB --backend kit # 选媒体后端（当前 kit；可扩展其它实现）
```
逐镜头只调 `media_backend` 的 `generate()` / `download()` 两个接口（不直接拼 kit CLI）。产物 `out/JOB/clips/shot_001.mp4` …。
`out/JOB/manifest.json` 每镜记 `task_id / model / status`（原子写）：提交前写 `submitting`、拿到 ID 改 `submitted`、下载成功改 `downloaded`。
**三态**：下载没下来=只重下不重生成；PT 明确失败=唯一可重生成；状态未知（超时/断网/还在跑）=保留 ID 不重发、稍后再查。崩在提交中途=提交状态未知，挂起待人工确认，绝不重发。

### 可替换后端
`media_backend.py` 定义 `MediaBackend` 的两个接口 `generate()` / `download()`：
- 当前 `KitMediaBackend`（生成走 kit CLI、下载走 urllib、源视频走 yt-dlp）。
- 将来接其它媒体后端时，新增一个实现同名方法的 Backend 类即可，`generate.py` / `fetch.py` 主流程零改动。
- 音频同理：`audio_backend.py` 的 `LocalAudioBackend` → 未来 `KitAudioBackend` 等其它实现。

### Step 5 · 合成
```bash
python scripts/compose.py --job JOB --burn-subs
```
按原时间线拼接 + 烧字幕 + 归一化到 config 目标画幅，产物 `out/JOB/final.mp4`。
**合成完成 → 弹「卡片 4 成片交付卡」**（规格 / 音轨"全自生成" / 实际花费 vs 预估 / 产物清单）。

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

- `references/cards.md`：**四张交互卡的字段 / 时机 / 价格口径 / 门控（执行前先读）**。
- `references/workflow.md`：五步详细流程、产物清单、断点重跑、失败处理。
- `references/prompt-mapping.md`：breakdown.json 字段 → 生成参数映射、render_type 分流规则。
- `references/compliance.md`：版权红线、AI 标注、下载即删。
