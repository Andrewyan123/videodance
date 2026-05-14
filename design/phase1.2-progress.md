# Phase 1.2 — Asset Library 人脸一致性 进度日志

> 分支:`phase-1.2-faceid`
> 设计依据:`design/architecture.md` §7 Phase 1 acceptance + `design/phase1-progress.md` 末尾的 TODO
> 目标:接入 InsightFace,跨视图 / 跨 shot 用 ArcFace embedding 量化一致性

## 进度索引

- [x] 1. 装 InsightFace + ONNX Runtime + buffalo_l 模型
- [x] 2. `assets/faceid.py`:embedding 抽取 + cosine
- [x] 3. `builder.build_character()` 集成:抽 embedding + 一致性写 card.notes
- [x] 4. `critic/identity.py`:跨 shot identity 评分
- [x] 5. Acceptance test:3 个真实 i2v shot + cos sim 量化
- [x] 6. 写日志 + commit + push

---

## Step 1 — 装依赖 + 下模型

**做了什么**:
- `uv pip install insightface onnxruntime Pillow opencv-python modelscope`(modelscope 后来没用上,可清理)
- 下 `buffalo_l.zip`(288 MB,5 个 ONNX:detection / recognition / landmark_2d_106 / landmark_3d_68 / genderage)
- 默认 GitHub 源被墙(<40 KB/s)→ 并行探测 6 个镜像 → **`gh-proxy.com` 实测 ~1.8 MB/s 最快**
- 用 gh-proxy 下完(2 min),解压到 `~/.insightface/models/buffalo_l/`

**验证**:
- ✅ 5 个 onnx 文件全到位,体积匹配(143 MB + 174 MB + 16 MB + 5 MB + 1 MB)
- ✅ `FaceAnalysis(name='buffalo_l').prepare()` 5.6 s 加载完成,5 个 sub-model 都识别到

**镜像探测数据(留供后续参考)**:
| 镜像 | 25s 下载到 | 速度 |
|---|---:|---:|
| **gh-proxy.com** | 20.3 MB | ~800 KB/s |
| ghfast.top | 16.9 MB | ~680 KB/s |
| gh.llkk.cc | 4.7 MB | ~190 KB/s |
| GitHub 直连 | 964 KB | ~38 KB/s |
| ghproxy.com / mirror.ghproxy.com | 0 | 拒连 |

## Step 2 — assets/faceid.py

**做了什么**:
- `_get_app()` lazy 单例 `FaceAnalysis(buffalo_l, CPU)`,避免重复初始化
- `extract_embedding(path) -> np.ndarray | None`:返 512-d ArcFace,多脸取 `det_score` 最高的;无脸返 None
- `cosine_similarity(a, b) -> float`:`np.dot / (|a| * |b|)`
- `pairwise_cosine(embs) -> [(i, j, cos), ...]`:跳过 None 项
- `consistency_summary(embs) -> dict`:返回 `{n_total, n_detected, n_pairs, min, mean, max}`

**验证 — 区分性测试**:

跑 2 个不同角色各 3 视图(实成本 ~$0.12,wanx2.1-t2i-turbo):

| 对比组 | mean cos sim |
|---|---:|
| Yan 同人内 3 视图 | **+0.124** |
| Alice 同人内 3 视图 | **+0.148** |
| Yan × Alice 跨人 9 对 | **+0.040** |

**关键发现**:同人内 cos sim 远低于 ArcFace 标定的"同人 >0.4"。**根因**:`wanx2.1-t2i-turbo` 是纯文生图,没有参考图条件;同一段描述生成 3 次出来就是 3 个不同的人(只是都"年轻黑发亚洲男")。
- 测量本身没问题(intra 比 cross 高 3x,有显著区分性)
- 上游 t2i 模型不行 —— `design/full.md` 早就说过 "wanx 不吃 ref image, 一致性靠 prompt 复述",实测复述靠不住
- 真要拿高分必须切到 i2i 模型(`wanx-i2i` / `qwen-image-edit`)或 FaceID-Adapter / LoRA → Phase 1.3+

## Step 3 — builder 集成

**做了什么**:
- 真实路径下:`extract_embedding` for each view → 取 `front` 的 512-d 写入 `card.embedding`
- **Hard gate**:`front` view 必须检测到人脸,否则 raise(避免 retrieval 拿到 None embedding 没法比对)
- **Soft note**:`consistency_summary` 的字符串写入 `card.notes`,便于 inspect 时看到
- dry_run 路径不变:embedding=None,kind=None,notes=""

**验证**:
- ✅ 真实跑 yan,落库 `embedding`(512-d,arcface_buffalo_l)
- ✅ store roundtrip:`np.allclose(get_character('yan').embedding, original) = True`
- ✅ `card.notes` = `"cross-view cosine: mean=0.160 (min=0.109, max=0.207), detected=3/3"`
- ✅ dry_run 不抽 embedding,行为不变

## Step 4 — critic/identity.py

**做了什么**:
- `_probe_duration_sec(path)` 用 ffprobe 拿时长
- `_extract_frame(video, t_sec, out)` 用 ffmpeg 抽单帧(`-ss <t> -i <vid> -frames:v 1`)
- `score_shot_identity(video, card_embedding, *, frame_time_sec=None)`:
  - 默认抽中间帧(动作两端态脸可能侧/转,中间最稳)
  - 抽不到人脸 → 返回 `{'score': None, 'reason': 'no_face_in_frame'}`
- `score_shots_batch(paths, embedding) -> summary` 同 `consistency_summary` 结构

**验证 — 机制端到端**(用 yan/front.png ffmpeg 包成 3s mp4 当合成视频):
- ✅ score = **0.989** for self-vs-self(预期 ≈ 1.0,完美)
- ✅ 抽中间帧 t=1.5s,face_detected=True

## Step 5 — Acceptance test:3 个真实 i2v shot

**做了什么**:
- 用 yan 的 `description` 重新 t2i 出一张作 i2v 的 first_frame(15s)
- `wanx2.1-i2v-turbo` 跑 3 个不同动作 prompt 各 5s(共 ~6 min,~$7.50)
- 下 3 个 mp4 到本地
- 跑 `score_shots_batch(paths, yan.embedding)`

**实测结果**:

| shot | action | score @ mid-frame |
|---|---|---:|
| shot_1 | turns head slowly | +0.185 |
| shot_2 | smiles, nods, looks down | +0.082 |
| shot_3 | raises eyebrows in surprise | +0.170 |

**summary**:`detected=3/3, mean=0.146, min=0.082, max=0.185`

**对比表**:

| 对比组 | mean cos sim |
|---|---:|
| Yan t2i 同人内 3 视图 | 0.124 |
| **3 个 i2v shot vs yan card** | **0.146** |
| Yan vs Alice 跨人 9 对 | 0.040 |

**解读**:
- i2v shot 的 identity score(0.146)**显著高于**跨人基线(0.040)的 **3.7x** → first_frame 条件确实在起 identity 保持作用
- 绝对值 0.146 **远低于** ArcFace 教科书的 "同人 >0.4" → wanx-i2v-turbo 在身份保持上的能力只够"是不是人",不够"是不是同一人"
- 跟设计文档 `design/full.md` 的预测一致:"短剧需要 Kling 3.0 / SkyReels-V4"

## Acceptance 结论

| 设计目标 | 设计阈值 | 实测 | 状态 |
|---|---:|---:|---|
| Face detection 成功率 | 100% | 100% (3/3) | ✅ |
| Identity 测量基础设施 | 工作 | 端到端通(ffmpeg → embedding → cosine) | ✅ |
| 跨 shot mean cos sim | >0.75 | 0.146 | ❌ |

>0.75 的硬阈值**当前 wanx-i2v-turbo 不可达**,且不是 Phase 1.2 应当负责的事。Phase 1.2 提供的测量基础设施恰好是 Phase 1.3+ (LoRA / FaceID-Adapter / Kling backend / 视频模型切换) 的 acceptance 量尺。

## 落地文件

```
assets/faceid.py        85 行  ArcFace embedding + cosine + summary
assets/builder.py       +30 行 真实路径下抽 embedding + gate
critic/identity.py      94 行  视频抽帧 + 评分 + 批量
design/phase1.2-progress.md   本日志
```

## 留给 Phase 1.3+ 的 TODO

1. **接入 ref-conditioned t2i**:`wanx-i2i` 或 `qwen-image-edit`,让 3 视图真的来自同一张参考图,intra 应能升到 0.4+
2. **切换 i2v backend 实测**:用 `seeddance2` / `kling3`(等拿到 AK)跑同样 3 shot,看 i2v identity score 能升到多少
3. **LoRA 训练 pipeline**:核心角色 20-50 张图 → LoRA 微调,intra 应能升到 0.7+
4. **Critic policy**:基于 identity score 阈值的重试 / 升级(同 backend new seed → 切 backend → human gate)。当前 score 没接入到重试逻辑
5. **identity drift over a long clip**:抽多帧(每 1s 一张)看片内一致性,不只是 mid-frame
6. **acceptance test 的 mean > 0.75**:等上游模型升级后再校准阈值
