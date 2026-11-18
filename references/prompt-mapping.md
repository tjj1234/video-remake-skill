# 配方 -> 生成参数映射

## breakdown.json 字段 -> shots.json 字段

`recipe_to_prompt.py` 用多候选键名探测（不同 rea-video 版本字段名会变）：

| shots.json | 探测的源字段 | 说明 |
|---|---|---|
| duration | duration / dur / length，或 end-start | 时长秒数，缺省 5 |
| prompt | prompt / description / caption / visual / text | 画面描述；dict 时取 en/zh |
| render_type | render_type / render / type / kind / source | 决定 route |
| ref | ref / first_frame / reference / image | 图生视频首帧 |

解析不到的原始镜头对象完整保留在每镜的 `_raw`，`shots.json` 顶部 `_source` 指回 breakdown.json。

## route 分流（重要）

| route | 含义 | 怎么处理 |
|---|---|---|
| `pt_generate` | AI 生成镜头 | generate.py 调 pt_video.py 出片 |
| `code_remake` | 代码/模板渲染/录屏 | 不走 PT，用 Remotion / 前端重建（本 skill 不含，交给 agent） |
| `reshoot` | 实拍镜头 | 用有权素材或自己拍，本 skill 不含 |
| `skip` | 未识别 | 人工判断 |

## 人工复核要点

1. **prompt 质量**：自动抽的描述往往是中文短句；生成模型多用英文 prompt 更好，可让 agent 改写后填回。
2. **时长档位**：pt_video.py 文档提示 Seedance 最低 4s，2s 会被拒；不确定就用 5。
3. **比例**：默认 9:16；文生视频若模型不支持 9:16（Seedance 支持 16:9/4:3/1:1/adaptive），改用 720p + compose 阶段 pad 成 9:16。
4. **字幕不进 prompt**：rea-video 明确字幕/花字不写进生成提示词，应在 compose 阶段烧录。
5. **首帧**：图生视频镜头先确认 ref 路径有效（本地图 / URL / asset://）。

## 模型选择

不硬编码模型 ID。用 `pt_video.py models --json`（或 ask agent 列模型）看当前 Key 真正可用的模型，
再填 shots.json 的 model 字段。限定 wan3.0 的 Key 对其他模型会报 no permission。
