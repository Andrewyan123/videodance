# video1.0 — AI 长视频生成流水线

把 stateless 的图/视频模型套上一层"状态化"框架,产出**分钟级、角色一致**的连贯视频。LangGraph 编织 + DashScope / Anthropic / OpenAI 等多 backend,本地 SQLite 存全部产物。

```
你输入一段描述                 →  挑角色定妆照           →  挑每个镜头的视频          →  自动拼接成片
"A young man Yan walks in...    │ Yan v0 ★ v1  v2  v3   │ S1 ★◯  S2 ◯★  S3 ◯★    │ ▶ final.mp4
                                  │ (i2i anchor)             │ (identity score 标注)    │   下载
```

两种模式:

- **interactive(默认)** —— 4 步 wizard,每步用户参与抽奖+挑选,适合追求质量
- **auto** —— 单步全自动,适合 dry-run 或低成本快出

## 现在能做什么

- ✅ **角色一致性**:InsightFace ArcFace 512-d embedding + i2i 锚定(用挑中的 anchor + 指令 → keyframe,不做盲 t2i 召回。实测 identity 0.146 → 0.426,2.9×)
- ✅ **流派 profile**:短剧 / 漫画 / 电影 / 广告片 各一套(模型偏好 / 阈值 / 提示词)
- ✅ **多 backend video router**:wanx2.1-i2v-turbo / -plus(首尾帧)/ SeedDance / JiMeng(后两者 stub)
- ✅ **identity-driven retry**:每个 shot 跑完打分,不过线自动 retry seed → 切 backend → 放弃,全部评分落库当 RL reward
- ✅ **interactive 抽奖**:character / shot 每步多变体,用户挑,SSE 推实时进度,挑完拼成片
- ✅ **frontend wizard**:4 步可视化,CSS grid 横向 shot 树,hover 预览视频
- ✅ **本地优先**:全部数据在 `data/*.db` + 文件系统,无云依赖

## Quick Start

```bash
# 1. 配置 (首次)
cp .env.example .env                       # 填 ANTHROPIC_API_KEY + DASHSCOPE_API_KEY
unset all_proxy http_proxy https_proxy     # SOCKS 代理破 anthropic SDK

# 2. 装依赖
uv sync                                    # 或 pip install -r requirements.txt(如有)

# 3. 起 server
uv run uvicorn server:app --host 127.0.0.1 --port 8000

# 4. 浏览器
#    http://127.0.0.1:8000/         → wizard 交互模式(默认)
#    http://127.0.0.1:8000/auto.html → 老 auto 模式
```

CLI(不走 server):

```bash
uv run python video_ppl.py                 # 自动模式,__main__ 默认 dry_run=True
uv run python -m assets create ...         # 单独建角色卡
uv run python -m storyboard generate ...   # 单独出分镜
```

### 真实跑成本(参考)

| 配置 | 耗时 | 成本 |
|---|---:|---:|
| dry_run(全 mock) | <10 s | $0 |
| 单 char variant(3 张 t2i 三视图) | ~30 s | ~$0.06 |
| 单 shot variant(i2i + wanx-i2v-plus,5 s 视频) | ~5 min | ~$3 |
| Phase 5.1 acceptance(1 char × 2 + 1 shot × 2 + stitch) | ~12 min | ~$6 |

## 架构一眼

```
┌──────────┐    ┌────────────┐    ┌─────────────────────┐    ┌──────────┐
│ Planner  │ →  │ Char Sheets│ →  │ Shot Subgraph × N   │ →  │ Stitcher │
│  (LLM)   │    │  (t2i 3-vw)│    │  (并行 fanout)       │    │ (ffmpeg) │
└──────────┘    └────────────┘    │  Keyframe → Video    │    └──────────┘
                                  │  → Critic → (retry)  │
                                  └─────────────────────┘
```

- **Asset Lib**:下游所有阶段的唯一参考源(反角色漂移)
- **Shot is the atom**:每个 shot 独立,失败重生只重做这一个
- **永不让 shot N 的输出作为 shot N+1 的输入** —— 每个 shot 从 Asset Lib 重新取
- **Schema 即 API**:层间 Pydantic + 显式 validation,schema 升级走 versioning
- **Critic 输出从 day 1 起就结构化**:`data/critic_scores.db` 后续是 SFT/RL 的 reward

完整设计 → [design/architecture.md](design/architecture.md)。

## 项目结构

```
video1.0/
├── video_ppl.py            # 自动模式主图(LangGraph)
├── server.py               # FastAPI:auto + interactive 端点 + 静态前端
├── frontend/               # 单页 vanilla HTML/CSS/JS(无 build)
│   ├── index.html, wizard.{js,css}     ← 4 步 wizard(默认)
│   └── auto.html,  auto.{js,css}        ← 老 auto 模式
│
├── profiles/               # 短剧 / 动画 / 电影 / 广告 4 套 Profile
├── assets/                 # Phase 1.x:角色卡 SQLite + 三视图 PNG + ArcFace embedding
├── storyboard/             # Phase 2:strict Pydantic DSL + json-repair planner
├── generation/             # Phase 3:keyframe / video prompt + multi-backend router
├── critic/                 # Phase 4:identity score + retry policy
├── candidates/             # Phase 5.1:interactive 模式 SQLite(variants + selection)
├── providers/              # 模型 backend:LLM / Image / ImageEdit / Video (env-driven)
├── prompts/                # 老的集中 prompt 模板
├── audio/, post/, orchestration/    # Phase 6 留位,未实装
│
├── design/                 # 设计文档(架构 + 各 Phase 落地纪要)
└── data/                   # 运行产物(.gitignored):assets.db / critic_scores.db / candidates.db / 角色 PNG
```

## 配置

`.env`(从 `.env.example` 复制):

| Key | 必填 | 说明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✓ | Planner LLM(走 dashscope compatible-mode) |
| `ANTHROPIC_BASE_URL` | ✓ | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `DASHSCOPE_API_KEY` | ✓ | Image + video generation |
| `LLM_PROVIDER` | | `anthropic_compat` (默认) \| `openai_compat` |
| `IMAGE_PROVIDER` | | `dashscope_t2i` (默认) |
| `IMAGE_EDIT_PROVIDER` | | `wanx_i2i` (默认) |
| `VIDEO_PROVIDER` | | `dashscope_i2v` (默认) \| `seeddance` \| `jimeng`(后两者 stub) |
| `PLANNER_MODEL` | | 例如 `aws.claude-opus-4-6` |
| `STITCH_OUT_DIR` | | 拼接输出目录,默认 `/tmp/video1.0_stitch` |

Shell env 优先于 .env(便于命令行临时覆盖)。

## 文档

- [design/architecture.md](design/architecture.md) — 整体架构、Profile 设计、模块布局、路线图
- [design/full.md](design/full.md) — 行业调研原文(设计灵感来源)
- [CLAUDE.md](CLAUDE.md) — 给 Claude Code 用的快速入门,含 gotchas
- [design/phaseN-progress.md](design/) — 各 Phase 的落地纪要(Phase 1 / 1.2 / 1.3 / 2 / 3.1 / 3.2 / 4.1 / 5.1 / 5.2)
- [assets/README.md](assets/README.md), [storyboard/README.md](storyboard/README.md), [critic/README.md](critic/README.md) — 模块级说明

## 状态

| 能力 | 状态 |
|---|---|
| Auto 模式 pipeline | ✅ |
| Interactive 模式 + wizard UI | ✅ |
| Asset Lib(角色卡 + ArcFace) | ✅ |
| Storyboard DSL + json-repair | ✅ |
| 多 backend video router | ✅(wanx 实接,seeddance/jimeng stub) |
| Identity-driven retry loop | ✅(只 identity 一维) |
| Multi-dim critic(scene/narrative/VSA) | ⏳ Phase 4.2 |
| TTS + 口型同步 | ⏳ Phase 6 |
| RIFE 转场 / 字幕 / BGM | ⏳ Phase 6 |
| Session 持久化(wizard 可 resume) | ⏳ |
| 真 OSS 上传 / 分享 | ⏳ |

## 设计原则(摘自 architecture.md)

1. **Shot is the atom** —— 单 shot 独立,失败重生只重做这一个
2. **永不让 shot N 输出作为 shot N+1 输入** —— 每个 shot 从 Asset Lib 重新取参考
3. **Schema 即 API** —— 层间 Pydantic + version,不在原地改字段
4. **Critic 输出从 day 1 起就结构化** —— 落 SQLite,天然 RL reward
5. **Provider Protocol per backend type** —— 换 vendor 改 env

## 非目标

- 工业级吞吐 / 并发调度 / 多租户
- 对象存储 / CDN / 鉴权
- 实时 / 直播
- 自动训练 LoRA(后续可选)

个人本地使用,简洁为先。
