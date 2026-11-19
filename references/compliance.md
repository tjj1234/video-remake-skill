# 合规红线

## rea-video 官方立场（原文要点）

> rea-video is for learning a video's structure, pacing and techniques, then remaking it with
> material you have the rights to. Don't reuse other people's footage, music, characters or likeness.
> Follow platform rules and local AI-content labeling requirements when you publish.

即：只学结构 / 节奏 / 技法，用你有权使用的素材重做。不要搬运别人的画面、音乐、角色、形象。

## 本 skill 的默认约束

1. **全 AI 重生成**：shots.json 里只有 `pt_generate` 类镜头进入生成；不复制原片画面。
2. **下载即删**：`keep_source=false`（默认）时，compose 完成后删除 `source.mp4`；
   原片仅用于拆解，不做二次分发。
3. **素材有权**：配乐（compose 的 --music）用你有授权的音乐或 PT TTS 生成；字幕是自己重写的文案，不直接搬原片字幕轨。
4. **发布标注**：按平台规则和中国《人工智能生成合成内容标识办法》标注 AI 生成内容；**该义务仅在国内发布时触发，不在国内发布不打水印**。
5. **默认无水印**：技术链路全程默认不烧水印——`generate.py` 拼给 kit 的命令不带 `--watermark`（kit 默认关、网关默认 false），合成环节也不叠加水印；提示词还写了「画面里不要出现文字和水印」。
6. **标识归属**：skill 不自动把 AI 标识烧进视频，留给发布方按地域处理（国内由平台自动加或发布方自行标注）。「技术无水印」**不免除**国内发布的 AI 标识义务。要主动开 PT 的 AI 水印需在生成命令加 `--watermark`（当前没有）。

## 平台条款

- YouTube / TikTok 的下载受其服务条款约束；yt-dlp 仅用于本地分析。
- 若目标是把原片换货/换文案做广告素材，确保你对该视频有商业使用授权。

## 一句话

学它的"为什么有效"（钩子、节奏、运镜、结构），画面全部重新生成，原片用完就删，发布标明 AI。
