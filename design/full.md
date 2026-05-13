下面把现在比较成熟、可落地的 AI 视频生成流水线做一个系统梳理，分四块：**整体流程共识、角色一致性技术栈、短剧/动画的差异化路径、框架设计建议**。

---

## 一、当前已经收敛的标准流程：七阶段流水线

到 2026 年初，行业基本收敛到一个七阶段流水线，从剧本创作（GPT-5、Claude、DeepSeek 等 LLM 生成含镜头语言的完整剧本）、角色场景资产建设（SD 训练 LoRA 或 IP-Adapter FaceID 锁定角色一致性）、分镜设计（70% 创作精力集中于此）、视频生成（Seedance 2.0、Kling 3.0、Sora 2 等，每段 5-10 秒）、配音与音效（CosyVoice、ElevenLabs、Suno v4）、后期剪辑合成、到分发与运营。

工业化层面有一个核心范式转移值得注意：创作重心被极大程度前置，创作者将超过 70% 的精力集中在分镜与动态分镜阶段，为后续的 AI 自动化生成提供明确逻辑指令。换句话说，**"分镜即程序"**——分镜阶段产出的结构化指令（角色 ID、机位、景别、运镜、时长、对白、情绪），后面所有 AI 模块都是这份"程序"的执行器。这跟传统影视先拍后剪的逻辑是反过来的。

另一个关键范式是 **Script-to-Asset-to-Keyframe**，摒弃传统的"抽卡式"生成，先生成精准的起始帧和结束帧，AI 在两帧之间生成平滑的视频过渡，所有画面都受到"角色定妆照"和"场景概念图"的约束。这个工作流的本质是把"开放式生成"压缩成"插值生成"，把不确定性从 video 模型转移到更可控的 image 模型。

---

## 二、角色一致性：技术栈三代演进

这是整个流水线最硬的卡点，解决方案经历了三代演进：第一代（2023 年）以 LoRA 训练为主，需为每个角色训练专用模型，耗时较长；第二代（2024 年初）以 IP-Adapter/FaceID 和 InstantID 为代表，无需训练即可保持面部一致；第三代（2024 末至 2025 年）将 PuLID、ADetailer、ControlNet、IP-Adapter FaceID 组合使用，加上 Midjourney V7 Omni Reference 功能，一致性达到 95%，基本满足工业化需求。

技术机制上分三层：

**(1) 身份编码层（identity encoding）**:
- **LoRA 训练**：最稳但最重，单角色 20-50 张图，10-30 分钟训练，绑死身份特征到 U-Net cross-attention 层。适合长期复用的核心角色。
- **IP-Adapter FaceID**：把 InsightFace 提取的人脸 embedding 通过额外的 cross-attention 注入 diffusion 主干，免训练但身份保真度低于 LoRA。
- **PuLID / InstantID**：进一步把 ID embedding 与 ControlNet 风格的 spatial 控制结合，能在保留身份的同时换姿态、换光照。

**(2) 结构控制层**：ControlNet（OpenPose / Depth / Canny）锁定身体姿态与构图，避免身体比例漂移。这一层和身份层正交。

**(3) 全局参考层**：Midjourney V7 的 Omni Reference 功能可以全局锁定面部服饰纹理。Seedance 2.0、Kling Omni 等视频模型已经原生支持 **@图片 N** 引用语法，片段描述中可用 @图片1、@图片2 对应参考图顺序（一般为场景 → 角色 → 物品 → 分镜主图），支持自动生成含运镜、机位与 @图片 约束的文案——这相当于把"角色卡 + 场景卡 + 道具卡"作为视频模型的 conditioning token。

**根本机制问题**：现有 AI 视频系统不像传统动画流水线那样维护固定的身份模型，而是基于 prompts、references 和 scene context 反复重建角色，因此现实目标是感知连续性（perceptual continuity）而非像素完美身份。当前技术依赖"二维图像驱动"，缺乏三维时空建模能力，导致角色在多镜头或复杂运镜下容易出现面容穿帮。这一点跟你做 LLM 的视角很容易类比：现在的视频生成是 stateless 的，每个 shot 都是一次独立 forward；要做长片就得在外部框架里维护 state（即"角色档案"），相当于把 KV-cache 外置成一个 asset library。

---

## 三、AI 短剧 vs AI 漫剧/动画：流程分叉点

两个场景在技术取舍上有明显差异：

**AI 真人短剧（photorealistic）**：
- 难度更高，写实人类角色更难保持一致，许多创作者选择风格化美术（卡通、动画、插画），不仅美学吸引力强，AI 处理起来也更可靠。
- 主流是 **image-to-video** 流水线：先用 Midjourney/Flux/SD3.5 出角色定妆照与场景概念图，再用 Seedance 2.0 / Kling 3.0 / Sora 2 做 I2V，每段 5-10 秒。
- 对脸部修复依赖 ADetailer + face restoration；对长时序依赖首尾帧拼接 + RIFE/SeedVR2 修复。
- 口型同步：SkyReels-V4 实现毫秒级口型对齐，解决音画同步难题。

**AI 漫剧/动画（stylized）**：
- 风格容差大，一致性更容易保持。
- 主流是 **N 宫格分镜直出**：用 NanoBanana Pro + Gemini 3 等模型一次性生成 4/16/25 宫格的连贯分镜，再单独驱动每格做 I2V。这套打法在 ComfyUI 社区已经标准化。
- 适合 Z Image Turbo、LongCat-Image 等中文友好的 image 模型先出帧，LongCat-Image 是美团开源的新一代模型，主打"小参数、强中文、高效率"，对中文指令理解精准，生成的国风、玄幻场景极具美感。
- Flux Kontext 这类支持局部编辑的模型用于角色微调（换衣、改光影）。

**两条路径的公共部分**是分镜结构、资产库、配音、剪辑；**分叉**主要在 image 模型选型与 I2V 模型选型上。所以框架设计时这两层要做成可插拔的 backend。

---

## 四、框架架构设计建议

学术界已经有几个值得参考的多智能体框架：**MAViS**（script writing → shot designing → character modeling → keyframe generation → video animation → audio generation，配合 3E 原则 Explore/Examine/Enhance）、**StoryAgent**（story design, storyboard, video, coordination, observer 五类 agent）、以及最近的 **ScripterAgent + DirectorAgent + CriticAgent** 三件套（用 GRPO 训练 ScripterAgent 对齐专业导演标准，DirectorAgent 跨场景连续生成，CriticAgent 做评估）。

我给你画一下一个工业可落地版本的框架架构：下面把每层的工程要点和你做 ML 训练时关心的几个交叉点过一遍：

### 1) 编剧 + 导演层：把 LLM 当 DSL 编译器用

这层的关键不是"写得漂亮"，而是产出**结构化分镜 JSON**——必须能被下游确定性消费。一个最小可用的 schema 大致是：

```json
{
  "shot_id": "S03-007",
  "duration_sec": 6.5,
  "scene_ref": "@scene_03_office_night",
  "characters": ["@char_01_yan", "@char_02_boss"],
  "props": ["@prop_05_laptop"],
  "shot_type": "medium_close_up",
  "camera": {"angle": "low", "movement": "dolly_in"},
  "action": "yan looks up from laptop, frowns",
  "dialogue": {"speaker": "@char_01_yan", "text": "..."},
  "emotion": "tense",
  "narration": null
}
```

**ScripterAgent 训练**：值得参考 ScripterAgent 用 GRPO 对齐专业导演标准的做法——这正是你熟悉的训练范式。可以用一个 reward model 评估剧本的钩子密度、反转节奏、对白口语化程度、是否符合分镜约束，然后 GRPO group rollout。Reward 来源建议混合：(a) 规则项（长度、JSON 合法性、字段完整性）、(b) LLM-as-judge（用 GPT-5/Claude/Qwen 打分）、(c) 下游 video 模型的可执行性反馈（如果分镜让 video 模型生成失败率高，回扣分）。最后一项是闭环关键。

**强制 JSON 输出**：用 `jsonrepair` + JSON mode + few-shot 兜底（LocalMiniDrama 项目里就是这么做的），避免下游解析炸掉。

### 2) 资产层：让生成变成"有状态的"

这是整个框架的核心 state，相当于把 stateless 的 video model 套上一层 retrieval-conditioning。每个角色应该有：

- **三视图**（正/侧/背 + 至少一张表情参考）
- **LoRA 权重**（对核心角色训，10-30 张图够用；如果是次要角色，跳过 LoRA 只用 reference）
- **FaceID embedding**（InsightFace 提取的 512-d 向量，落库）
- **风格 token**（一段固定 prompt 描述，比如"短发、丹凤眼、黑色西装"）
- **检索 key**：`@char_01_yan` 这样的 ID

分镜里的 `@char_01_yan` 在执行时被 retrieval 替换为 (LoRA 路径, FaceID embedding, 风格 token, 三视图 PNG) 四元组，然后注入到 image / video 模型。这套机制和 RAG 几乎是同构的——你完全可以把它看成"视觉资产的 RAG"。

**冷启动新角色**：用 Flux + IP-Adapter FaceID 先出三视图（不训 LoRA），跑通流水线；如果角色后续使用频次高，再异步训 LoRA 提升一致性，热替换上去。

### 3) 生成层：从"开盲盒"到"有约束的插值"

按 Script-to-Asset-to-Keyframe 的范式，每个 shot 分两步：

**Step 1 - Keyframe generation**：T2I 模型 + 角色 reference + 场景 reference 出**首帧、尾帧**。首尾帧之间要做一致性校验：跑 face recognition 比对，跑 CLIP similarity 比对场景，不通过就重生成。注意首尾帧的 prompt 要写出**动作的两端态**（"她看着窗外" → "她转头看向门口"），而不是中间状态。

**Step 2 - Video synthesis**：I2V 模型用首尾帧 + 动作描述 + 运镜参数生成 5-10s 视频。Seedance 2.0 / Kling 3.0 / Wan2.2 都支持首尾帧条件；Sora 2 不支持首尾帧但物理真实感强，适合特效镜头。

**Backend 路由策略**：根据 shot 元数据自动选模型。比如 `physics_intensive=true` → Sora 2；`human_action=true` → Kling 3.0（行业公认动作大师）；`stylized_anime=true` → Wan2.2 + 漫画 LoRA。这个路由 policy 本身也可以训——把它当成一个 bandit/RL 问题，reward 是 CriticAgent 的评分。

### 4) 音频与口型同步：dual-stream 是关键

普通 TTS + 视频拼接会有口型错位，SkyReels-V4 通过对称双流 MMDiT 架构，视频分支"看"音频，音频分支"听"视频，两者在生成基座层实现绑定，彻底解决音画分离和口型错位。要做长片这一步绕不开，否则观感塌掉一半。开源方案目前是 Wan2.2-Animate 系列做唇音迁移。

### 5) 评审层：CriticAgent 闭环

CriticAgent 要打分至少四个维度：
- **角色一致性**：跨 shot 做 ArcFace embedding 余弦相似度，<0.6 报警
- **场景连续性**：CLIP image-image 比对相邻 shot 背景
- **叙事一致性**：抽取每个 shot 的视觉描述（用 VLM），喂回 LLM 判断是否符合剧本
- **VSA (Visual-Script Alignment)**：ScripterAgent 论文里提的指标，对齐 script 中的镜头语言描述和实际生成画面

不通过的 shot 进入重试队列：先尝试同 backend 换 seed，再尝试换 backend，最后兜底为人工干预。

---

## 五、几个需要警惕的工程坑

1. **角色漂移的累积效应**：长视频里角色看起来在前几个 clip 很完美，到第 5 个 clip 头发颜色变了，第 10 个面孔微妙偏移，第 20 个就是另一个人——视频越长，漂移越严重。所以**永远不要让 shot N 的输出作为 shot N+1 的输入**，永远从资产库重新取参考。

2. **数据存储**：分镜 JSON、关键帧、视频片段、音频段、最终成片要有清晰的版本管理。LocalMiniDrama 把所有数据存在本地浏览器（无后端），适合个人创作；工业级要上对象存储 + 元数据 DB。

3. **算力调度**：单段视频生成 1-3 分钟，一集短剧 50-100 个 shot，串行跑要几小时。需要 AI 并发生成：一键生成支持图片/视频并发（默认各 3 路），同时处理多个角色/场景/分镜任务。多 backend 异步队列 + 失败重试是基操。

4. **当前技术天花板**：67% 的创作者认为角色一致性是制作 AI 短剧最大的难题，AI 对 100 分钟以上的长叙事把控能力弱，常出现"搭积木式叙事"。框架设计上别试图一次生成长片，老老实实做"原子 shot + 拼接"，把长叙事一致性交给资产库和 CriticAgent 来保证。

5. **跟你 Qwen / 多模态方向的交叉点**：这套框架里 ScripterAgent、DirectorAgent、CriticAgent 三个 agent 都是典型的 SFT + RL 训练对象，正好可以用你们组的 agentic 训练 pipeline（synthetic task generation + progressive SFT+RL）做基座；尤其 CriticAgent 是天然的 VLM verifier，跟 RLVR 的思路高度一致——把生成的 shot 作为 trajectory，CriticAgent 给 reward，反过来训 ScripterAgent 和 DirectorAgent 的 routing policy，闭环就跑起来了。这是一个很自然的 agentic 训练 benchmark。