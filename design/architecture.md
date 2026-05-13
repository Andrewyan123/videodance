# 长视频(分钟级)AI 视频生成项目 — 架构设计

> 个人使用,本地优先,简洁为先。所有"工业化"特性(对象存储、多租户、分布式)默认不做。

## 0. 目标与非目标

**目标**
- 把 stateless 的视频模型套上一层"状态化"框架,产出分钟级、角色一致的连贯视频
- 支持四类常见流派(短剧、动画、电影、广告片),每类有独立的 Profile 决定模型选型 / 提示词 / 阈值 / 后期策略
- 模块解耦 —— 每一层可独立优化、独立测试,通过 Pydantic schema 串联
- 单机本地运行,数据存本地 SQLite + 文件系统

**非目标**
- 工业级吞吐 / 并发调度 / 多租户
- 对象存储 / CDN / 鉴权
- 实时 / 直播
- 自动训练 LoRA(后续可选,不在 MVP)

---

## 1. 现状 (Phase 0)

| 层 | 已有 | 状态 |
|---|---|---|
| Orchestration | `video_ppl.py` LangGraph 图:Planner → CharacterSheets → Shots(并行)→ Stitcher | ✅ 跑通 |
| LLM provider | AnthropicCompat + OpenAICompat (DashScope eval 网关) | ✅ |
| Image provider | DashScope wanx-t2i-turbo | ✅ |
| Video provider | DashScope wanx-i2v-turbo | ✅ |
| Stitch | ffmpeg concat,失败回退 re-encode | ✅ |
| Web UI | FastAPI + SSE + 单页 vanilla HTML | ✅ |
| Asset Library | 无 —— 每个 shot 重新生成角色参考图 | ❌ |
| Storyboard schema | 单层 JSON,字段简略 | ⚠️ 待细化 |
| 首尾帧 i2v | 无 —— 单帧条件 | ❌ |
| Critic | 直 pass | ❌ |
| 音频 / 口型同步 | 无 | ❌ |
| 流派 Profile | 无 | ❌ |

---

## 2. 目标态分层架构

```
┌──────────────────────────────────────────────────────────────────┐
│  Orchestration (orchestration/)                                  │
│    LangGraph + SQLite checkpoint, retry/resume, profile loader   │
└──────────────────────────┬───────────────────────────────────────┘
                           │
       ┌───────────────────┼───────────────────────────┐
       ▼                   ▼                           ▼
   ┌────────┐         ┌─────────┐                  ┌────────┐
   │Profile │ ──────→ │Pipeline │ ←──── Critic ────│Critic  │
   │(流派)  │  drives │ stages  │      reward      │(评审)  │
   └────────┘         └─────────┘                  └────────┘
                           │
   ┌───────────────────────┼───────────────────────┬──────────┬──────────┐
   ▼                       ▼                       ▼          ▼          ▼
┌──────┐              ┌──────────┐            ┌────────┐  ┌──────┐  ┌──────┐
│Asset │              │Storyboard│            │Generate│  │Audio │  │ Post │
│ Lib  │              │  (DSL)   │ ─────────→ │  Core  │→ │+Sync │→ │      │
└──────┘              └──────────┘            └────────┘  └──────┘  └──────┘
   ▲                       │                       │
   └─── retrieval (@char_id, @scene_id) ───────────┘
        每个 shot 从这里取角色 / 场景, 永不复用 shot N-1 输出
```

**关键耦合点**:Asset Lib 是下游所有阶段的**唯一参考源**。Storyboard 的 `@char_01` 引用在 Generation 阶段被解析为 (3-view, FaceID embedding, style tokens) 元组。这是反角色漂移的核心机制。

---

## 3. 设计原则(5 条铁律)

1. **Shot is the atom.** 单个 shot 是不可分割的工作单元。每个 shot 的中间产物(keyframe / video / critic score)独立持久化。失败重生只重做这个 shot。

2. **永不让 shot N 的输出作为 shot N+1 的输入。** 每个 shot 都从 Asset Lib 重新取角色 / 场景参考。这是 design 文档反复强调的反漂移铁律。

3. **Schema 即 API。** 每层之间用 Pydantic 模型 + 显式 validation。schema 升级走 versioning(`shot_schema_v2`),不在原地改字段。

4. **Critic 输出从 day 1 起就是结构化的。** 后续做 SFT/RL on planner/router policy 时,这些分数就是天然的 reward signal。

5. **Provider Protocol per backend type.** LLM / Image / Video / TTS / LipSync 各自一个 Protocol,具体实现可换 vendor。换模型改 env,不动节点代码。

---

## 4. 模块布局

```
video1.0/
├── design/
│   ├── architecture.md      ← 本文件
│   └── full.md              ← 行业调研原文
├── profiles/                 # 流派配置 (本次落地)
│   ├── base.py              # Profile dataclass + 默认值
│   ├── short_drama.py
│   ├── anime.py
│   ├── cinema.py
│   ├── commercial.py
│   └── registry.py          # name → Profile() 解析
│
├── assets/                   # 资产库 (骨架本次落地, 实现 Phase 1)
│   ├── schema.py            # CharacterCard / SceneCard Pydantic
│   ├── store.py             # SQLite 元数据 + 文件系统
│   ├── faceid.py            # InsightFace embedding (Phase 1.2)
│   ├── builder.py           # T2I → 3-view → 校验 → 入库
│   └── cli.py               # python -m assets create/list/inspect
│
├── storyboard/               # 分镜 DSL (骨架本次落地, 实现 Phase 2)
│   ├── schema.py            # Shot v1 schema (versioned)
│   ├── planner.py           # LLM → JSON (jsonrepair + few-shot 兜底)
│   ├── validator.py         # schema check + @id 引用解析
│   ├── prompts.py           # planner system prompts (per profile)
│   └── cli.py
│
├── generation/               # 图 / 视频执行 (骨架本次落地, 实现 Phase 3)
│   ├── keyframe.py          # 首帧 / 尾帧 t2i (条件: char refs + scene)
│   ├── video.py             # i2v (single / first_last frame)
│   ├── router.py            # backend 选型策略 (从 Profile 取偏好)
│   └── providers/           # 现 providers/ 后续平移过来
│
├── critic/                   # 多维度评审 (骨架本次落地, 实现 Phase 4)
│   ├── identity.py          # ArcFace 跨 shot 一致性
│   ├── scene.py             # CLIP 场景连续性
│   ├── narrative.py         # VLM caption → LLM 判叙事
│   ├── vsa.py               # Visual-Script Alignment
│   ├── policy.py            # 重试升级 (seed → backend → human)
│   └── prompts.py           # critic rubric per profile
│
├── audio/                    # 音频 + 口型 (骨架本次落地, 实现 Phase 5)
│   ├── tts.py               # CosyVoice / ElevenLabs
│   ├── lipsync.py           # Wan2.2-Animate stub
│   └── mixer.py
│
├── post/                     # 后期 (骨架本次落地, 实现 Phase 6)
│   ├── stitch.py            # ffmpeg concat / RIFE 转场
│   ├── master.py            # 字幕烧录 / 混音
│   └── overlay.py           # 广告片 logo/CTA 叠加
│
├── orchestration/            # LangGraph 编织 (骨架本次落地)
│   ├── graph.py             # 主图 (后续从 video_ppl.py 平移)
│   ├── state.py             # PipelineState / ShotState
│   └── nodes.py             # 节点 = module 的薄包装
│
├── providers/                # 已存在,后续平移到 generation/providers/
├── prompts/                  # 已存在,后续拆分到各模块
├── server.py                 # FastAPI
└── frontend/                 # Web UI
```

**注意**:本次 PR **只新增骨架**,不平移现有代码。`video_ppl.py` / `providers/` / `prompts/` 保留原位,继续工作。后续 Phase 真正实现某模块时,再做"现有代码 → 新模块"的迁移。

---

## 5. 流派 Profile —— "口子" 机制

### 5.1 Profile 是什么

每个流派是一个 dataclass,集中决定该流派下所有阶段的行为差异。Profile **只做配置**,不导入任何模块类——通过字符串引用 backend ID / strategy name。

### 5.2 默认值对照表

| 维度 | short_drama | anime | cinema | commercial |
|---|---|---|---|---|
| 视频时长 | 60-180s | 灵活 | 300-5400s | 15-60s |
| 单 shot 时长 | 5-10s | 3-8s | 5-15s | 3-8s |
| Shot 数 ≈ | duration/7 | duration/5 | duration/10 | duration/5 |
| identity 阈值 | 0.85 | 0.6 | 0.85 | 0.5 |
| style 阈值 | 0.7 | 0.85 | 0.85 | 0.7 |
| product 阈值 | n/a | n/a | n/a | 0.9 |
| Planner 重点 | dialogue + emotion | scene + 风格延续 | camera 语言 + pacing | CTA + product |
| Schema 扩展 | `dialogue.speaker` | `style_ref` | `camera_movement, lighting` | `product_id, cta, logo_overlay` |
| Keyframe 策略 | first_last | single | first_last | first_last |
| Video backend 偏好 | kling3 → wanx-i2v-plus | wan2.2 anime-lora → wanx-i2v-plus | sora2 → kling3 | sora2 → seeddance2 |
| 音频启用 | yes | optional | yes | yes(BGM 为主) |
| 口型同步 | required | optional | required | optional |
| Critic 维度 | identity, lipsync, narrative | style, scene, narrative | all 4 | product, cta, scene |
| 转场策略 | hard_cut | hard_cut | rife | hard_cut + overlay |
| Retry 预算 | 2 | 1 | 5 | 5 |

### 5.3 扩展点(钩子)汇总

下面这张表是 Profile 暴露的**所有可配置点**。加新流派只需填这张表的字段。

| Phase | Hook | Type | Default |
|---|---|---|---|
| Planner | `system_prompt` | str / callable | generic 模板 |
| Planner | `shot_count_formula` | callable(duration_sec)→int | `ceil(duration/6)` |
| Planner | `extra_schema_fields` | list[Pydantic Field] | [] |
| Planner | `single_shot_duration_range` | tuple[float, float] | (5.0, 8.0) |
| Assets | `consistency_threshold` | float | 0.85 |
| Assets | `required_views` | list[str] | ["front", "side", "3-quarter"] |
| Generation | `keyframe_strategy` | `single \| first_last \| n_grid` | `single` |
| Generation | `video_backend_preferred` | str (backend ID) | `wanx-i2v-turbo` |
| Generation | `video_backend_fallback` | list[str] | [] |
| Generation | `image_backend_preferred` | str | `wanx2.1-t2i-turbo` |
| Audio | `enabled` | bool | False |
| Audio | `tts_backend` | str | `cosyvoice` |
| Audio | `lipsync_required` | bool | False |
| Critic | `dimensions` | set[str] | {identity, scene, narrative, vsa} |
| Critic | `retry_budget` | int | 2 |
| Critic | `escalation_policy` | list[str] | [seed, backend, human] |
| Post | `transition_smoother` | `concat \| rife \| crossfade` | `concat` |
| Post | `overlay_renderer` | str \| None | None |
| Post | `subtitle_burner` | bool | False |

### 5.4 调用流

```
client POST /api/runs (profile="short_drama")
   ↓
orchestrator: profile = registry["short_drama"]
   ↓
节点执行时从 Profile 取配置 (不再硬编码):
   planner_node:    prompt = profile.system_prompt
                    count = profile.shot_count_formula(duration)
   keyframe_node:   strategy = profile.keyframe_strategy
   video_node:      backend = router(profile, shot_metadata)
   critic_node:     dims = profile.dimensions
                    budget = profile.retry_budget
   post_node:       smoother = profile.transition_smoother
```

### 5.5 Profile vs Skill

- **Profile**:一组钩子值的集合,大颗粒、流派级、整套配齐。
- **Skill**(未来演进):单个钩子的可复用实现,小颗粒、可组合。例如 `rife_smoother`、`logo_overlay`、`json_repair_planner`。

MVP 阶段先做 Profile;成熟后把高复用钩子抽成 Skill。

---

## 6. Video Backend 矩阵

| Backend ID | 接入状态 | 首尾帧 | 时长 | 强项 | 主用 profile |
|---|---|---|---|---|---|
| `wanx-i2v-turbo` | ✅ 已接 | 否 | 3-5s | 便宜、快 | dev / dry-run |
| `wanx-i2v-plus` | ⏳ | 是 | 5-10s | 国风、双帧 | drama / anime |
| `kling3` | ⏳ TBD | 是 | 5-10s | 人物动作大师 | drama / cinema |
| `sora2` | ⏳ TBD | 否 | 5-20s | 物理真实感 | cinema / commercial |
| `seeddance2` | ⏳ stub(本次) | 是 | 5-10s | 字节系 | drama / anime |
| `jimeng` (即梦) | ⏳ stub(本次) | 是 | 灵活 | 风格丰富 | anime / commercial |
| `wan2.2-anime-lora` | ⏳ 自托管 | 否 | 灵活 | 二次元 | anime |

SeedDance 和即梦走字节 volcengine 平台,认证体系类似(`VOLC_ACCESSKEY` + `VOLC_SECRETKEY`)。本次 PR 只完善接口签名,实际 API 调用等拿到 AK 后再填。

---

## 7. 分阶段路线图

| Phase | 范围 | 主交付物 | 关键 acceptance |
|---|---|---|---|
| **Phase 0** | 已完成 | MVP pipeline、Web UI | dry_run 跑通 |
| **本次 PR** | 文档 + 骨架 | architecture.md / profiles/ / 6 模块骨架 / SeedDance+JiMeng stub | 现有 `video_ppl.py` 不受影响,新模块可独立 import |
| **Phase 1** | Asset Library MVP | CharacterCard schema / SQLite store / 创建 CLI / 引用解析 | 5 个 shot 跨 shot 的 face cosine sim 均值 >0.75 |
| **Phase 2** | Storyboard DSL 加固 | v1 schema / jsonrepair / @id 引用校验 | 20 prompt 测试,合法率 >95% |
| **Phase 3** | Generation 升级 | 首尾帧 / backend router / 新 backend 接入 | 跨 backend 延迟+成本对比表 |
| **Phase 4** | Critic Loop | 4 维评分 / 重试升级 | 故意造劣质 shot,critic 命中并触发重试 |
| **Phase 5** | 音频 + 口型 | TTS / lipsync(Wan2.2-Animate) | 对话片段口型偏差 <100ms |
| **Phase 6** | 后期精修 | RIFE 转场 / BGM / 字幕 | 1 分钟成片,无可见硬切 |

每个 Phase 是独立可发布的里程碑。前一 Phase 不必完美就能进下一个 —— stub 也行,关键是接口对齐。

---

## 8. 数据存储(本地优先)

| 类型 | 位置 | 备份 |
|---|---|---|
| 角色 / 场景元数据 | `data/assets.db` (SQLite) | 手动 `cp` |
| 角色三视图 / 场景图 | `data/assets/<char_id>/*.png` | 手动 |
| 生成的 keyframe / 视频中间产物 | `data/runs/<thread_id>/*` | 可清理 |
| LangGraph checkpoint | `data/checkpoint.db` (SQLite, 现 `./video_pipeline.db` 后续迁过来) | - |
| 最终成片 | `data/output/*.mp4` | 手动 |

所有路径相对项目根。`.gitignore` 已排除 `data/`。

**为什么不做云存储**:单机使用,文件系统就是数据库;复杂同步是负债。需要分享成片用 `scp` 或上传到任意网盘。

---

## 9. 工程坑(摘自 full.md,持续提醒)

1. **角色漂移累积**:shot N 输出**永远不能**作为 shot N+1 输入。每个 shot 从 Asset Lib 重新取参考。
2. **算力调度**:单 shot 视频 1-3 分钟,50-100 shot 串行要几小时。LangGraph 并行 fanout + DashScope QPS 限流 + 429 退避重试已就位。
3. **JSON 健壮性**:planner LLM 输出 fence 围栏 / 缺字段都正常,jsonrepair + few-shot retry 是基操。
4. **长叙事天花板**:别试图一次生成 100 分钟。原子 shot + 拼接,长叙事一致性交给 Asset Lib + Critic。

---

## 10. 未决项 / 后续讨论

- **InsightFace 依赖体积**:Phase 1.2 接入 ArcFace 时需要(`insightface` + `onnxruntime`,模型 ~300MB)。如果觉得重,先用 CLIP image-image 余弦做占位。
- **LoRA 训练 pipeline**:Phase 1.5 可选项。需要 GPU,本地训练耗时长,建议核心角色才训。
- **VolcEngine API 认证细节**:等拿到 AK 再实装。
- **Skill 层提取时机**:Profile 落地、跑过 2-3 个流派后再拆。
- **前端流派切换 UI**:Phase 1 完成后加,在 server.py 的 `/api/runs` 加 `profile` 字段。

---

## 11. 引用

- `design/full.md` —— 行业调研原文,设计灵感来源
- `CLAUDE.md` —— 项目快速入门
- `.env.example` —— 配置项参考
