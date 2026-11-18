# 工作流参考

## 目录约定

```
out/<job_id>/
├── _preflight.json          预检报告
├── meta.json                来源信息
├── source.mp4               下载的原片（cleanup 时会删）
├── source*.vtt              字幕
├── ref/                     rea-video 拆解产物
│   ├── breakdown.json       结构化拆解（recipe_to_prompt 的输入）
│   ├── VIDEO.md             给模型读的分层配方
│   ├── report.html          人看的报告
│   ├── packaging/*.srt      成片字幕
│   └── video/               逐镜首尾帧
├── shots.json               生成参数表（人工卡点在这里）
├── clips/shot_001.mp4 ...   每镜生成结果
├── _generate_results.json   生成汇总
└── final.mp4                成片
```

## 断点重跑

每步完成都会在 `state.json` 写 `done`，重跑时自动跳过已完成的步骤。
强制重某步：删掉 `state.json` 里对应条目，或直接单跑该脚本。
pipeline 从某步继续：`python scripts/pipeline.py --job JOB --resume-from <step>`。

## 各步失败处理

| 步骤 | 常见失败 | 处理 |
|---|---|---|
| preflight | 缺 ffmpeg/yt-dlp/rea-video | 按 `install_hints` 装；这只是检测，不改环境 |
| fetch | 平台反爬 / 签名 URL | 让用户手动下载后 `--local`；或换 yt-dlp cookies |
| decompose | VLM 未配 / OCR 依赖冲突 | 不开 `--vlm` 用离线模式；OCR 冲突见 rea-video 文档的 opencv 处理 |
| recipe | breakdown 字段解析不全 | 编辑 shots.json 手工补齐，见 prompt-mapping.md |
| generate | 模型 no permission / 超时 | models --json 查可用模型；换 -m；失败镜头用 --only 单跑 |
| compose | 字幕路径 / 尺寸不一致 | compose 已做归一化；字幕找不到会 warn 但不中断 |

## VLM 说明

平台下载的是重压缩视频，rea-video 官方明确：原始文件上 tools-only 离线就准，
重压缩源要加 VLM（Gemini / Doubao 等 OpenAI 兼容多模态）才准。
配法：`config.json` 的 `vlm.base_url` / `vlm.model`，环境变量放 Key（`api_key_env` 指定名字），
然后 `decompose.py --vlm`。VLM 全挂时 rea-video 不中断，报告会标注"等同未开"。

## 合规

见 `compliance.md`。默认：`keep_source=false` 时合成后删原片；只学结构，全 AI 重生成。
