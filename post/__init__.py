"""后期: 拼接 / 转场 / 字幕 / 叠加.

Phase 6 (待实现, 当前 video_ppl.py 内的 stitch_node 是基础版):
  - stitch.py:   ffmpeg concat + RIFE 转场平滑
  - master.py:   字幕烧录 + 音频混入 + 输出 H.264 master
  - overlay.py:  广告片 logo / CTA 字幕叠加 (按 Profile.overlay_renderer 选实现)
"""
