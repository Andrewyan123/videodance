# orchestration/ — LangGraph 编织

> Phase 1+ 完成后,把 `video_ppl.py` 平移过来

## 职责

主流水线图定义、节点函数、state schema。所有节点都是其他 module 的薄包装(节点本身不实现业务)。

## 计划文件

| 文件 | 职责 |
|---|---|
| `graph.py` | `build_main_graph(checkpointer, profile)` + shot subgraph |
| `state.py` | `PipelineState`、`ShotState` TypedDict + reducers |
| `nodes.py` | `plan_node`、`character_sheet_node`、`keyframe_node`、`video_node`、`critic_node`、`stitch_node` |
| `entry.py` | `run_pipeline(...)`、`resume_pipeline(...)` |

## 节点职责对照表(目标态)

| 节点 | 委托给 |
|---|---|
| `plan_node` | `storyboard.planner` |
| `character_sheet_node` | `assets.builder` 或 `assets.store.get_character` |
| `keyframe_node` | `generation.keyframe` |
| `video_node` | `generation.video` |
| `critic_node` | `critic.*` 多维度 + `critic.policy` |
| `audio_node` (新增) | `audio.tts` + `audio.lipsync` |
| `stitch_node` | `post.stitch` |
| `master_node` (新增) | `post.master` |

迁移在 Phase 1 完成后做,**保留 `video_ppl.py` 作为兼容入口**直至所有 module 都实装。
