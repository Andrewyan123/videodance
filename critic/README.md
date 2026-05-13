# critic/ — 多维度评审

> Phase 4 (待实现)

## 职责

对每个生成的 shot 做结构化打分,触发重试或人工干预。打分输出**永久存档**,为后续 RL 训练 planner / router policy 提供 reward。

## 评分维度(由 Profile 选用子集)

| 维度 | 实现 | 短剧 | 动画 | 电影 | 广告 |
|---|---|:-:|:-:|:-:|:-:|
| identity | ArcFace 余弦比对 char_ref vs 生成视频抽帧 | ✓ | | ✓ | |
| scene | CLIP image-image 相邻 shot 背景 | ✓ | ✓ | ✓ | ✓ |
| narrative | VLM caption → LLM judge vs script | ✓ | ✓ | ✓ | ✓ |
| vsa | 关键运镜词命中(low_angle, dolly_in)| | | ✓ | |
| style | CLIP image-text 风格 prompt 匹配 | | ✓ | ✓ | |
| lipsync | 音频频谱 vs 嘴部 landmark 偏差 | ✓ | | ✓ | |
| product | 产品外观 embedding 余弦 | | | | ✓ |
| cta | OCR 检测 CTA 文字 / 时长 | | | | ✓ |

## 重试升级 (escalation policy)

```
shot fail
   ├─→ retry budget > 0?
   │     ├─→ try same backend, new seed   ──┐
   │     ├─→ try fallback backend          ──┼─→ shot 通过 / 进下一步
   │     └─→ escalate to human gate         ──┘
   └─→ 持久化失败原因, mark shot as failed
```

retry_budget 由 Profile 决定(电影/广告 5 次,短剧 2 次,动画 1 次)。

## 重要

打分结果落库到 `data/critic_scores.db`,字段:
`(thread_id, shot_id, dimension, score, threshold, passed, backend_used, retry_count, timestamp)`。这张表后续就是 RL reward dataset。
