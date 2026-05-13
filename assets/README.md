# assets/ — 资产库

> Phase 1 (待实现)

## 职责

把"无状态"的视频模型套上一层 retrieval-conditioning。所有角色/场景/产品都先在这里建档,storyboard 里只引用 `@char_id` / `@scene_id`,下游执行时从这里取参考。

## 计划文件

| 文件 | 职责 |
|---|---|
| `schema.py` | `CharacterCard`、`SceneCard`、`ProductCard` Pydantic 模型 |
| `store.py` | SQLite 元数据 + `data/assets/<id>/*.png` 文件系统 |
| `faceid.py` | InsightFace 抽 512-d ArcFace embedding + 跨视图一致性校验 |
| `builder.py` | T2I → 多视图 → 校验 → 入库 的工作流 |
| `cli.py` | `python -m assets create --prompt ... --name ...` |

## 接口 (草案,Phase 1 落地时定稿)

```python
from assets import store

# 创建
card = store.create_character(name="yan", description="...", profile=anime_profile)
# 引用
card = store.get_character("@char_yan")
# 返回三视图 URL 列表 + embedding + style tokens
```

## 注意

- 仅本地存储:`data/assets.db` + `data/assets/<char_id>/*.png`
- 不做 OSS 上传 / 多机同步
- InsightFace 依赖在 Phase 1.2 接入,Phase 1.1 先用 CLIP 余弦占位
