# video1.0 — Video Generation Pipeline

LangGraph-based 文生视频流水线: 用户 prompt → 多 shot 并行生成 → ffmpeg 拼接成片。

## Architecture

```
Planner -> CharacterSheets -> [Shot Subgraph × N parallel] -> Stitcher
                                       │
                                       └── Keyframe -> Video -> Critic -> (retry?)
```

- **Planner** (LLM): 用户 prompt → 结构化 `{global_style, characters, shots}` JSON
- **CharacterSheets** (T2I): 每个角色生成多角度参考图,供后续 shot 复用
- **Shot Subgraph** (并行 fanout): 每个 shot 独立子图 — keyframe(T2I) → video(I2V) → critic(VLM) → finalize
- **Stitcher** (ffmpeg): 下载所有 shot 视频, `concat -c copy` 拼接(失败回退 re-encode)

State 用 LangGraph reducers 累积(`shots` 用 dict-merge,`total_cost_usd` 用 `operator.add`),所以并行 shot 写回安全。SQLite checkpointer 支持崩溃续跑。

## Project Structure

```
video_ppl.py        # 图定义 + 节点 (orchestration only)
providers/          # 模型 backends, 一类 vendor 一个类
  base.py           # Protocol + Result dataclass (LLM/Image/Video)
  _dashscope.py     # 共享 async submit-and-poll
  llm.py            # AnthropicCompatClient, OpenAICompatClient
  image.py          # DashScopeT2I, OpenAIImageClient(stub for gpt-image-2)
  video.py          # DashScopeI2V, SeedDanceClient(stub)
  __init__.py       # build_*_provider() 工厂, 读 env 选实现
prompts/            # 提示词模板
  planner.py        # PLANNER_SYSTEM + build_planner_user()
  critic.py         # CRITIC_RUBRIC
  shots.py          # build_keyframe/video/character_ref_prompt + CHARACTER_ANGLES
```

**节点代码不直接调任何 vendor SDK** — 只调 `LLM_PROVIDER.ainvoke()` / `IMAGE_PROVIDER.generate()` / `VIDEO_PROVIDER.generate()`。换 vendor 改 env 即可,加 vendor 在 providers 包里加新类 + 在工厂多一个分支。

## 关键设计决策

- **Provider 抽象**: 每类(LLM/Image/Video)定义 Protocol + Result dataclass。具体类不继承 ABC,鸭子类型够用。Provider 在 `video_ppl.py` import 时构造一次(单例),env-driven。
- **Prompts 集中**: 所有 prompt 字符串和模板生成函数都在 `prompts/`,不散落在节点逻辑里。换 prompt 不改图。
- **dry_run 在 wrapper 而非 provider**: `_gen_image` / `_gen_video` 在节点侧检查 `dry_run` 返回 mock,真实 provider 类干净不沾 mock 逻辑。
- **VLM critic 暂未抽 provider**: 当前 `_vlm_critic_check` 直 pass。要接 Qwen-VL/Claude Vision 时再加 `VLMProvider` Protocol + `providers/vlm.py`。
- **ShotState 键名 vs PipelineState 故意错开** (`char_sheets` vs `character_sheets`, `style` vs `global_style`, `is_dry_run` vs `dry_run`): LangGraph 子图退出时同名键会回写父级,并行 fanout 写非 `Annotated` 父键会触发 `InvalidUpdateError`。

## Gotchas (踩过的坑)

- **eval.dashscope.aliyuncs.com/apps/anthropic-native 返回 403 RBAC**: 二组 key (`sk-a624...`) 没权限。改走 `dashscope.aliyuncs.com/compatible-mode/v1`,路径是 `/chat/completions` 但 Claude 模型响应是 Anthropic 原生 schema。这就是 `AnthropicCompatClient` 的设计起点。
- **shell 里有 SOCKS 代理变量(`all_proxy=socks5://...`) 会让 anthropic SDK 报 `socksio not installed`**: 跑前先 `unset all_proxy http_proxy https_proxy`,或装 `httpx[socks]`。
- **LangGraph `stream_mode="updates"` 子图节点的 update 是 `None`**: 不能直接 `update.keys()`,要 `isinstance(update, dict)` 守卫。
- **wanx2.1-i2v-turbo duration 当前只接受 [3, 5]**: 自动 clamp。要更长换 `wanx2.1-i2v-plus`(支持到 10s)。
- **wanx2.1-i2v-turbo 不返回末帧**: `last_frame_url=None`,链式条件 `prev_last_frame_url` 暂不可用,fanout 也都传 None。
- **wanx2.1-t2i-turbo 不吃 ref images**: 纯 t2i,角色一致性靠 prompt 复述。要参考图条件生成换 `wanx2.1-i2i` / `qwen-image-edit`,API 路径不同,要加新 provider 类。
- **planner LLM 会把 JSON 套 ```json ... ```** : `plan_node` 里有 fence 剥离逻辑。
- **macOS TCC 偶尔会拦截 Claude Code 进程访问 `~/Documents`**: 报 `Operation not permitted`。需要在系统设置→隐私与安全性→文件与文件夹给 Terminal 授权,然后 Cmd+Q 重启 Terminal。

## 运行

Key 和 provider 选择都从项目根的 `.env` 加载(`providers/__init__.py` 在工厂构造前 `load_dotenv()`)。模板见 `.env.example`,实际值放 `.env`(已 gitignored)。Shell env 优先级高于 .env(`override=False`),CI / 命令行临时覆盖照常工作。

```bash
cp .env.example .env   # 首次, 填入实际 key
unset all_proxy http_proxy https_proxy   # 见 gotchas
uv run python video_ppl.py               # __main__ 默认 dry_run=True
```

真实跑:在 `__main__` 改 `dry_run=False`,或调 `run_pipeline(..., dry_run=False)`。

`.env` 必填:`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`(指向 dashscope compat-mode)、`DASHSCOPE_API_KEY`。可选项见 `.env.example` 注释行。

## 当前未实现 / TODO

- `OpenAIImageClient` (gpt-image-2): stub,接入按 OpenAI Images API
- `SeedDanceClient`: stub
- VLM critic: 直 pass,真实质量门控未接
- Stitch 上传到 OSS: 当前只产 `file:///tmp/...` 本地路径
- 链式 last-frame → first-frame: provider 当前不返回末帧,严格连续性要换模型 + 改 fanout
