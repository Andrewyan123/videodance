# Phase 5.2 — Frontend Wizard 进度日志

> 分支:`phase-5.2-frontend-wizard`(基于 main @ `0c950c8`)
>
> Phase 5.1 的后端 8 个端点已就位,本 phase 把它们包装成 4 步可视化 wizard:
>   1. INPUT (prompt + profile + duration)
>   2. CHARS (每个角色一行 N 张候选,点击挑选)
>   3. SHOTS (CSS grid 树状视图,shot 列 × 候选行,小视频预览)
>   4. RESULT (拼接 + 下载)

## 进度索引

- [x] 1. 老 frontend 移到 `auto.*` + 新写 wizard 三件套
- [x] 2. server.py 加 `/api/asset` 服务 character ref 图 + 拼接 mp4
- [x] 3. SSE 集成:wizard 订阅 events 显示 propose 进度
- [x] 4. 本地 smoke:静态文件 + 端点 + path traversal 防御
- [x] 5. 写日志 + commit + push

---

## Step 1 — frontend 文件结构

```
frontend/
├── index.html      ← 新, wizard 入口
├── wizard.js       ← 新, 状态机 + API 调用 + SSE 订阅
├── wizard.css      ← 新, 步骤指示器 + char grid + shot tree
├── auto.html       ← 从 index.html 移过来 (旧 auto 模式 UI 保留备用)
├── auto.js
└── auto.css
```

`auto.*` 仍可用(`http://.../auto.html`),走老的 `mode=auto` POST /api/runs 单步全跑流程。

## Step 2 — UI 设计

### 步骤指示器(顶部)

```
1. 输入  →  2. 角色  →  3. 镜头  →  4. 成片
   ●        ○          ○           ○
```

当前步显示蓝色,已完成步显示绿色。

### Step 1: INPUT

简单表单:`prompt textarea + duration + profile dropdown + dry_run checkbox + Start`。

### Step 2: CHARS

`storyboard.characters` 一个一个 block 展示:

```
Yan @char_yan
  young Chinese man, 25 years old, short black hair...
  [img v0★] [img v1] [img v2] [img v3]
  抽 [3] 个   [生成候选]   variant 0: t2i...
```

每个 char 都有"再抽 N 个"按钮 + 数量输入框(默认 3,可调 1-8)。点缩略图 = `/select?variant=K`,UI 上 ★ 标记。

### Step 3: SHOTS(CSS grid tree)

横向布局,每个 shot 一列,column 内垂直堆叠候选小视频:

```
┌─ Shot 0 S01-001 ─┐  ┌─ Shot 1 S01-002 ─┐  ┌─ Shot 2 S01-003 ─┐
│ 5s · Yan walks... │  │ 5s · Yan opens.. │  │ 5s · Yan smiles. │
│                   │  │                  │  │                  │
│ [▶ video v0]      │  │ [▶ video v0] ★   │  │ [▶ video v0]     │
│  id=0.42          │  │  id=0.51         │  │  id=0.39 (red)   │
│                   │  │                  │  │                  │
│ [▶ video v1] ★    │  │ [▶ video v1]     │  │ [▶ video v1]     │
│  id=0.48          │  │  id=0.46         │  │  id=0.55 ★       │
│                   │  │                  │  │                  │
│ 抽 [2] 段 [生成]  │  │ 抽 [2] 段 [生成] │  │ 抽 [2] 段 [生成] │
└───────────────────┘  └──────────────────┘  └──────────────────┘
```

- 鼠标 hover 上视频 → 自动播放(`vid.play()` on mouseenter)
- 点 = `/select`
- identity score 显示颜色:< 0.30 红字提示 wanx 拉胯,>= 0.30 绿字
- failed variant(`error` 字段)显示红色边框 + 错误消息

CSS:`.tree-cols { display: grid; grid-auto-flow: column; grid-auto-columns: 220px; }` + `overflow-x: auto`,自动横向滚动。

### Step 4: RESULT

```
[▶  final video player]

3 shots    18.4 MB    [下载]

[← 改选择]  [Start over]
```

## Step 3 — SSE 进度集成

`wizard.js` 在 POST /api/runs 拿到 thread_id 后立即 `new EventSource('/api/runs/{tid}/events')`。后端的 `propose_*_progress` / `done_variant` / `complete` / `error` 事件会推到这条流,前端在对应 char/shot 的 progress span 显示:

```
variant 0: t2i
variant 0 done
variant 1: t2i
...
完成 4 个变体
```

shot propose 因为耗时长(~10 min/段),用户能实时看到 "variant 0: keyframe" → "variant 0: i2v seed=42" → "variant 0: score" → "variant 0 done id=0.42",体验比"转圈 10 分钟"好得多。

## Step 4 — Server.py 加 `/api/asset`

Phase 1.2 generated 的 character 参考图路径是 `file:///private/tmp/.../front.png`,浏览器不能直接 load file://。新增端点:

```python
@app.get("/api/asset")
async def serve_asset(path: str = Query(...)):
    # 白名单允许两个根:
    #   STITCH_OUT_DIR  (拼接 mp4)
    #   VIDEO1_DATA_DIR (角色 png + 候选数据)
    real = os.path.realpath(path)
    if not any(real.startswith(root + os.sep) ...):
        raise HTTPException(403)
    # 自动推 mime: .png/.jpg/.mp4 → image/* | video/*
```

`wizard.js` 的 `resolveLocalUrl()` 把 `file://...` 转成 `/api/asset?path=...`,自动适配。

### 安全验证

```
/etc/passwd                       → 403  ✓
../../../../etc/passwd            → 403  ✓
$HOME/.ssh/id_ed25519             → 403  ✓
/tmp/video1.../nope.png (合法但缺) → 404  ✓
```

## Step 5 — Smoke 测试

无成本验证,server 起在 8002 端口,VIDEO1_DATA_DIR 隔离:

| 测试 | 结果 |
|---|---|
| GET `/` `/index.html` `/wizard.js` `/wizard.css` | **200** OK |
| GET `/auto.html` `/auto.js` `/auto.css`(legacy 仍可用) | **200** OK |
| POST `/api/runs` interactive dry_run | **OK**,返 mock storyboard:1 char `alice`, 1 shot `S01` |
| GET `/api/runs/{tid}/tree` | OK,`status=proposing`, candidates 都是空(预期) |
| `/api/asset` 三种 path traversal 攻击 | 全 **403** |
| `/api/asset` 合法路径但文件不存在 | **404** |

**没跑真实 propose 流程**(那要 ~$6 + 12 min,Phase 5.1 acceptance 已经端到端验证过后端)。Phase 5.2 的 wizard 只是包装层,只要静态文件 + API shape 对就 OK。

## 落地文件

```
frontend/index.html         87 行   wizard 4 步 markup
frontend/wizard.js         323 行   state machine + API + SSE
frontend/wizard.css        233 行   步骤指示器 + char grid + shot tree
frontend/auto.html         [renamed from index.html, 引用改 auto.*]
frontend/auto.js           [renamed from app.js]
frontend/auto.css          [renamed from style.css]
server.py                  +30 行   /api/asset 新端点 + 白名单
design/phase5.2-progress.md 本日志
```

## 用法

```bash
# 起 server
unset all_proxy http_proxy https_proxy
uv run uvicorn server:app --host 127.0.0.1 --port 8000

# 浏览器
http://127.0.0.1:8000/         # 新 wizard (creative mode)
http://127.0.0.1:8000/auto.html # 旧 auto 模式 (legacy, 单步全跑)
```

## 留给后续 phase 的 TODO

1. **进度条更细**:propose_shot 单 variant 内的 i2i / i2v / score 三个 stage,目前只在 progress span 显示文字。可以做成进度条 + 预计剩余时间
2. **失败 variant 重抽**:目前红框显示但需要点"再抽 N 个"。可以加每格的"重抽本格"按钮
3. **多角色拖拽排序**(用户多角色场景下,可视化哪个角色出现在哪个 shot)
4. **拼接前的 timeline 预览**:选完所有 shot,在 stitch 前展示一个 timeline 让用户检查顺序
5. **dry_run 全链路**:propose builder 加 dry_run 支持,让前端能完整体验所有 4 步而不烧钱
6. **session 持久化**:刷新页面 / 关浏览器后,可以通过 thread_id 恢复进度
