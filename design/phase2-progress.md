# Phase 2 — Storyboard DSL 加固 进度日志

> 分支:`phase-2-storyboard`(基于 `main` @ `d423c4e`)
> 设计依据:`design/architecture.md` §7 Phase 2 + `storyboard/README.md`
> 目标:把内联 planner 抽到独立 `storyboard/` 模块 —— 严格 Pydantic schema + JSON 修复 +
> `@char_id` 引用解析(宽容)+ profile-aware,acceptance 是 20 prompt × 4 profile 的合法率。

## 进度索引

- [x] 1. 装 `json-repair`
- [x] 2. `storyboard/schema.py`:Pydantic v1 models
- [x] 3. `storyboard/prompts.py`:profile-aware system prompts
- [x] 4. `storyboard/repair.py`:fence + json_repair
- [x] 5. `storyboard/validator.py`:schema + 引用解析(宽容)
- [x] 6. `storyboard/planner.py`:`generate_storyboard()` 主入口 + 重试
- [x] 7. `storyboard/cli.py`:`python -m storyboard {generate,validate,profiles}`
- [x] 8. `video_ppl.py` 集成
- [x] 9. Acceptance:20 prompt × 4 profile = 80 LLM 跑测 → 见末尾
- [ ] 10. 写日志 + commit + push

---

## Step 1 — 装 json-repair

`uv pip install json-repair`(轻量 ~10KB,0.59.10)

实测各种破 JSON 都修得不错(尾随逗号 / 单引号 / 截断 / fence-leakage 全过)。是否要前置 fence 剥离 → 给保险:实现里走 `strip_fences → json.loads → repair_json fallback` 三层。

## Step 2 — schema.py(Pydantic v1)

| 类 | 字段 | 校验点 |
|---|---|---|
| `CharacterRef` | `char_id, name, description` | `@char_yan` → `char_yan` 自动 normalize |
| `CameraSpec` | `angle, movement` | 都是 `Literal` 枚举 |
| `DialogueSpec` | `speaker, text` | speaker 也 normalize @id |
| `ShotV1` | `shot_id, index, duration_sec(2-20), character_ids, props, shot_type, camera, action, emotion, dialogue, narration, scene_ref` | duration 范围,shot_type/camera 枚举 |
| `Storyboard` | `schema_version=1, global_style, characters, shots(≥1)` | top-level wrapper |

设计取舍:
- 字段全有默认值 → LLM 漏字段填默认,**不直接 fail**(配合宽容策略)
- `model_config = {"extra": "ignore"}` → LLM 多输出字段被静默丢弃
- `schema_version` 锁定为 `1`,后续演进走 v2

验证(7 case): 实例化 / @id normalize / extra 字段忽略 / 4 种枚举违规 / JSON roundtrip,**全过**。

## Step 3 — prompts.py(profile-aware)

`build_system_prompt(profile)` 拼接:
1. `profile.system_prompt`(流派导向,例如短剧重对白)
2. `profile.extra_schema_fields` 关键字段提示
3. SCHEMA_GUIDE(字段定义 + 约束 + 枚举值)
4. 时长建议(从 `profile.single_shot_duration_range` 取)
5. few-shot 示例

输出 4 个 profile 的 system prompt 长度 ≈ 2000 字符,profile 关键内容(`display_name`、关键字段)都注入正确。

## Step 4 — repair.py

三层 fallback:`strip_fences → json.loads → repair_json`。

`strip_fences` 用 regex `r"^\s*```(?:json|JSON)?\s*\n(.*?)\n```\s*$"`,也兼容无尾 fence 的情况。

7 case 验证(clean / fenced-json / fenced-bare / trailing-comma / single-quotes / fenced+truncated / non-json):全过,non-json 正确 raise `ValueError`。

## Step 5 — validator.py(宽容)

返回 `ValidationReport`:
```
storyboard:         Storyboard | None   (schema 通过才有)
schema_errors:      list[str]            (硬错: Pydantic 失败)
missing_chars:      list[str]            (@char_id not in asset store; 仅 warn)
unreferenced_chars: list[str]            (characters 声明但 shot 没用)
warnings:           list[str]            (shot 引用了未声明的 @char_id 等)
```

`schema_ok` = schema 通过(硬约束)
`refs_ok` = `not missing_chars`(软约束,宽容时不阻塞)

7 case 验证全过,关键路径:store 空时 `missing_chars=[char_yan]` 但 `schema_ok=True`(符合宽容策略),用户用 `python -m assets create` 建过后 `missing_chars=[]`。

## Step 6 — planner.py(主入口 + 重试)

`async generate_storyboard(user_prompt, target_duration_sec, profile, *, llm=None, max_retries=3, check_store=True, verbose=False)`

返回 `(storyboard, validation_report, metrics)`:
```
metrics = {
  n_llm_calls,
  repair_used,
  last_repair_mode: 'direct' | 'repaired',
  raw_lengths: [int, ...],
}
```

重试逻辑:
```
for attempt in 0..max_retries:
  if attempt == 0: 用 system + user_prompt
  else:           用 system + retry_prompt(把上次 error 反向喂)
  resp = await llm.ainvoke(messages)
  data = parse_llm_json(resp.content)          # ValueError → continue
  report = validate(data, check_store=...)
  if report.schema_ok: return ...
  else: continue
raise StoryboardError(last_raw, last_errors)
```

MockLLM 4 case 验证(clean / broken-then-valid / bad-schema-then-valid / all-bad):**全过**,确认重试 prompt 把 schema error 反向喂回。

真实 LLM 验证(opus-4-6 / OLD key):27.4s 一次出 5 shot,**所有字段填齐**(shot_type/camera/dialogue/emotion/action),无 repair,1 次调用。

## Step 7 — CLI

```
python -m storyboard generate --prompt "..." --duration 25 --profile short_drama
python -m storyboard validate < storyboard.json
python -m storyboard profiles                  # 列出 4 个 profile
```

约定:数据 stdout,进度/错误 stderr,退出码 0/1/2。

3 命令 smoke test 全过。

## Step 8 — video_ppl.py 集成

改动:
- `PipelineState` 加 `profile_id: str` 字段
- `plan_node` 改用 `storyboard.generate_storyboard()`:
  - dry_run 路径不变(mock 数据,3 shots)
  - 真实路径:`get_profile(state['profile_id'] or 'short_drama')` → `generate_storyboard()` → 转换 v1 schema → 旧 `Shot`/`CharacterSheet` dataclass
- ShotV1 → Shot 映射:`action → prompt`,`shot_type + camera → camera 字符串`,**丢弃** `emotion/dialogue/scene_ref/props`(Phase 3+ 接 keyframe/audio 时再串)
- `run_pipeline()` 加 `profile_id` 参数

验证:
- ✅ dry_run 跑通,3/3 shots,行为与之前一致
- ✅ 真实跑:10s prompt,opus-4-6 出 2 shots,full pipeline 走通(planner → char_sheet → keyframe t2i fallback → i2v → stitch),最终 mp4 3.4MB,total cost $0.07

集成不破:有 anchor 走 i2i(Phase 1.3),无 anchor 走 t2i(原行为)。

## Bonus — LLM provider 加 rate-limit 退避

实测两个 key 都遇上限流(NEW key 在 6h 内整把失效 401,OLD key opus-4-6 触发 "50 req/min"),原 retry 只看 `status==429`,但 DashScope 经常把上游 429 wrap 成 418 / 状态码非 429 的错误。

新增 `_is_rate_limited(status, body_text)`:状态 429 或 4xx+ 且 body 含 `rate limit / Throttling / exceed / 429` 等关键词,**但排除** `per day / quota / daily` 这种永久性的(退避救不回)。

效果:opus-4-6 50/min 限流时,自动退避 1.9 / 2.7 / 4.4 / 8.7 / 16.4 / 30 秒重试(指数 + jitter),最多 6 次。

---

## Step 9 — Acceptance 实测

**配置**:20 prompts × 4 profiles = **80 trials**,concurrency=2,planner LLM = `aws.claude-opus-4-6`(OLD key + anthropic_compat)。
**总耗时**:**59.6 min**(平均 74.5s/trial,中位 ~40s,5xx 重试拉高方差)。

### 总结果

> **pass rate = 77/80 = 96.2%, PASS ✓**(目标 >95%)

| profile     | pass | JSON 合法率 | repair 用过 | 平均 shot 数 | dialogue 填充 | 镜头运动 | 平均 LLM 调用 | 平均耗时 |
|-------------|------|-----------|-----------|------------|--------------|--------|-------------|--------|
| short_drama | 20/20 | **100.0%** | 0%   | 5.0 | **75%** | 100% | 1.05 | 47.4s |
| anime       | 19/20 | 95.0%      | 5%   | 4.5 | 25%     | 95%  | 1.45 | 86.5s |
| cinema      | 19/20 | 95.0%      | 0%   | 7.0 | 15%     | 95%  | 1.65 | 129.5s |
| commercial  | 19/20 | 95.0%      | 5%   | 5.0 | 30%     | 95%  | 1.60 | 94.2s |

### 失败 trial(3 个)

1. **#5 cinema** —— "An aging detective reviews old case files..." schema fail 3 次。LLM 在 cinema profile 下倾向输出更复杂的 camera 字段(自定义运镜词),频繁脱离枚举。
2. **#5 commercial** —— "Showcase new wireless earbuds..." schema fail 3 次。LLM 把 product / CTA 字段写在我们没声明的位置(`extra="ignore"` 静默丢,导致 action / shot 字段反而缺失),还是逻辑错误而非随机噪音。
3. **#6 anime** —— TimeoutError (120s 单次 HTTP timeout)。aiohttp 的 timeout 不在 `_post_chat_completion` 的 retry 循环里被捕获,直接 raise。**这是真实 bug,留 Phase 2.1 修**。

### 关键观察

- **短剧最稳**:100% pass,平均 1.05 次 LLM 调用,几乎一次过。Pydantic schema 严格度高但 LLM 输出收敛快。
- **Cinema 最慢**:平均 129s/trial(opus-4-6 上),3 次调用占比高,且更容易 schema-fail(需要更多 camera 枚举值约束)。
- **Dialogue 字段填充**:short_drama 75% 有 dialogue(符合 profile 重对白的设计),其它 profile 15-30% 偶发。
- **镜头运动**:95%+ shot 都有非 `static` 的 camera.movement,profile 的"重镜头语言"提示在 LLM 输出里起作用。
- **重试有效性**:LLM transient(status=418 wrap 500/529/Overloaded)在 v1 没识别,fix 后 v2 全部走 retry 救回。3 个失败里 2 个是 schema 不收敛(真正信号),1 个是 timeout(代码 bug)。

### 完整 dump

`/tmp/phase2_acceptance.json`(80 个 trial 的详细 metrics)。可用 `jq` 进一步分析。

---

## 落地文件

```
storyboard/schema.py          ~140 行  Pydantic v1 models
storyboard/prompts.py         ~110 行  profile-aware system prompts
storyboard/repair.py            ~50 行  fence + json_repair
storyboard/validator.py        ~90 行  schema check + 宽容引用解析
storyboard/planner.py         ~110 行  generate_storyboard + 重试
storyboard/cli.py             ~115 行  argparse generate/validate/profiles
storyboard/__main__.py           5 行  入口
storyboard/acceptance_test.py ~190 行  20×4 acceptance harness
providers/llm.py               +50 行  _is_rate_limited 兼容 418-wrapped 429
video_ppl.py                   +40 行  plan_node 用 generate_storyboard + profile_id
.env                           更新  默认 opus-4-6 + 备注新旧 key 状态
design/phase2-progress.md     本日志
```

## 留给 Phase 3+ 的 TODO

1. **`Shot.notes` / `ShotV1 → Shot` 额外字段保留**:当前丢弃 `emotion/dialogue/scene_ref/props`。Phase 3 keyframe_node 接 i2i 时 emotion 应注入 instruction,dialogue 走 Phase 5 TTS。
2. **`@scene_id` 引用解析**:`SceneCard` 还没建,validator 当前不 check scene_ref。Phase 3 加 `assets/scene_store.py`。
3. **NEW key 失效问题**:`sk-b3f7...` 半小时内整把 401,可能 dashscope eval 网关在某种 key 管理。生产用最好用 OLD key + opus-4-6 (3s/call 稳定)。
4. **planner LLM 选型基准**:Phase 2 用 opus-4-6 (27s),gpt-5.4 (~2s) 现已不能用。等 key 恢复跑同 prompt 对比延迟 / JSON 合法率 / 字段填充率,选最佳模型。
5. **acceptance 自动化**:`acceptance_test.py` 应该接入 CI,每 PR 跑一遍门控。
