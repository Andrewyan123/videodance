# video1.0 — AI 长视频生成流水线

LangGraph 编织的 文/分镜 → 多 shot 视频 → 拼接成片 的本地工具链。两种模式:**auto**(一把跑完)和 **interactive**(用户每步参与抽奖+挑选)。

**📋 整体架构 / 流派 Profile / 长期路线图 → [design/architecture.md](design/architecture.md)。各 Phase 落地纪要 → `design/phaseN-progress.md`。本文档是给 Claude Code(和人)看的快速入门。**

## 当前能力(Phase 0 → 5.2 全部已落地)

```
Phase 1   Asset Library                — SQLite + 文件系统角色卡 (data/assets.db)
Phase 1.2 InsightFace ArcFace          — 角色 embedding (512-d)
Phase 1.3 i2i paradigm                 — 用挑中的 anchor + 指令做 keyframe (不是盲 t2i 召回)
Phase 2   Storyboard DSL               — strict Pydantic + 3 层 json-repair + profile-aware planner
Phase 3.1 Rich keyframe fields         — shot_type/camera/emotion/action_start/action_end/dialogue + LLM 切首尾
Phase 3.2 First-last frame i2v         — wanx2.1-kf2v-plus + multi-backend router
Phase 4.1 Critic loop                  — identity → policy(retry seed → switch backend → fail),落 data/critic_scores.db
Phase 4.2 Rich-field i2v prompt        — motion arc + camera + emotion + dialogue 拼进 i2v prompt
Phase 5.1 Interactive backend          — char/shot variant 抽奖 + 用户 select + 拼接,落 data/candidates.db
Phase 5.2 Frontend wizard              — 4 步交互 UI:Input → Chars → Shots tree → Stitch + SSE 进度
```

```
Planner -> [CharVariants × N] -> [ShotVariants × K per shot] -> Stitcher
   LLM      用户挑 anchor             用户挑 video               ffmpeg concat
            (后续 i2i 用 anchor)        critic 自动评分
                                       failure → retry(seed/backend)
```

## Project Structure

```
video_ppl.py        # 主图(自动模式):Planner → CharacterSheets → Shots(并行)→ Stitcher
                    #   Shot 含 retry_seed / retry_force_backend / last_backend_used(Phase 4.1)
server.py           # FastAPI:auto 模式 SSE + interactive 模式 8 端点 + /api/asset 静态服务

frontend/           # 单页 vanilla HTML/CSS/JS,不需 build
  index.html / wizard.{js,css}   # 默认入口:4 步 wizard(creative mode)
  auto.html  / auto.{js,css}     # 老 auto 模式,/auto.html 单独访问

design/             # 设计文档(source of truth)
  architecture.md
  phase{1, 1.2, 1.3, 2, 3.1, 3.2, 4.1, 5.1, 5.2}-progress.md   # 各阶段落地纪要

profiles/           # 流派 Profile:short_drama / anime / cinema / commercial
  base.py registry.py *.py

assets/             # Phase 1.x:角色资产库
  schema.py store.py builder.py faceid.py cli.py  README.md
  # CharacterCard(SQLite data/assets.db)+ 三视图 PNG + ArcFace 512-d embedding

storyboard/         # Phase 2:分镜 DSL
  schema.py prompts.py repair.py validator.py planner.py cli.py  README.md
  # Pydantic strict + 3 层 fallback(json-repair → few-shot retry → fail)

generation/         # Phase 3:keyframe / video 执行 + 选 backend
  keyframe_prompt.py video_prompt.py router.py
  # build_first_last_pair() / build_video_instruction()
  # router.generate_video(profile, shot, seed?, force_backend?) → (Result, factory_name)

critic/             # Phase 4:多维评审 + 重试决策
  identity.py policy.py store.py  README.md
  # score_shot_identity(ffmpeg 抽帧 → InsightFace → cos sim vs anchor)
  # decide_next(score, threshold, retry_count, profile) → pass | retry_seed | retry_backend | fail
  # 评分一律落 data/critic_scores.db(后续 RL reward 用)

candidates/         # Phase 5.1:interactive 模式数据层
  schema.py store.py builder.py acceptance_test.py
  # 3 表 SQLite(data/candidates.db):sessions / character_candidates / shot_candidates
  # propose_character_variants() / propose_shot_variants() / promote_character_to_canonical()

providers/          # 模型 backend,每类一组 Protocol + 具体类
  base.py _dashscope.py
  llm.py        # AnthropicCompatClient(走 dashscope compat-mode),OpenAICompatClient
  image.py      # DashScopeT2I(wanx2.1-t2i-turbo)
  image_edit.py # WanxI2IClient(wanx2.1-imageedit, i2i 通道)
  video.py      # DashScopeI2V(turbo), WanxI2VPlusClient(kf2v-plus), SeedDance/JiMeng(stub)

prompts/            # 老的集中 prompt(planner / critic / shots)
                    # Phase 2 起,新模块自带 prompts(storyboard/generation/critic)

audio/  post/  orchestration/   # Phase 6 留位,仅 __init__.py,未实装

data/               # 运行产物(.gitignored)
  assets.db / assets/<char_id>/         # 角色卡 + 三视图
  critic_scores.db                       # 每次评分(thread_id × shot_id × dimension)
  candidates.db                          # interactive sessions / variants / selections
video_pipeline.db   # LangGraph checkpoint(老,位置在项目根)
```

**节点代码不直接调任何 vendor SDK** —— 只调 `LLM_PROVIDER.ainvoke()` / `IMAGE_PROVIDER.generate()` / `IMAGE_EDIT_PROVIDER.edit()` / `VIDEO_PROVIDER.generate()`。换 vendor 改 env,加 vendor 在 providers 包加新类 + 工厂多一个分支。

## 两种运行模式

### Auto(一把跑完)

```
POST /api/runs  { mode: "auto" (默认), prompt, duration_sec, profile, dry_run }
  → 后端跑 video_ppl.run_pipeline(...)
  → SSE 推 started / plan_done / character_sheets_done / shot_progress / stitch_done / completed
  → 成片落 STITCH_OUT_DIR
```

UI:`/auto.html`。

### Interactive(用户参与抽奖)

| Endpoint | 行为 |
|---|---|
| `POST /api/runs  {mode:"interactive"}` | 只跑 planner,返 thread_id + storyboard,入 sessions 表 |
| `GET  /api/runs/{tid}/storyboard` | 拿 storyboard JSON |
| `POST /api/runs/{tid}/characters/{cid}/propose?n=4` | 抽 N 张候选(t2i) |
| `POST /api/runs/{tid}/characters/{cid}/select?variant=K` | 选定 + promote 到 assets/store |
| `POST /api/runs/{tid}/shots/{sid}/propose?n=3` | 抽 K 段候选(i2i + i2v + critic) |
| `POST /api/runs/{tid}/shots/{sid}/select?variant=K` | 选定 |
| `GET  /api/runs/{tid}/tree` | 整棵树 + selection 状态 |
| `POST /api/runs/{tid}/stitch` | 拉 selected 视频 → ffmpeg concat → 成片 |

`GET /api/runs/{tid}/events` SSE 流:propose_*_start / progress / done_variant / complete / error。

UI:`/`(默认,4 步 wizard)。

## 关键设计决策

- **Provider 抽象**:每类(LLM/Image/ImageEdit/Video)定义 Protocol + Result dataclass。鸭子类型,不继承 ABC。Provider 在 `providers/__init__.py` import 时构造一次(单例),env-driven。
- **i2i 而非 t2i 召回(Phase 1.3 关键决策)**:角色 keyframe 用 `anchor.png + 指令文本` 走 wanx-imageedit,**不**重新 t2i 召回。原因:wanx-t2i-turbo 不吃 ref_images,纯 t2i 召回 identity 只有 0.146,切 i2i 后 0.426(2.9×)。
- **Prompts 集中或就近,二选一**:老 `prompts/` 集中放 planner/critic/shots;新模块(storyboard/generation/critic)各自自带 prompt 文件。换 prompt 不改图。
- **dry_run 在 wrapper 而非 provider**:节点侧检查 `dry_run` 返回 mock,provider 类干净不沾 mock。
- **ShotState 键名 vs PipelineState 故意错开**(`char_sheets` vs `character_sheets`, `style` vs `global_style`, `is_dry_run` vs `dry_run`, `pipeline_thread_id` 透传 thread_id):LangGraph 子图退出时同名键会回写父级,并行 fanout 写非 `Annotated` 父键会触发 `InvalidUpdateError`。
- **storyboard 落 JSON 字符串到 sessions 表**:schema 演进零代价。同款做法 `assets.store.payload` 也用了。
- **character variants 用 `__v0/__v1` 后缀走 asset_builder**:每个 variant 独立目录(`data/assets/char_yan__v0/`),用户 select 后 `promote_character_to_canonical` 把数据写到 canonical `char_yan`,下游 i2i 无感知。
- **critic 评分一律落库**(无论 pass/fail):`data/critic_scores.db` 是后续 RL 的天然 reward signal。表 `dimension` 字段为多维度准备,目前只有 `identity`。

## Gotchas(踩过的坑)

- **eval.dashscope.aliyuncs.com/apps/anthropic-native 403 RBAC**:eval 二组 key 没权限。走 `dashscope.aliyuncs.com/compatible-mode/v1` —— 路径是 `/chat/completions` 但 Claude 模型响应是 Anthropic 原生 schema。这就是 `AnthropicCompatClient` 的设计起点。
- **SOCKS 代理破 anthropic SDK**:shell 里有 `all_proxy=socks5://...` 会让 anthropic 报 `socksio not installed`。跑前 `unset all_proxy http_proxy https_proxy`(或装 `httpx[socks]`)。
- **LangGraph `stream_mode="updates"` 子图节点 update=None**:不能直接 `update.keys()`,要 `isinstance(update, dict)` 守卫。
- **wanx2.1-i2v-turbo duration 只接受 [3, 5]**:自动 clamp。更长用 `wanx2.1-i2v-plus`(到 10s)。
- **wanx2.1-i2v-turbo 不返回末帧**:`last_frame_url=None`,链式 last → first 暂不可用,fanout 都传 None。
- **wanx2.1-t2i-turbo 不吃 ref_images**:Phase 1.3 切 i2i 的根本原因。要图条件生成走 `wanx2.1-imageedit` / `qwen-image-edit`。
- **wanx-imageedit 要 data URI**:`file://` URI 报 "Incorrect padding",必须 base64 data URI。
- **wanx-kf2v-plus 真实模型名**:8 个变体探测后是 `wanx2.1-kf2v-plus`(不是 `wan2.1-kf2v-plus`)。
- **planner LLM 会把 JSON 套 ```json ... ```**:`plan_node` 和 `storyboard/repair.py` 都有 fence 剥离。
- **wanx-i2v-plus first-last 反而比 turbo 单帧 identity 低**(0.368 vs 0.426,强运动 shot):双帧插值 pose drift。Phase 4.1 critic loop 就是为此 retry。
- **macOS TCC 拦 ~/Documents**:`Operation not permitted` → 系统设置→隐私与安全性→文件与文件夹给 Terminal 授权 → Cmd+Q 重启 Terminal。
- **macOS /tmp 自动清理**:跨日的 acceptance 产物会消失,别指望长久。
- **DashScope 429 / 418 wrapped 429**:`providers/_dashscope.py` 有指数退避 + jitter 重试,timeout / connection error 同样。

## 运行

Key 和 provider 选择都从项目根 `.env` 加载(`providers/__init__.py` 在工厂前 `load_dotenv()`)。模板见 `.env.example`,实际值放 `.env`(已 gitignored)。Shell env 优先于 .env(`override=False`)。

```bash
cp .env.example .env                       # 首次,填 key
unset all_proxy http_proxy https_proxy     # 见 gotchas
uv run uvicorn server:app --host 127.0.0.1 --port 8000

# 浏览器:
#   http://127.0.0.1:8000/         → wizard 交互模式
#   http://127.0.0.1:8000/auto.html → 老 auto 模式
```

CLI(不走 server):

```bash
uv run python video_ppl.py                   # __main__ 默认 dry_run=True
uv run python -m assets create ...           # 角色卡 CLI(Phase 1)
uv run python -m storyboard generate ...     # 分镜 DSL CLI(Phase 2)
```

`.env` 必填:`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`(指向 dashscope compat-mode)、`DASHSCOPE_API_KEY`。其他可选项见 `.env.example` 注释。

### 真实跑成本参考(Phase 5.1 acceptance:1 char × 2 variant + 1 shot × 2 variant + stitch)

| 步骤 | 耗时 | 成本 |
|---|---:|---:|
| planner | 9 s | <$0.01 |
| 2 char variants(t2i 三视图) | 59 s | ~$0.12 |
| 2 shot variants(i2i + wanx-i2v-plus,串行) | 639 s | ~$6 |
| stitch | 2.5 s | 0 |
| **总计** | **~12 min** | **~$6** |

## 当前未实装 / TODO

- **真实第三方 backend**:`SeedDanceClient` / `JiMengClient` 已写 stub + API shape,等用户拿到 `ARK_API_KEY` / volcengine AK 即可填实。`OpenAIImageClient`(gpt-image-2)同样 stub。
- **multi-dim critic**:目前只 identity 一维。scene / narrative / VSA 留 Phase 4.2。
- **音频 + 口型同步**:`audio/` 仅占位,TTS(CosyVoice)+ Wan2.2-Animate 留 Phase 6。
- **后期精修**:`post/` 占位,RIFE 转场 / BGM / 字幕烧录留 Phase 6。
- **stitch 上传到 OSS**:当前只产 `file:///tmp/...` 本地路径,要 share 用 scp / 上传网盘。
- **session 持久化**:wizard 刷新页面 / 关浏览器后无法 resume(SQLite checkpoint 在,SSE 流断了)。
- **dry_run 完整链路**:`candidates/builder.py` propose 还没接 dry_run,wizard 4 步不能纯 mock 体验。
- **失败 variant 单独重抽**:UI 目前红框显示,要点"再抽 N 个"整组重来。
