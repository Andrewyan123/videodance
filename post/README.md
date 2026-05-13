# post/ — 后期

> Phase 6 (待实现,当前 stitch 在 `video_ppl.py:stitch_node`)

## 职责

把所有 shot 视频 + 音频 + 字幕 + 叠加 → 最终成片 mp4。

## 计划文件

| 文件 | 职责 |
|---|---|
| `stitch.py` | ffmpeg concat (现 stitch_node 移植过来),按 Profile 选 `concat / rife / crossfade` |
| `master.py` | 字幕烧录 + 音频 mux + 输出最终 H.264 mp4 |
| `overlay.py` | 广告片专用 logo / CTA 字幕渲染 |

## Profile 行为

| Profile | transition_smoother | overlay_renderer | subtitle_burner |
|---|---|---|---|
| short_drama | concat | None | True |
| anime | concat | None | False |
| cinema | rife | None | False (用单独 .srt) |
| commercial | concat | logo_cta | True |
