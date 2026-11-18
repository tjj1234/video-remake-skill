# video-remake skill

把一条 YouTube / TikTok 链接，拆解后用 PowerTokens 模型重做成一条全新 9:16 短视频。
这是给 dsh 底座 agent 用的编排 skill：串起 rea-video（拆解）+ powertokens-kit 的 pt_video.py（生成）+ yt-dlp + ffmpeg。

## 装进 dsh

dsh 扫描这几个 skill 目录（项目级优先）：`<项目>/.dsh/skills`、`<项目>/.agents/skills`、`~/.dsh/skills`、`~/.agents/skills`。
只认一层目录（`skills/video-remake/SKILL.md` 可以，不能再套子目录）。

```bash
# 用户级全局（推荐）
cp -r video-remake ~/.dsh/skills/          # 或 ~/.agents/skills/
# 项目级
cp -r video-remake <项目>/.dsh/skills/
```

新增 skill **不用重启 dsh**，下一轮对话生效。skill ≠ plugin，不走 `dsh plugin add`。

## 配置

```bash
cp config.example.json config.json
```

Key 不写在这里，由 pt_video.py 自己管理（`pt_video.py config --set-key`）。
如果 pt_video.py 不在默认候选路径，在 config.json 的 `pt_video_script` 填绝对路径。

## 依赖（已支持隔离自动安装）

需要 Python 3.10+ / ffmpeg / yt-dlp / rea-video / PT Key。
本机实测（2026-11-17）：三类依赖已全部隔离安装完成，`preflight --job <任意>` 结论「全部就绪」：
- yt-dlp 2026.10.26 → `.venv/`
- rea-video 0.1.0（CLI `decompose` 可用）→ `tools/rea-video` + `.venv/`
- ffmpeg 9.0.2-essentials → `tools/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe`
- PT Key 尾号 xo4E，默认模型 wan3.0-video

**网络不稳时的兜底脚本**（本沙箱实测：git clone 与 urllib 单次下载都会中途断连）：
- `scripts/_fetch_rea_tarball.py`：用 GitHub codeload tarball + 5 次重试下载 rea-video 源码，替代易断的 git clone。
- `scripts/_fetch_ffmpeg.py`：ffmpeg 断点续传 + zip 完整性校验 + 8 次重试，替代单次 urlretrieve。
两者产出与 `install.py --apply` 相同；若 `--apply` 因网络失败，先跑对应兜底脚本再重跑 `--apply`（检测到源码/zip 已就位会跳过下载直接安装）。

`install.py` 把这三类装进 **skill 私有目录**（`.venv/` + `tools/`），不碰系统 Python / PATH / 运行中的服务，
卸载即删目录。**逐类授权、绝不静默安装：**

```bash
python scripts/install.py --plan          # 1. 列清单（体积/落点/权限），不装
# 2. 用户确认某类后，agent 逐个执行：
python scripts/install.py --authorize yt_dlp   # 记录授权
python scripts/install.py --apply yt_dlp       # 才真正装
# 依次处理 ffmpeg / rea_video
python scripts/install.py --authorize model_67mb && python scripts/install.py --apply model_67mb  # 67MB 模型单独确认
python scripts/install.py --status         # 查看状态
python scripts/install.py --uninstall      # 预览卸载（加 --yes 才真删）
```

- `yt_dlp` / `rea_video`：装进 `.venv/`（rea-video 用 git clone 到 `tools/rea-video` 再 `pip install -e`，并自动修复 opencv 冲突）。
- `ffmpeg`：从 gyan.dev 下预编译 zip 解压到 `tools/`（Windows 自动化；mac/linux 给 brew/apt 提示）。可在 config 用 `ffmpegSource` 覆盖下载源。
- `model_67mb`：`--apply` 只建隔离目录，真正下载在首次 decompose 时由 rea-video 完成，落在 `tools/models`。**可跳过**：首次验证加 `--no-stems` 就不触发这 67MB 下载（声音分离仅用于音频层分析，不影响画面镜头拆解）。
- 未授权 `--apply` 直接拒绝（exit 3）；安装失败不标记已装，可重试。

脚本通过 `_common.resolve_tool()` 优先查私有目录、回退系统 PATH，所以装完免配环境变量。

## 在 dsh agent 里调用

装好后自然语言即可（description 会命中）：

> 帮我把这条视频 remake 一下：https://www.youtube.com/shorts/xxxxx
> 拆解这条 TikTok 然后用 PT 重做：https://www.tiktok.com/...

agent 会按 SKILL.md 走：预检 → 下载 → 拆解 → **把 shots.json 镜头表给你确认** → 批量生成 → 合成。

手动逐步跑：
```bash
python scripts/preflight.py --job demo
python scripts/fetch.py --job demo --url "https://..."
python scripts/decompose.py --job demo --vlm
python scripts/recipe_to_prompt.py --job demo
# 看/改 out/demo/shots.json
python scripts/generate.py --job demo
python scripts/compose.py --job demo --burn-subs
```
一键：`python scripts/pipeline.py --job demo --url "..." --vlm`（到镜头表停下），确认后
`python scripts/pipeline.py --job demo --resume-from generate`。

## 和"调 kit"的关系

generate.py 就是直接调你已经接好的 `pt_video.py generate`——跟你在基座 agent 里调 kit 出视频是同一条通道。
区别只是这里由 skill 按拆解出的镜头表**批量、按时间线**地调它。换模型改 shots.json 的 model，或改 config 的 default_model。

## 未实测（重要）

- 未 clone rea-video、未装 yt-dlp/ffmpeg 实跑；拆解质量与下载成功率待阶段 A 验证。
- breakdown.json 字段随 rea-video 版本变化，`recipe_to_prompt.py` 做了多候选探测 + 原始字段保留，但**镜头表必须人工确认**。
- dsh 沙箱能否外联 YT/TT、平台反爬，需实测；失败时用 `--local` 手动放视频绕过下载。
- VLM 需自己配 OpenAI 兼容多模态端点的 base_url/model/Key。
