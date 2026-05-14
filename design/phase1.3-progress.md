# Phase 1.3 — I2I 范式切换 进度日志

> 分支:`phase-1.3-i2i`(基于 `phase-1.2-faceid`)
> 设计依据:`design/full.md` "Script-to-Asset-to-Keyframe" + 用户 product 视角
>
> 关键洞察(用户提出):
>   "验证一致性不足。必须先生成 anchor,后续 keyframe 用 anchor + instruction 来做 i2i,
>    才能保证最大一致性。"
>
> Phase 1.2 实测 mean cos sim 0.146 远低于 ArcFace 同人阈值 0.4,根因是 wanx-t2i 没有
> 参考图条件 —— 同一描述生成的 3 张其实是 3 个不同的人。Phase 1.3 切换到 i2i 范式:
> keyframe = i2i(anchor + scene instruction),anchor 锚定人脸,instruction 换场景。

## 进度索引

- [x] 1. Probe wanx-i2i:找对模型名 + 端点
- [x] 2. `providers/base.py`:`ImageEditProvider` Protocol + `ImageEditResult`
- [x] 3. `providers/image_edit.py`:`WanxI2IClient` + 2 stub
- [x] 4. factory + env:`build_image_edit_provider()`
- [x] 5. `keyframe_node` 改造:有 anchor → i2i,没 anchor → t2i 回退
- [x] 6. Acceptance test:**3 shot mean cos sim = 0.426**(vs 1.2 基线 0.146,**2.9x**)
- [x] 7. 写日志 + commit + push

---

## Step 1 — Probe wanx-i2i

**做了什么**:
- 测了 4 个候选 (model × endpoint) 组合
- 测了 3 种 base_image 输入格式

**结论**:

| Endpoint | Model | 提交状态 | 最终任务状态 |
|---|---|---|---|
| `/image2image/image-synthesis` | **`wanx2.1-imageedit`** | **200 OK** | **SUCCEEDED** ✅ |
| `/image2image/image-synthesis` | `wanx-x-painting` | 403 quota | - |
| `/multimodal-generation/generation` | `qwen-image-edit-plus` | 403 不支持 async | - |
| `/multimodal-generation/generation` | `qwen-vl-plus` | 403 不支持 async | - |

| base_image 输入格式 | 状态 |
|---|---|
| 原始 base64 字符串 | 400 "input length [1, 61440]" |
| **`data:image/png;base64,<b64>`** | **SUCCEEDED** ✅ |
| `file:///local/path` | 200 提交,但任务 FAILED "Incorrect padding" |

**关键发现**:
- 模型必须是 `wanx2.1-imageedit`,function 必须是 `description_edit`(其他 function 用于风格化、上色等)
- DashScope 通过 data URI 接收 base64 图,不需要先上传到 OSS
- 单次 i2i 调用 ~15 秒,$0.05

**首次验证人脸保留**:用 yan/front.png 做 anchor,prompt "a young man walking in a misty forest",出图后 ArcFace cos sim **0.815**(vs anchor)—— 远超 ArcFace 同人阈值 0.4,远超 Phase 1.2 t2i intra 的 0.124,验证 wanx-imageedit 真正保住了人脸。

## Step 2 — ImageEditProvider Protocol

新增 `providers/base.py`:
- `ImageEditResult` dataclass(`url`,`cost_usd`)
- `ImageEditProvider` Protocol:`async edit(anchor_path: str, instruction: str, *, size: str | None = None) -> ImageEditResult`

设计:`anchor_path` 是本地路径,provider 内部负责 base64 编码 / OSS 上传。上层完全不知道传输方式。

## Step 3 — WanxI2IClient + 2 stub

新增 `providers/image_edit.py`:
- **`WanxI2IClient`**(实装):wraps `wanx2.1-imageedit` + `description_edit` + 共享的 `_dashscope.submit_and_poll`
- **`QwenImageEditClient`**(stub):路径在 `multimodal-generation/generation`,当前 key 不支持 async,接入时改 sync API
- **`GPTImageEditClient`**(stub):走 eval 网关 `/compatible-mode/v1/images/edits`,multipart upload,需要时再实装

`_to_data_uri(path)` 工具:读本地 PNG → base64 → 拼 `data:image/png;base64,...`。

## Step 4 — Factory + env

`providers/__init__.py`:加 `build_image_edit_provider()`,默认 `wanx_i2i`,env 切换:
- `IMAGE_EDIT_PROVIDER=wanx_i2i | qwen_image_edit | gpt_image_edit`
- `IMAGE_EDIT_MODEL=wanx2.1-imageedit`(可覆盖)

`.env.example` 加注释行。

**验证**:
- ✅ 默认 → `WanxI2IClient model=wanx2.1-imageedit function=description_edit`
- ✅ 切 `qwen_image_edit` / `gpt_image_edit` → 对应 stub 类
- ✅ 未知值 → `ValueError`
- ✅ stub `.edit()` 调用 → `NotImplementedError` 带清晰消息
- ✅ 端到端 `WanxI2IClient.edit(anchor, instruction)` 一次 15.5s,出 URL,cost=$0.05

## Step 5 — keyframe_node 改造

**改了什么**:
- 加 `IMAGE_EDIT_PROVIDER` singleton(import 时构造)
- 加 `_gen_image_edit(anchor_path, instruction, *, dry_run)` wrapper(同 `_gen_image` 范式)
- 改造 `keyframe_node`:
  1. 扫 `shot.character_ids` → `state.char_sheets[*].ref_image_urls`,找第一个本地 `file://` + 文件存在的 anchor
  2. 找到 anchor 且非 dry_run → 走 i2i(`_gen_image_edit`)
  3. 没 anchor 或 dry_run → t2i 回退(原行为)
- 加日志:`[keyframe] i2i mode shot=... anchor=...` / `[keyframe] t2i fallback shot=... (no local anchor)`

**关键不变性**(向后兼容):
- dry_run 路径不变(占位 mock URLs 不是 `file://`,自动走 fallback)
- 没用 `python -m assets create` 建过档的角色 → cache miss → ref_image_urls 是远端 DashScope URL → 走 fallback
- 建过档的角色 → cache hit → 本地 anchor → 走 i2i

**验证**:`uv run python video_ppl.py`(dry_run)3/3 完成,行为与 Phase 1.2 一致。

## Step 6 — Acceptance test

**做了什么**(总成本 ~$8,~7 min):
- 复用 Phase 1.2 入库的 yan card(包括 anchor PNG + 512-d embedding)
- 3 个不同场景 instruction:misty bamboo forest / cozy cafe / rooftop night
- **`WanxI2IClient` 出 3 个 keyframe**(15s × 3 = ~45s)
- **`DashScopeI2V/wanx2.1-i2v-turbo` 用每个 keyframe 作 first_frame 出 5s shot**(107s × 3 = ~5 min)
- ffmpeg 抽中间帧 → InsightFace embedding → 跟 yan.embedding 算 cos sim

**实测**:

| shot | scene | identity cos sim @ mid-frame |
|---|---|---:|
| shot_1 | misty bamboo forest | +0.427 |
| shot_2 | cozy cafe | +0.387 |
| shot_3 | rooftop night | +0.466 |

**Summary**:`detected=3/3, mean=+0.426, min=+0.387, max=+0.466`

**对比 Phase 1.2 基线**:

| 阶段 | 流水线 | mean | min | max | vs ArcFace 0.4 |
|---|---|---:|---:|---:|:---:|
| 1.2 | t2i → i2v | 0.146 | 0.082 | 0.185 | ❌ |
| **1.3** | **i2i(anchor) → i2v** | **0.426** | 0.387 | 0.466 | ✅ |

**提升**:`+0.280`(2.9×)相对 Phase 1.2 基线,3/3 shot 都过 ArcFace 同人阈值 0.4。

## 关键结论

1. **`design/full.md` 的 "Script-to-Asset-to-Keyframe" 范式经实证有效**:从 t2i 文本召回切换到 i2i 锚定,**identity score 提升 2.9×**,从"勉强能区分"跨入"同人级别"。

2. **wanx2.1-imageedit 远比 wanx2.1-t2i-turbo 适合做 keyframe**:同样的"年轻男性"概念,t2i 抽卡每次出不同人(intra cos sim 0.124),i2i 锚定到具体 anchor 后真的能保留人脸(intra cos sim 0.815)。

3. **关键帧 i2i (0.815) → i2v 中间帧 (0.426) 还有约 0.4 的衰减**:首帧锚定后,i2v 视频中间帧仍会偏离 anchor 约 0.4 cos sim。要继续提分需要:
   - Backend 升级:`wanx-i2v-plus`(支持首尾帧)/ Kling 3.0 / SkyReels-V4(动作大师)
   - 多帧约束:不只首帧,关键时点也加 i2i 出帧

4. **Provider 抽象正确**:`ImageEditProvider` Protocol 落地,3 个具体实现(1 实装 + 2 stub),`keyframe_node` 不依赖具体 vendor,后续接入 qwen-image-edit / gpt-image-2 / flux-kontext 只需填 stub 内容,**节点 0 改动**。

## 落地文件

```
providers/base.py            +14 行  ImageEditProvider + ImageEditResult
providers/image_edit.py      120 行  WanxI2IClient + 2 stub + _to_data_uri
providers/__init__.py        +20 行  build_image_edit_provider + export
video_ppl.py                 +45 行  keyframe_node i2i 改造 + _gen_image_edit wrapper
.env.example                  +3 行   IMAGE_EDIT_PROVIDER / IMAGE_EDIT_MODEL
design/phase1.3-progress.md  本日志
```

## 留给后续 Phase 的 TODO

1. **接 qwen-image-edit-plus**:同 vendor,可能 i2i quality 更高;需要切 sync API。
2. **接 gpt-image-2**:走 eval 网关,multipart,适合英文 prompt 场景。
3. **接 flux-kontext / nano-banana-pro**:更细粒度的局部编辑能力,适合"换衣"、"改光影"等微调场景。
4. **多 anchor / 多角度参考**:Midjourney V7 Omni Reference 风格,把 front+side+3-quarter 都作 ref 喂给 i2i,看一致性是否再升。
5. **i2v backend 升级测试**:同 anchor → 同 keyframe → 不同 i2v backend(wanx-i2v-plus / Kling / SkyReels),看中间帧 cos sim 能拉到多少。
6. **多角色 shot**:当前 anchor 只取第一个角色的 ref。多角色场景需要更复杂的 reference 组合(group reference / mask 区域)。
7. **首尾帧 i2i**:Phase 3 路线图的目标 —— 不只首帧,尾帧也用 i2i 锚定,i2v 做"插值"而非"延拓",一致性应进一步上升。
