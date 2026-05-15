# Phase 4.1 — Critic Loop (identity-driven retry) 进度日志

> 分支:`phase-4.1-critic-loop`(基于 main @ `b5143c0`)
> 设计依据:`design/architecture.md` §7 Phase 4 + Phase 3.2 实测反向发现
>
> 目标:把 Phase 1.2 的 `critic/identity.py` 接进 retry 链路 ——
>   identity score < threshold → 同 backend 换 seed → 升级 backend → 放弃。
>   每次评分落 SQLite,留作后续 RL reward。
>
> **不在本次范围**(留 Phase 4.2):
>   - scene / narrative / VSA 三维度
>   - 人工 gate(等 Web UI 升级)

## 进度索引

- [x] 1. `critic/store.py`:SQLite 评分记录
- [x] 2. `critic/policy.py`:`decide_next_action` 决策
- [x] 3. i2v backend 加 `seed` 参数
- [x] 4. video_node + critic_node + critic_router 改造
- [x] 5. Acceptance:单 shot 跑通 retry trigger
- [x] 6. 写日志 + commit + push

---

## Step 1 — `critic/store.py`

SQLite 评分历史,schema 设计为**多维度共用**(`dimension` 字段),将来加 scene/narrative/VSA 不改表。

```sql
CREATE TABLE critic_scores (
  id, thread_id, shot_id, dimension, score, threshold, passed,
  backend, retry_count, video_url, detail (JSON), timestamp
)
```

API:`record_score(...) -> id` + `get_scores(thread_id?, shot_id?, dimension?)`。

落 `./data/critic_scores.db`(同 assets store 的 DATA_DIR 约定)。

7 个 case 单元测试全过。

## Step 2 — `critic/policy.py`

`decide_next(score, threshold, retry_count, profile_*)` 返回 `CriticDecision(action, reason, next_seed?, next_backend?)`。

行为表(profile = short_drama,escalation=("seed", "backend", "human"),budget=2):

| retry_count | score | action |
|---:|---:|---|
| 0 | 0.95 | pass |
| 0 | 0.50 | retry_seed(new_seed=1042) |
| 1 | 0.50 | retry_backend(next=wanx-i2v-plus) |
| 2 | 0.50 | **fail**(budget 用尽) |
| 0 | None(no face) | retry_seed |

6 个 case 单元测试全过,包括"escalation 到 human"也正确 mark fail(UI 未接)。

## Step 3 — i2v backend 加 `seed`

`VideoProvider.generate(..., seed: int | None = None)` Protocol 扩展。
- `DashScopeI2V`(turbo):`parameters.seed = seed` 透传给 DashScope
- `WanxI2VPlusClient`(kf2v-plus):同上,透传 plus 端点
- `SeedDanceClient` / `JiMengClient`:签名加 seed,body 加 `body["seed"]`(等真接时确认 ark 是否消化)

`router.generate_video()` 也接 `seed` + `force_backend` 两个新参,后者把 critic 指定的 backend **插到 chain 最前**优先用。

返回类型改成 `(VideoResult, factory_name_used)` —— 告诉调用方真用了哪个 backend,落库时用。

## Step 4 — video_ppl 三节点改造

新增 / 修改字段:

| 容器 | 字段 | 用途 |
|---|---|---|
| `PipelineState` | +`thread_id: str` | critic 落库 |
| `ShotState` | +`pipeline_thread_id: str` | 同上(透传到子图) |
| `Shot` | +`retry_seed`, `retry_force_backend`, `last_backend_used` | critic 写 / video_node 读 / DB 落库用 |

**video_node**:
- 读 `shot.retry_seed` / `shot.retry_force_backend` → 传给 router
- router 返回 `(result, backend_used)` → 写 `shot.last_backend_used`
- **用完清掉 retry 提示**(防止下次错用)

**critic_node**(改造重点):
- dry_run / 无 video / 无 character embedding → 短路直 pass
- 下载视频(支持 `file://` 直接用,不再 download)→ `score_shot_identity` → ArcFace cos sim
- `record_score(...)` 落 DB(**无论 pass/fail 都记**)
- 调 `policy.decide_next(...)` → `pass/retry_seed/retry_backend/fail`
- 写 `shot.status` + (retry hints if retry)

**critic_router**(重写):
```
status=done                  → finalize_shot
status=failed + 有 retry 提示 → video_node (retry_count++, video_url=None)
status=failed + 无 retry 提示 → finalize_shot (mark failed)
```

清掉了之前硬编码的 `MAX_RETRIES = 2` —— budget 现在由 profile 决定,policy 算。

## Step 5 — Acceptance (零生成成本)

复用 Phase 3.2 acceptance 留下的 yan 视频(`/tmp/video1_phase32_acceptance/*.mp4`),`critic_node` 加 `file://` URL 直接读支持。

### 测试 A:RETRY → FAIL 路径(S2_door_keys,历史 identity 0.181)

```
attempt 1 (retry=0): identity 0.181 < 0.85 → retry_seed (new_seed=1042) → router → video_node
attempt 2 (retry=1): identity 0.181 < 0.85 → retry_backend (→wanx-i2v-plus) → router → video_node
attempt 3 (retry=2): identity 0.181 < 0.85 → fail (budget 2 exhausted) → router → finalize_shot
最终 status=failed
DB: 3 条记录
  retry=0 score=0.181 passed=False backend=wanx_i2v_plus
  retry=1 score=0.181 passed=False backend=wanx_i2v_plus
  retry=2 score=0.181 passed=False backend=wanx-i2v-plus  ← critic 切换后的 backend
```

✓ 决策按 escalation_policy 顺序触发(seed → backend → fail)
✓ DB 落每一次评分(3 条 = 1 initial + 2 retry)
✓ retry budget 严格执行
✓ critic_router 路由正确(2 次 → video_node,1 次 → finalize_shot)

### 测试 B:PASS 路径(S3_drinks_coffee,历史 0.504,临时降阈值到 0.3)

```
attempt 1 (retry=0): identity 0.504 >= 0.30 → pass → router → finalize_shot
最终 status=done
DB: 1 条记录(passed=True)
```

✓ 单次过线立刻 finalize
✓ DB 也记 pass(为 RL 用)

### 测试 C:dry_run / 无 embedding(单元测试覆盖)

- dry_run → status=done, notes="ok (dry-run)"
- char 无 embedding → status=done, notes="skipped"

## 关键工程结论

**Phase 4.1 把 Phase 3.2 的反向发现工程化了**:
- 当前所有 backend(turbo/plus)对**短剧 profile 的 0.85 阈值**都达不到 → 每个 shot 都会触发重试
- retry budget 2 = 总共 3 次尝试,够走完 seed → backend 两个策略
- 数据全落 `data/critic_scores.db`,**这张表就是后续 RL 的训练数据**(state=shot+backend,action=router 选的 backend,reward=identity score)

**短期实用价值**:
- 强运动 shot 在 wanx-plus 上会拖垮 identity(Phase 3.2 实测),critic 现在会自动重试甚至切到 turbo(turbo 单帧延拓 identity 更稳)
- 多 backend 接入后(SeedDance / Kling 真接),fallback chain 自动按 profile 优先级走

---

## 落地文件

```
critic/store.py            128 行  SQLite 评分历史 + CRUD
critic/policy.py           109 行  decide_next + CriticDecision
providers/base.py           +1 行   VideoProvider.generate 加 seed
providers/video.py         +20 行  4 个 backend 都接 seed
generation/router.py       +30 行  seed + force_backend 参数, 返回 factory_name
video_ppl.py              +130 行  PipelineState +thread_id; ShotState +pipeline_thread_id;
                                   Shot +retry_seed/force_backend/last_backend_used;
                                   critic_node 真打分+落库; critic_router 新规则
design/phase4.1-progress.md  本日志
```

## 留给 Phase 4.2 / 后续的 TODO

1. **scene 维度**:CLIP image-image 比对相邻 shot 背景的连续性(Phase 4.2)
2. **narrative 维度**:VLM caption + LLM judge(用 planner LLM)
3. **VSA 维度**:camera 关键词在视频特征里的命中(关键运镜词出现率)
4. **多维度综合决策**:目前只 identity 一维。需要 weighted scoring + dimension-specific 重试策略
5. **人工 gate**:Web UI 上展示"等审批"的 shot,用户点 pass/redo
6. **RL trainer**:基于 `critic_scores` 表训 router policy(state→backend,reward=identity_score)
7. **retry 路径下"真的换 backend"**:当前 short_drama profile 的 fallback[0]=wanx-i2v-plus,跟 preferred(kling3→映射为 wanx-i2v-plus)是同一个 factory。需要 profile 调整或 router 跳过同 factory
8. **critic_node 性能**:每个 shot 多 ~10s 评分(download + InsightFace)。100 shot 视频要 15+ 分钟纯评分。优化:并发抽帧 / GPU FaceID
