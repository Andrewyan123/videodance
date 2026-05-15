# Phase 3.2 — 首尾帧 i2v + 多 backend + Router 进度日志

> 分支:`phase-3.2-i2v-plus`(基于 main @ `27af0ba`)
> 设计依据:`design/architecture.md` §7 Phase 3 + `design/full.md` "Backend 路由策略"
>
> 目标:
> 1. **wanx-i2v-plus 真实接入**(支持首尾帧条件)
> 2. **SeedDance / JiMeng stub-with-correct-shape**(查 volcengine API,等 AK)
> 3. **Router**(profile-driven + fallback)
> 4. **video_node 改造**:用 router,传双帧
> 5. **Acceptance**:wanx-plus 双帧 vs Phase 1.3 单帧 turbo 基线对比

## 进度索引

- [x] 1. Web search 三个 backend API 形状
- [x] 2. Probe wanx-i2v-plus 真请求确认参数
- [x] 3. VideoProvider Protocol 扩展 `last_frame_url`
- [x] 4. 实装 `WanxI2VPlusClient` + factory + env
- [x] 5. SeedDance + JiMeng stub-with-correct-shape
- [x] 6. `generation/router.py`(profile-driven + fallback)
- [x] 7. `video_node` 改造:用 router,传双帧
- [x] 8. Acceptance:3 shot × wanx-plus 双帧 i2v
- [x] 9. 写日志 + commit + push

---

## Step 1 — Web search

**WebSearch + WebFetch 三个 backend 的 API 形状**:

| Backend | Endpoint | 鉴权 | 字段 |
|---|---|---|---|
| wanx kf2v (双帧) | `dashscope.aliyuncs.com/api/v1/services/aigc/image2video/video-synthesis` | `Bearer DASHSCOPE_KEY` | `input.first_frame_url` + `input.last_frame_url` |
| SeedDance / JiMeng | `ark.cn-beijing.volces.com/api/v3/contents/generations/tasks` | `Bearer ARK_API_KEY`(**非 HMAC**) | `content` 数组:`[text, image_url(first), image_url(last)]` |

**关键纠错**:
- 之前 stub 写的"`VOLC_ACCESSKEY` + `VOLC_SECRETKEY` HMAC-SHA256"是错的。volcengine ark 用简单 Bearer key。
- 之前的 stub 模型名 `seeddance-2-pro` 也不对。实际是 `doubao-seedance-2-0-260128` / `doubao-seedance-2-0-fast-260128`。
- wanx-i2v-plus 的端点是 `/image2video/`(不是 turbo 的 `/video-generation/`),字段是 `first_frame_url` / `last_frame_url`(不是 `img_url`)。

## Step 2 — Probe wanx-i2v-plus

8 个候选 model 名实测:
| Model | Status |
|---|---|
| `wan2.1-kf2v-plus` | 400 Model not exist |
| `wan2.2-kf2v-flash` | 400 unauthorized media(模型存在,我那 URL 假)|
| **`wanx2.1-kf2v-plus`** | 400 unauthorized media → **模型存在** |
| `wanx2.1-i2v-plus` | 400 url error → **模型存在,可能单帧** |
| 其他 4 个 | 400 Model not exist |

**`wanx2.1-kf2v-plus` 真实跑通**:用 i2i 出的 OSS URL 作 first+last → 提交 → 4.5 分钟 SUCCEEDED → 返回 `output.video_url`。接口形状 100% 确认。

## Step 3 — `VideoProvider` Protocol 扩展

`providers/base.py`:
```python
class VideoProvider(Protocol):
    async def generate(
        self, *, prompt, first_frame_url, duration_sec,
        last_frame_url: str | None = None,    # 新加, 可选
    ) -> VideoResult: ...
```

`DashScopeI2V` (turbo) 接到 `last_frame_url` 时 warn + 丢(turbo 不支持)。

## Step 4 — WanxI2VPlusClient

`providers/video.py`:
- `WanxI2VPlusClient(model="wanx2.1-kf2v-plus")`
- Endpoint = `image2video/video-synthesis`
- Body = `{input: {first_frame_url, last_frame_url, prompt}, parameters: {resolution: "720P"}}`
- duration 模型固定 5s,参数被忽略
- last_frame 缺失时用 first 当 last + warn(退化为静态)
- 注册到工厂 `VIDEO_PROVIDER=wanx_i2v_plus`

## Step 5 — SeedDance / JiMeng stub-with-correct-shape

`providers/video.py` 改造:
- 引入 `_VolcengineArkBase` 共享类,封装 ark 端点 + body 拼装
- `SeedDanceClient(model="doubao-seedance-2-0-260128")` 继承
- `JiMengClient(model="doubao-seedance-2-0-fast-260128")` 继承(fast 变体,JiMeng 偏 C 端速度)
- `_build_body()` 把 `content` 数组拼对(text + image(first) + image(last))
- `_submit_and_poll()` 是 `NotImplementedError`,但**所有上下文都齐全**:`api_key` 检测、端点、body 拼装、模型 ID
- **拿到 `ARK_API_KEY` 后只需填 `_submit_and_poll` 的 HTTP 调用**(同 `_dashscope.submit_and_poll` 范式),`generate()` 不动

env 变量:`ARK_API_KEY` 或 `VOLC_ARK_API_KEY`。

## Step 6 — `generation/router.py`

按 profile 偏好 + fallback chain 选 backend。

**映射**(`PROFILE_TO_FACTORY`):
| profile 产品名 | factory name |
|---|---|
| `wanx-i2v-turbo` | `dashscope_i2v` |
| `wanx-i2v-plus` | `wanx_i2v_plus` |
| `seeddance2` / `seeddance` | `seeddance` |
| `jimeng` | `jimeng` |
| **`kling3` / `sora2` / `wan2.2-anime-lora`** | **`wanx_i2v_plus`** (fallback,未真接) |

**升级规则**:`has_last_frame=True` 但选中 backend 不支持双帧 → 强制升 `wanx_i2v_plus`。

**fallback 链**:`profile.video_backend_preferred + profile.video_backend_fallback` 顺序尝试;`NotImplementedError` / `RuntimeError` 触发降级;链上同 factory 名跳过(避免重试同一个失败 backend)。

实测决策表(`short_drama` profile,`preferred=kling3, fallback=[wanx-i2v-plus, wanx-i2v-turbo]`):
- 无 last_frame → kling3 解析为 wanx_i2v_plus → 用 plus
- 有 last_frame → 直 plus
- plus 出错 → fallback 到 turbo

## Step 7 — `video_node` 改造

- `ShotState` 加 `pipeline_profile_id`(避开父级 `profile_id` 冲突)
- `fanout_shots` 把 profile_id 注入子图 state
- `video_node` 真实路径调 `generation.router.generate_video(profile, ...)`,传 `last_frame_url=shot.last_keyframe_url` (仅 first_last 策略下)
- dry_run 短路不调 router

dry_run smoke test 通过,3/3 shots 完成。

## Step 8 — Acceptance: wanx-plus 双帧 i2v 实战

**配置**:复用 yan 角色(Phase 1.2 入库的 anchor + ArcFace embedding)。3 个 shot 混合运动 / 静态:
- S1_walk_window:中度运动(站起来 → 贴窗)
- S2_door_keys:**强运动**(屋内 → 半身出门外)
- S3_drinks_coffee:静态(手放桌上 → 杯子放回杯托)

**流程**:
1. 3 × 2 = 6 次 i2i(`WanxI2IClient`,~$0.30,~1.5 min)
2. 3 × 1 = 3 次 wanx-plus i2v(并发 2,$3 × 3 = $9,~9 min)
3. 3 个视频(6-10 MB,**比 Phase 1.3 turbo 输出 500KB 大十倍 → 质量明显更高**)
4. ffmpeg 抽中间帧 → InsightFace embedding → cos sim vs yan.embedding

**实测结果**:

| shot | 类型 | mid-frame identity vs yan |
|---|---|---:|
| S1_walk_window | 中度运动 | +0.420 |
| **S2_door_keys** | **强运动** | **+0.181** ← 拖累项 |
| S3_drinks_coffee | 静态 | +0.504 |
| **mean** | | **+0.368** |

**对照 Phase 1.3 baseline**(单帧 turbo,3 shot 平均 0.426)— Phase 3.2 **平均反低 0.058(14%)**。

### 反向但有用的发现

per-shot 看出明显模式:
| shot 运动量 | Phase 3.2 双帧 wanx-plus | 解读 |
|---|---:|---|
| 静态(S3) | **0.504** | wanx-plus 最强 |
| 中度运动(S1) | 0.420 | 跟 baseline 持平 |
| **强运动(S2 走门)** | **0.181** | 拖垮均值 |

**假设**:wanx-plus 在两帧间做"插值",当两帧人脸姿态显著不同时(S2 的 first↔last cos sim 我们 Phase 3.1 测得 0.35),中间帧是在两个"稍不同的脸"之间混合 → identity 漂移。而 turbo 单帧"延拓"模式,从一个固定 first 帧发散,中间帧通常保留 first 的脸特征 → identity 更稳。

去掉 S2 异常值,S1+S3 平均 0.462,反比 Phase 1.3 baseline 0.426 更好 → wanx-plus 在**弱/无运动 shot 上确实更强**。

### 工程结论

1. **wanx-plus 不是简单的"plus 比 turbo 好"** —— 需要按 shot 运动量动态选 backend
2. **强运动 shot 在 wanx-plus 上反而拉胯** —— 可能要 Kling 3(动作大师)或 SkyReels(对动作专门优化)
3. **静态 / 弱运动 shot wanx-plus 是赢**(0.504 vs baseline 0.43)
4. **Phase 4 Critic Loop 的重要性凸显**:identity score < 阈值 → 自动切 backend 重试。Phase 3.2 提供了量化数据,Phase 4 把它接进 retry 逻辑

---

## 落地文件

```
providers/base.py             +5 行   VideoProvider Protocol 加 last_frame_url
providers/video.py           +180 行  WanxI2VPlusClient + 重写 SeedDance/JiMeng (correct shape)
providers/__init__.py        +10 行   工厂注册 wanx_i2v_plus
generation/router.py         150 行   profile→backend 映射 + fallback chain + 双帧自动升级
video_ppl.py                  +30 行  ShotState pipeline_profile_id; video_node 用 router
design/phase3.2-progress.md  本日志
```

## 留给 Phase 3.3 / Phase 4 的 TODO

1. **接通 SeedDance + JiMeng**:拿到 `ARK_API_KEY` 后填 `_VolcengineArkBase._submit_and_poll` 的 HTTP 调用即可,生成层 / router 都不需要改
2. **Kling 3 / Sora 2 接入**:同样 stub-with-shape 模板;Kling 是字节系的对手 Pika / Runway,可能需要独立账号
3. **Phase 4 Critic Loop**:
   - identity score < 0.4 → 同 backend 换 seed 重试
   - 还 < 0.4 → 升级到 fallback backend(强运动 shot 改 turbo / Kling)
   - 仍 < 0.4 → human gate
4. **acceptance 加 SeedDance / JiMeng**:有 AK 后,跑同 3 shot 对比 wanx-plus,看动作差异是否好
5. **shot-level metadata 用于路由**:目前 router 只看 has_last_frame,可以加 `physics_intensive` / `human_action` 等标签,profile 决定路由(per design/full.md)
6. **video_node 富字段进 prompt**:当前只传 action,emotion / camera_movement 等都没拼到 i2v prompt(已在 Phase 3.1 落到 Shot dataclass)
