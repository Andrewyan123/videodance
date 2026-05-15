# Phase 5.1 — Interactive 后端 (Variants + Stitch) 进度日志

> 分支:`phase-5.1-candidates-backend`(基于 main @ `ebb533f`)
>
> 把 pipeline 从"自动一把跑完"改成"用户参与每个决策":
> 1. **角色抽奖**:为每个 character 生成 N 张候选,用户挑
> 2. **视频抽奖**:为每个 shot 生成 K 段候选,用户挑
> 3. **拼接**:用户选完整路径 → 后端拼接选中的视频
>
> 范围:**纯后端**(SQLite + 新 API)。前端 wizard + 树状视图见 **Phase 5.2**。

## 进度索引

- [x] 1. `candidates/schema.py` + `candidates/store.py`:SQLite 候选表
- [x] 2. `candidates/builder.py`:propose_character / propose_shot 协程
- [x] 3. `server.py` 新端点:interactive 模式 + propose / select / tree / stitch
- [x] 4. Acceptance:Python script 跑完整流程
- [x] 5. 写日志 + commit + push

---

## Step 1 — 数据层(`candidates/`)

3 张 SQLite 表(`data/candidates.db`):

| 表 | 字段 | 角色 |
|---|---|---|
| `sessions` | thread_id (PK), user_prompt, target_duration_sec, profile_id, storyboard_json, status, final_video_url, total_cost_usd, timestamps | 一个 interactive run 的根 |
| `character_candidates` | id, thread_id, char_id, variant_index, description_used, seed, ref_image_url, embedding_json (512-d float list), consistency_score, selected, cost_usd, ts | 角色多候选,每个 propose 都续编号 |
| `shot_candidates` | id, thread_id, shot_id, variant_index, first_keyframe_url, last_keyframe_url, seed, backend_used, video_url, identity_score, selected, cost_usd, error, ts | shot 多候选,critic identity 已经一并存 |

`UNIQUE(thread_id, char_id, variant_index)` 防重复,select 是"清同组 selected=0,目标置 1"的原子操作。

8 个 case 单元测试全过(`session upsert/get / next_variant_index / list filter / select 切换 / embedding roundtrip` 等)。

## Step 2 — `candidates/builder.py`

两个核心协程:

### `propose_character_variants(thread_id, char_id, n, description, profile_id, emit?)`

- 复用 Phase 1.2 `assets.builder.build_character`,但用 `char_id__v{idx}` 作底层 ID
  避免文件互相覆盖(每个 variant 写到 `data/assets/char_yan__v0/`、`__v1/` ...)
- 入库后,**用户 select 时** `promote_character_to_canonical()` 把 selected variant 的数据
  作为 canonical `char_yan` upsert 到 `assets/store`,供下游 i2i 用
- 单 char 串行(避 t2i QPS 限流),不同 variant 通过 seed 触发不同 t2i 输出
- 估算 cost:每 variant ~$0.06(3 t2i × $0.02)

### `propose_shot_variants(thread_id, shot_id, shot, n, profile_id, emit?)`

- 从 `assets.store` 取 selected character 的 anchor 路径 + embedding
- 每个 variant 用 seed = `42 + variant_idx * 1000`,触发不同 i2i / i2v 输出
- 关键帧策略跟 profile.keyframe_strategy 走(single / first_last)
- 视频由 `generation.router.generate_video` 选 backend(含 fallback)
- critic identity score 一起算了存到 candidate 行
- 失败也存(`video_url=""`, `error=` 字段),便于 UI 提示用户

`emit` 是可选 callback,把 `propose_*_start / progress / done_variant / complete / error` 事件推到 SSE 队列,前端可订阅。

## Step 3 — server.py 新端点

| Endpoint | 行为 |
|---|---|
| `POST /api/runs` (含 `mode=interactive`) | 只跑 planner 出 storyboard,入 sessions 表,返回 thread_id + storyboard JSON |
| `GET /api/runs/{tid}/storyboard` | 直接返 storyboard dict |
| `POST /api/runs/{tid}/characters/{cid}/propose?n=4` | 调 propose_character_variants,sync 等结果返新候选列表 |
| `POST /api/runs/{tid}/characters/{cid}/select?variant=K` | DB 置 selected=1,promote 到 assets/store |
| `POST /api/runs/{tid}/shots/{sid}/propose?n=3` | 调 propose_shot_variants,sync 等结果(~10 min) |
| `POST /api/runs/{tid}/shots/{sid}/select?variant=K` | DB 置 selected |
| `GET /api/runs/{tid}/tree` | 整个 run 的 storyboard + 每个 char/shot 的 candidates 列表 + selection 状态 |
| `POST /api/runs/{tid}/stitch` | 按 shot.index 顺序拉 selected video URLs,下载,ffmpeg concat(失败回退 re-encode),写 final mp4 |

向后兼容:`mode=auto`(默认)走原全自动 pipeline,SSE 推进度。**前端旧代码不破**。

dry_run:interactive 模式下 dry_run 用 mock storyboard,避免 LLM 调用。

## Step 4 — Acceptance(end-to-end)

`candidates/acceptance_test.py` 用 httpx 串完整流程,数据可信:

| 步骤 | 耗时 | 成本 |
|---|---:|---:|
| POST /api/runs interactive(真 LLM) | 9.3s | <$0.01 |
| 2 char variants(真 t2i) | 58.8s | ~$0.12 |
| Select char + promote | <1s | 0 |
| **2 shot variants(真 i2i + wanx-i2v-plus,串行)** | **639.3s (~10.6 min)** | **~$6** |
| Select shot | <1s | 0 |
| GET /tree | <1s | 0 |
| **POST /stitch(ffmpeg concat)** | **2.5s** | **0** |
| **总计** | **~12 min** | **~$6.12** |

### 输出验证

- 2 个 character variants:`char_yan__v0/v1`,front.png 各落盘
- 2 个 shot variants:identity score = 0.205 / 0.213(wanx-i2v-plus 强运动 shot,跟 Phase 3.2 测的 0.181 同量级)
- Tree 状态:`char_yan: 2 candidates, 1 selected / S01-001: 2 candidates, 1 selected`
- 最终 mp4:`/tmp/video1.0_stitch/final_interactive_db3b3470.mp4`,**7.9 MB**(单 shot 5s 视频,质量 OK)

**ALL PASS** ✓

## 关键设计决策

1. **storyboard 落 JSON 字符串到 sessions 表,不展开成关系字段** —— schema 演进零代价,跟 assets.store 的 payload 字段一脉相承
2. **embedding 用 JSON string 存(不是 BLOB)** —— 调试方便,体积可接受(512-d float ≈ 5-6 KB)
3. **character variants 用 `__v` 后缀走 asset_builder** —— 复用 Phase 1.2 的所有逻辑(FaceID 抽取、cross-view 校验、文件管理),不重写一遍
4. **promote_character_to_canonical** —— 用户 select 后才把 canonical char_id 写入 asset_store,**下游 Phase 1.3 i2i / Phase 3.x router 完全不知道用户在挑选**,接口零侵入
5. **propose 同步**(返回结果)+ **SSE 异步**(推进度) —— 简单 + 灵活,前端可以选择阻塞等或者订阅事件流

## 落地文件

```
candidates/__init__.py          11 行  模块说明
candidates/schema.py            76 行  Session / CharacterCandidate / ShotCandidate Pydantic
candidates/store.py            226 行  3 表 SQLite CRUD + select 切换
candidates/builder.py          258 行  propose_character / propose_shot + promote
candidates/acceptance_test.py  120 行  端到端 httpx 测试脚本
server.py                     +220 行  interactive 模式分支 + 6 个新端点
design/phase5.1-progress.md   本日志
```

## 留给 Phase 5.2 的 TODO

1. **前端 wizard** —— 4 步流程:Input → Character lottery grid → Shot tree → Stitch+preview
2. **CSS grid 树状视图** —— 横向列(shot)× 纵向候选,点选 highlight,空白格"再抽 N 个"按钮
3. **进度条** —— shot propose ~10 min,前端要订阅 SSE 显示当前到哪个 variant 哪个 stage(t2i/i2v/score)
4. **多角色 UI** —— 如果 storyboard 有 2+ characters,wizard 每个 char 一行 grid
5. **失败 variant 可视化** —— ShotCandidate 已经存 error,UI 显示一个红框"重新抽奖"
6. **identity score 排序提示** —— shot variants 按 score 倒排,最高 highlight,用户可以参考
