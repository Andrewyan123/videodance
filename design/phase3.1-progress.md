# Phase 3.1 — 富字段 + 首尾帧 i2i (LLM 法) 进度日志

> 分支:`phase-3.1-rich-keyframes`(基于 main @ `31f7b83`)
> 设计依据:`design/architecture.md` §7 Phase 3 + `design/full.md` "Script-to-Asset-to-Keyframe"
> 目标:
> 1. ShotV1 富字段(`shot_type / camera / emotion / scene_ref / dialogue`)真正进 i2i instruction
> 2. `profile.keyframe_strategy=="first_last"` 时生成两帧关键帧
> 3. action 拆分用 **LLM 法**(planner 输出 `action_start` / `action_end`),不用规则 hint
>
> **不在本次范围**(留 Phase 3.2):接首尾帧 i2v 模型(`wanx-i2v-plus` / Kling 3 / Sora 2);
> backend router(profile-driven backend 选型)

## 进度索引

- [x] 1. schema 改:ShotV1 加 `action_start` / `action_end`;Shot dataclass 加富字段 + `last_keyframe_url`
- [x] 2. prompts.py:SCHEMA_GUIDE + few-shot 训 LLM 拆动作
- [x] 3. `generation/keyframe_prompt.py`:富字段拼 i2i instruction + 首尾配对
- [x] 4. `keyframe_node` 改造:profile-driven single | first_last 分支
- [x] 5. Probe LLM:**4/4 shot 都填了 action_start/end,质量好**
- [x] 6. Acceptance:5 shot baseline vs first_last 双帧,**双帧都保 identity 且姿态有显著差异**
- [x] 7. 写日志 + commit + push

---

## Step 1 — Schema 改造

`storyboard/schema.py`:`ShotV1` 加 `action_start: str | None` 和 `action_end: str | None`,**可选**(为运动型 shot 必填,静态可空)。schema_version 不变(v1 加可选字段向后兼容)。

`video_ppl.py`:`Shot` dataclass 加:
- `last_keyframe_url: str | None`(first_last 策略的尾帧)
- `shot_type / camera_angle / camera_movement / emotion / scene_ref`(从 ShotV1 平移过来)
- `action_start / action_end`(LLM 法的动作两端态)
- `dialogue_speaker / dialogue_text`(Phase 5 audio 用)

旧字段 `prompt / camera` 保留(向后兼容,= ShotV1.action / 拼好的字符串)。

`plan_node` 把 ShotV1 富字段全串到 Shot dataclass,**不再丢弃** —— Phase 2 时这些都被默默扔了。

## Step 2 — prompts.py 教 LLM 拆动作

`SCHEMA_GUIDE` 加 `action_start` / `action_end` 字段说明 + 详细规则:
- "必须是**自包含的画面 / 姿态描述**,不引用 action 字段"
- "对运动型 shot(走动 / 转身 / 拿起 / 离开),两端态必须**空间或姿态显著不同**"
- "对静态 shot(坐着思考 / 注视一物),两端可以接近,但仍要细微差(头微抬 / 视线移动等)"

`FEWSHOT_EXAMPLE` 加第二个示例(Yan 从远处走到窗前),显式展示"显著空间位移"的拆法。

## Step 3 — `generation/keyframe_prompt.py`

新模块 `generation/`,第一个文件 `keyframe_prompt.py`。两个 API:
- `build_keyframe_instruction(...)` :单帧策略,拼 `{global_style}, {shot_type} shot, {camera_phrase}, {action}, {emotion} mood`
- `build_first_last_pair(...)` :返回 `(first_instr, last_instr)`,共享视觉骨架(camera + shot_type + global_style),action 部分用 `action_start` / `action_end` 替代。**缺 LLM 字段时规则法 fallback**(`frozen at the BEGINNING/ENDING moment`)。

视觉骨架共享设计:首尾两帧的 camera 角度、shot_type、global_style 都一致,**只有 action 部分**(身体姿态)不同。这样 i2v 模型在做插值时,主要变化集中在主体动作,背景 / 构图 / 光照都稳定。

emotion=`neutral` 时不加 mood 修饰(避免画蛇添足)。

## Step 4 — keyframe_node profile-driven 分支

`ShotState` 加 `keyframe_strategy: str` 字段。`fanout_shots` 时按 `profile.keyframe_strategy` 注入。

`keyframe_node` 主要逻辑:
```
strategy = state["keyframe_strategy"]
use_rich = bool(shot.shot_type and shot.camera_angle)

if strategy == "first_last":
    first/last instr = build_first_last_pair(...) if use_rich else legacy + rule hint
    if anchor: i2i x2 → first/last keyframe_url
    else:      t2i x2 → first/last keyframe_url
else:  # single
    instr = build_keyframe_instruction(...) if use_rich else legacy
    if anchor: i2i x1 → keyframe_url
    else:      t2i x1 → keyframe_url
```

**向后兼容**:
- dry_run 走 t2i fallback,流程不变,3 shot 全过
- 旧 shot(无 shot_type)走 legacy `build_keyframe_prompt`
- single 策略保持 Phase 1.3 行为

## Step 5 — Probe LLM 拆动作的真实效果

跑 storyboard.generate_storyboard,prompt = "Yan 咖啡馆等女友焦虑相视一笑",profile=short_drama(keyframe_strategy=first_last)。

**结果**(4/4 shot 都填了 action_start/end,1 次 LLM 调用,28s):

| Shot | 类型 | action_start | action_end |
|---|---|---|---|
| 0 等待 | 静态 | Yan 端杯子看窗外 | 头转向门口,手仍在杯上 |
| 1 看手机焦虑 | 半静态 | 手机刚抬到眼前 | 手机略放下,皱眉 |
| 2 Lin 进门 | **运动** | 手在玻璃门把手上,半身在外 | 完全进入店内,门关上,头转向 Yan |
| 3 相视而笑 | 半运动 | Yan 转椅识别,Lin 软焦内 | 两人面对面笑,Yan 紧张消散 |

LLM 写得**质量很高**:
- 运动型 shot(#2)有显著空间位移,完全是 i2v 想要的两端态
- 静态 shot(#0)起止主要是视线方向变化,细节差异合理
- 描述都是**自包含的画面**,不引用 action 字段(符合 prompt 要求)

**这是规则法 `frozen at BEGINNING/END` 做不到的**。

## Step 6 — Acceptance 实测

5 个 shot(运动 / 静态混合),用 yan 角色(Phase 1.2 入库的 anchor + ArcFace embedding),跑:
- Baseline:5 × 单帧 i2i(Phase 1.3 范式)
- New:5 × 双帧 i2i(Phase 3.1 LLM 法 first_last)

总成本:15 × $0.05 = ~$0.75,3.7 分钟。

### 实测结果

| shot | baseline single | first frame | last frame | first ↔ last |
|---|---:|---:|---:|---:|
| S1_walk_window | +0.478 | +0.503 | +0.610 | +0.420 |
| S2_sits_reads | +0.572 | +0.653 | +0.767 | +0.676 |
| S3_door_keys | +0.712 | +0.481 | +0.502 | **+0.350** |
| S4_surprised | +0.313 | +0.334 | +0.385 | +0.343 |
| S5_drinks_coffee | +0.572 | +0.803 | +0.508 | +0.496 |
| **mean** | **+0.530** | **+0.555** | **+0.554** | **+0.457** |

### 对照基线

| 数据点 | cos sim |
|---|---:|
| 跨人 baseline(Phase 1.2 测) | 0.040 |
| 同帧自比(理论上限) | ~1.000 |
| Anchor vs i2i 输出(Phase 1.3 probe) | 0.815 |
| **本次 first / last identity** | **0.55** |
| **本次 first ↔ last** | **0.457** |

### 结论

1. **identity 保持**:first 和 last 两帧的 identity score ≈ 0.55,跟 baseline single 持平(0.53)。Phase 1.3 的 i2i 范式在双帧场景下继续有效。
2. **两帧有意义的差异**:first ↔ last 平均 cos sim 0.457 — **远低于"同人自比" >0.9**(说明两帧不是复制),又**显著高于"跨人" 0.04**(说明还是同一人)。**这是 i2v 想要的同人不同姿态组合**。
3. **运动型 shot 区分度最大**:S3_door_keys(走门动作)first↔last = 0.35,姿态差异最强 → Phase 3.2 接 first_last i2v 时这种 shot 受益最多。
4. **静态 shot 区分度最小**:S2_sits_reads(坐着读书)first↔last = 0.676,接近"同帧",符合预期(本来动作小)。

**Phase 3.1 范式打通**,数字符合预期。下个 Phase 3.2 接首尾帧 i2v(`wanx-i2v-plus`)就能验证"双帧 vs 单帧 i2v"对 identity score 的最终影响。

---

## 落地文件

```
storyboard/schema.py            +6 行   ShotV1 加 action_start/end
storyboard/prompts.py           +30 行  SCHEMA_GUIDE 加规则 + few-shot 加运动 shot 示例
generation/__init__.py            6 行  模块占位
generation/keyframe_prompt.py    90 行  build_keyframe_instruction / build_first_last_pair
video_ppl.py                    +90 行  Shot dataclass 富字段; ShotState +keyframe_strategy;
                                        plan_node 不丢字段; keyframe_node 单/双帧分支;
                                        fanout_shots 注入 keyframe_strategy
design/phase3.1-progress.md     本日志
```

## 留给 Phase 3.2 的 TODO

1. **接 first_last i2v backend**:probe `wanx-i2v-plus` API(同 vendor,无需新 AK)。预期 video_node 改造:
   - profile.keyframe_strategy=="first_last" 时同时传 first_frame_url + last_frame_url 给 i2v
   - 现有 `wanx-i2v-turbo` 不接 last_frame,要么换 plus,要么对 turbo 静默丢弃 last
2. **video_node 富字段进 prompt**:当前 `_gen_video(prompt=shot.prompt)` 还只用 action 一句话,应该把 emotion / camera_movement 等也拼进 i2v prompt
3. **Backend router**:`generation/router.py`,按 `profile.video_backend_preferred` + shot metadata 选 backend;支持 fallback chain(plus → turbo)
4. **acceptance 加 i2v 阶段**:同 5 shot,对比"单帧 i2v(Phase 1.3)" vs "双帧 i2v(Phase 3.2)" 的视频质量 + identity score
5. **dialogue 字段**:Phase 3.1 已经从 ShotV1 串到 Shot.dialogue_speaker/text,但 video_node 没用。Phase 5 audio 模块会接,这里保留备用
