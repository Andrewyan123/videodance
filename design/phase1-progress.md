# Phase 1 — Asset Library MVP 进度日志

> 分支:`phase-1-asset-library`
> 设计依据:`design/architecture.md` §7 Phase 1 + `assets/README.md`
> 范围(本次完成):schema / store / builder / CLI / video_ppl 集成
> 范围(留 Phase 1.2):InsightFace 一致性校验、LoRA 训练

## 进度索引

- [x] 0. 切分支 + 加 `data/` 到 .gitignore
- [x] 1. Schema: CharacterCard
- [x] 2. Store: SQLite + 文件系统
- [x] 3. Builder: T2I → 多视图 → 入库
- [x] 4. CLI: create/list/inspect/delete
- [x] 5. video_ppl.py 集成: `@char_id` retrieval
- [x] 6. 综合验证 + commit

---

## Step 1 — Schema (assets/schema.py)

**做了什么**:
- `CharacterCard` Pydantic v2 模型,字段:`char_id`、`name`、`description`、`style_tokens`、`ref_image_urls`、`required_views`、`embedding` + `embedding_kind`(Phase 1.2 填)、`profile_id`、`schema_version`、`created_at`、`updated_at`、`notes`
- `SCHEMA_VERSION = 1` 模块常量,用于后续 schema 迁移
- `touch()` 方法手动更新 `updated_at`

**验证**:
- ✅ 实例化:`CharacterCard(char_id, name, description, ...)` 通过
- ✅ JSON round-trip:`model_dump_json` → `model_validate_json` 完全一致
- ✅ 可变性:`touch()` 后 `updated_at` 推进
- ✅ Validation 错误:缺少 `name`/`description` 被 Pydantic 拒绝(2 validation errors)
- ✅ `schema_version=1` 写入实例

## Step 2 — Store (assets/store.py)

**做了什么**:
- 路径布局:`./data/assets.db`(SQLite)+ `./data/assets/<char_id>/*.png`。`VIDEO1_DATA_DIR` env 可覆盖根
- DB schema:`characters` 表只存 `char_id / name / profile_id / payload(整 JSON) / created_at / updated_at`,字段不展开,**schema 演进无需 ALTER TABLE**
- API:`upsert_character` / `get_character` / `list_characters(profile_id=None)` / `delete_character(..., delete_assets=True)`
- 索引:`idx_characters_profile` 用于按 profile 筛选
- `reset_for_test()` 给测试用

**设计取舍**:
- 文件系统是 ground truth,SQLite 是索引。后续 phase 加 `rebuild_index()` 从文件系统反推 DB
- 同步 sqlite3(非热路径,不需要 async)
- 单机本地,不考虑并发写(MVP scope)

**验证**(用 `VIDEO1_DATA_DIR=/tmp/video1_test_store` 隔离):
- ✅ 空库 `list` 返回空
- ✅ `upsert` + `get` round-trip
- ✅ asset dir `/tmp/.../assets/char_yan_01` 自动创建
- ✅ 同 id `upsert` 覆盖,`updated_at` 单调递增
- ✅ 多 character + profile 过滤(`list_characters(profile_id="short_drama")`)
- ✅ `delete` 存在的返 True,不存在的返 False
- ✅ 删除时连带清理资产目录(空目录无文件残留)

## Step 3 — Builder (assets/builder.py)

**做了什么**:
- `build_character(char_id, name, description, *, profile_id, style_tokens, angles, dry_run)` 异步函数
- 流程:`build_image_provider()` 为每个角度调 T2I(顺序,避 QPS)→ 并发下载到 `data/assets/<char_id>/<angle>.png` → 构造 `CharacterCard` → `store.upsert_character`
- `_ANGLE_TO_PHRASE` 字典分离"文件名安全 ID"(`front/side/3-quarter/back`)和"T2I prompt 短语"(`front view / 3/4 view / ...`)。`prompts.CHARACTER_ANGLES` 的短语带 `/` 直接当文件名会被当目录,这是踩到的第一个坑
- `dry_run=True`:不调 T2I,落 67 字节最小合法 PNG 占位,流程其他部分照常
- 失败原子性:任一步出错 → 清理本次落地的文件(不残留半成品)
- Overwrite 模式:同 char_id 重建会先清空旧文件

**验证 1 — dry_run**:
- ✅ 3 个角度文件命名正确:`['3-quarter.png', 'front.png', 'side.png']`
- ✅ 文件存在且非空(占位 PNG)
- ✅ Overwrite 后 store 里 name/description 都更新,文件个数不变
- ✅ 无效 angle(`'top', 'bottom'`)被 `ValueError` 拒绝,错误消息列出支持列表

**验证 2 — 真实 T2I**:
- ✅ DashScope `wanx2.1-t2i-turbo` 跑了 27.1s 出 3 张图
- ✅ 文件 282-415 KB,`file` 命令确认:`PNG image data, 1280 x 720, 8-bit/color RGB`
- ✅ 成本估算 ~$0.06(3 × $0.02)

## Step 4 — CLI (assets/cli.py + assets/__main__.py)

**做了什么**:
- argparse 4 个子命令:`create / list / inspect / delete`
- `python -m assets create --char-id ... --name ... --description ... [--profile ...] [--style-tokens ...] [--dry-run]`
- `python -m assets list [--profile ...]` 表格输出
- `python -m assets inspect <char_id>` 输出整 JSON
- `python -m assets delete <char_id> [--keep-files]` 默认删 DB + 文件,`--keep-files` 只删 DB

**约定**:
- 进度信息走 stderr,数据走 stdout(便于 `| jq` 处理 inspect 输出)
- 退出码:0 ok / 1 业务错(not found) / 2 usage 错

**验证**:
- ✅ `create --dry-run` 出 card,JSON 完整
- ✅ `list` 表格对齐,filter `--profile` 工作
- ✅ `inspect` 存在返回 JSON,不存在 exit 1 + 错误消息走 stderr
- ✅ `delete` 真删 + 后续 `list` 不再出现

## Step 5 — video_ppl.py 集成

**做了什么**:
- `character_sheet_node` 多一步 `asset_store.get_character(char_id)`:
  - **Cache hit**: 用 store 里的 `ref_image_urls`(本地 file:// 路径,不会过期),跳过 T2I,打 `[character_sheet] cache hit <id>` 日志
  - **Cache miss**: 走原 in-pipeline T2I 生成路径(临时 URL,**不写回 store**)
- 不自动写库:DashScope T2I 返回的 URL 有过期时间戳,缓存会失效。让用户主动 `python -m assets create` 持久化建档,确保是本地 file:// 路径

**设计取舍**:
- 集成是**纯加法**的 —— 旧 pipeline(无 store)行为完全不变,只多了一次 store 读
- 没有改 `CharacterSheet` dataclass 或 planner 输出 schema,降低耦合
- store 读是同步 sqlite3 调用,在 async 上下文里 OK(微秒级)

**验证**:
- ✅ Cache miss(空 store + dry_run):3/3 shots 完成,无 cache hit 日志
- ✅ Cache hit(`python -m assets create --char-id alice ...` 先建档 → 跑 pipeline 看到 `cache hit alice (3 refs from store)`),3/3 完成
- ✅ 两种路径结果一致,集成不破原 pipeline

## Step 6 — 综合验证

**做了什么**:
- 跑了 `from assets import schema, store, builder, cli` + `from profiles import get_profile` 完整 import test
- 跑了一次干净环境的 `video_ppl.py` dry_run,3/3 shots 完成
- `git status` 检查,无 `.env` / `data/` / `__pycache__` 误入版本控制

**Phase 1 MVP 完成定义对照**(`design/architecture.md` §7):
- ✅ CharacterCard schema 落地
- ✅ SQLite store 落地
- ✅ 创建 CLI 可用
- ✅ 引用解析:`@char_id` 在 store 中存在时走 retrieval

**Acceptance(face cos sim >0.75)推迟到 Phase 1.2**:本次 MVP 跳过 InsightFace 接入,所以 identity 相似度量化暂未实现。下一步引入 InsightFace + ArcFace embedding 后做 5-shot 一致性测试。

## 落地文件清单

```
assets/schema.py        99 行  Pydantic CharacterCard + SCHEMA_VERSION
assets/store.py        119 行  SQLite + 文件系统 CRUD
assets/builder.py       96 行  T2I → 多视图 → 入库 + 失败回滚
assets/cli.py          108 行  argparse create/list/inspect/delete
assets/__main__.py       4 行  `python -m assets` 入口
design/phase1-progress.md      本日志
.gitignore             +1 行   `data/` 加入排除
video_ppl.py           +12 行  character_sheet_node 增加 store cache lookup
```

## 给 Phase 1.2 留的 TODO

- `assets/faceid.py`:`extract_embedding(image_path) -> 512-d list[float]` + `cosine_sim(emb1, emb2)`
- `CharacterCard.embedding` 字段在 `builder.build_character()` 真实路径里自动填(选 front view 抽 ArcFace)
- 跨视图一致性校验:3 视图两两 cosine >0.8 才入库,否则 reject
- 跨 shot identity 评分:从生成视频抽帧 → embedding → 对比 card.embedding
- 依赖 `insightface` + `onnxruntime`(~300MB 模型,装的时候确认网络)


每一步包含:**做了什么 → 验证方式 → 验证结果**。
