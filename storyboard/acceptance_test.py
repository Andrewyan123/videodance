"""Phase 2 acceptance test: 20 prompts × 4 profiles = 80 LLM 跑测.

跑出来统计:
  - JSON 合法率 (Pydantic schema 通过率)
  - 引用解析 missing 率 (@char_id 不在 store, 宽容: warn 不 fail)
  - 平均重试次数 (n_llm_calls 分布)
  - 平均 shot 数 / 时长
  - 字段覆盖率 (shot_type, dialogue, emotion 等是否填充)

设计目标 (design/architecture.md §7 Phase 2):
  - JSON 合法率 > 95%
  - 引用解析失败率 < 5% (但 store 空的情况下当然 100% missing, 这是输入问题非 LLM 问题)

预算: 80 calls × ~27s = ~36 min (concurrency=2 减半)
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field

from profiles import get_profile
from storyboard.planner import StoryboardError, generate_storyboard


# --- 20 个测试 prompts ---

TEST_PROMPTS = [
    # === 短剧风(中文,对白) ===
    "主角 Yan 在咖啡馆等女友, 等了 5 分钟开始焦虑, 然后对方匆匆赶到道歉",
    "Yan 收到分手短信, 雨夜独自走在街头, 路过一家旧书店停下脚步",
    "母亲打电话问 Yan 什么时候回家过年, 他犹豫片刻后答应",
    "Yan 在地铁上偶遇前任 Lin, 两人尴尬对视, Lin 先开口打招呼",
    "Yan 加班到深夜, 收拾东西时发现同事 Lin 留下的一杯热咖啡和便签",
    # === 漫剧风(英文/中文,氛围) ===
    "A young witch discovers her familiar cat is missing, searches through her magical garden at twilight",
    "A samurai stands on a cliff at sunrise, drawing his katana slowly as wind blows cherry blossoms",
    "少女在课桌上偷偷画漫画, 老师走近时她慌忙合上, 同桌偷瞄一眼笑了",
    "A robot watches the rain through a window of a deserted city, then walks outside to feel it",
    "山间小屋的少年烤着栗子, 忽然听见门外传来狐狸的叫声, 他打开门看见雪地里的小狐狸",
    # === 电影风(英文,镜头语言) ===
    "An aging detective reviews old case files alone in his office, suddenly notices a connection between two seemingly unrelated photos",
    "A team of astronauts on Mars discovers an alien structure, the leader hesitates before entering",
    "A widow returns to her childhood home decades later, walks through empty rooms hearing distant echoes of laughter",
    "A jazz pianist plays for an empty club at 3am, a mysterious stranger enters and listens silently",
    "A father teaches his son to ride a bike at sunset, the camera pulls back to reveal they are on a rooftop",
    # === 广告片风(中文/英文,产品) ===
    "Showcase a new wireless earbuds product: a runner in the city, music transports them to a forest, then back to the city, with the tagline 'Sound, Anywhere'",
    "一杯新口味咖啡, 上班族在繁忙街头喝下第一口, 整个世界慢下来, 露出微笑",
    "Athletic shoes ad: a basketball player goes through training, missed shots, then finally hits the winning three-pointer",
    "智能手表新品发布: 运动员晨跑、心率监测、回家拥抱家人、夜晚睡眠监测, 24 小时陪伴你",
    "Luxury skincare ad: a woman wakes up, applies the cream, walks confidently into a meeting room, with elegant slow-motion",
]

PROFILE_IDS = ["short_drama", "anime", "cinema", "commercial"]


# --- 配置 ---

CONCURRENCY = 2   # 同时 2 个 LLM 调用 (opus-4-6 有 50/min 限流)
DURATION_BY_PROFILE = {  # 给每个 profile 一个合理的目标时长
    "short_drama": 25.0,
    "anime": 20.0,
    "cinema": 40.0,
    "commercial": 20.0,
}


@dataclass
class TrialResult:
    prompt_idx: int
    profile_id: str
    success: bool
    n_llm_calls: int = 0
    repair_used: bool = False
    last_repair_mode: str | None = None
    shot_count: int = 0
    char_count: int = 0
    missing_chars: int = 0
    warnings_count: int = 0
    has_dialogue: bool = False  # 是否有 shot 填了 dialogue
    has_camera_movement: bool = False  # 是否有 shot 有非 static 镜头
    error: str | None = None
    elapsed_sec: float = 0.0


async def run_trial(prompt_idx: int, prompt: str, profile_id: str) -> TrialResult:
    profile = get_profile(profile_id)
    target = DURATION_BY_PROFILE[profile_id]
    t0 = time.time()
    try:
        sb, report, metrics = await generate_storyboard(
            user_prompt=prompt,
            target_duration_sec=target,
            profile=profile,
            max_retries=2,
            check_store=True,  # 会 warn missing 但不 fail
            verbose=False,
        )
        elapsed = time.time() - t0
        return TrialResult(
            prompt_idx=prompt_idx, profile_id=profile_id,
            success=True,
            n_llm_calls=metrics["n_llm_calls"],
            repair_used=metrics["repair_used"],
            last_repair_mode=metrics["last_repair_mode"],
            shot_count=len(sb.shots),
            char_count=len(sb.characters),
            missing_chars=len(report.missing_chars),
            warnings_count=len(report.warnings),
            has_dialogue=any(s.dialogue is not None for s in sb.shots),
            has_camera_movement=any(s.camera.movement != "static" for s in sb.shots),
            elapsed_sec=elapsed,
        )
    except StoryboardError as e:
        return TrialResult(
            prompt_idx=prompt_idx, profile_id=profile_id,
            success=False,
            n_llm_calls=len(e.last_errors) if hasattr(e, "last_errors") else 0,
            error=str(e)[:200],
            elapsed_sec=time.time() - t0,
        )
    except Exception as e:
        return TrialResult(
            prompt_idx=prompt_idx, profile_id=profile_id,
            success=False, error=f"{type(e).__name__}: {e}"[:200],
            elapsed_sec=time.time() - t0,
        )


async def main():
    print(f"=== Phase 2 acceptance: {len(TEST_PROMPTS)} prompts × {len(PROFILE_IDS)} profiles = "
          f"{len(TEST_PROMPTS) * len(PROFILE_IDS)} trials, concurrency={CONCURRENCY} ===")

    sem = asyncio.Semaphore(CONCURRENCY)

    async def gated(idx, prompt, profile):
        async with sem:
            r = await run_trial(idx, prompt, profile)
            status = "✓" if r.success else "✗"
            print(f"  {status} #{idx:02d}/{profile:11s} {r.elapsed_sec:5.1f}s "
                  f"calls={r.n_llm_calls} shots={r.shot_count} "
                  f"{'repair' if r.repair_used else 'direct'}"
                  + (f"  ERR: {r.error}" if not r.success else ""),
                  flush=True)
            return r

    tasks = [
        gated(i, p, pid)
        for i, p in enumerate(TEST_PROMPTS)
        for pid in PROFILE_IDS
    ]
    t0 = time.time()
    results = await asyncio.gather(*tasks)
    total_time = time.time() - t0

    # --- 汇总 ---
    print(f"\n=== 总耗时 {total_time:.1f}s ({total_time/60:.1f} min) ===\n")

    by_profile: dict[str, list[TrialResult]] = {p: [] for p in PROFILE_IDS}
    for r in results:
        by_profile[r.profile_id].append(r)

    print(f"{'profile':12s}  {'pass':>6s}  {'JSON%':>6s}  {'repair%':>7s}  "
          f"{'avgShots':>8s}  {'dialog%':>7s}  {'camMov%':>7s}  {'avgLLM':>6s}  {'avgSec':>6s}")
    print("-" * 90)
    overall_pass = 0
    for p in PROFILE_IDS:
        rs = by_profile[p]
        n = len(rs)
        passed = sum(1 for r in rs if r.success)
        overall_pass += passed
        json_rate = passed / n * 100
        repair_rate = sum(1 for r in rs if r.repair_used) / n * 100
        avg_shots = sum(r.shot_count for r in rs) / n
        dialog_rate = sum(1 for r in rs if r.has_dialogue) / n * 100
        cam_rate = sum(1 for r in rs if r.has_camera_movement) / n * 100
        avg_llm = sum(r.n_llm_calls for r in rs) / n
        avg_sec = sum(r.elapsed_sec for r in rs) / n
        print(f"{p:12s}  {passed}/{n:<4d} {json_rate:5.1f}% {repair_rate:6.1f}%  "
              f"{avg_shots:8.1f}  {dialog_rate:6.1f}%  {cam_rate:6.1f}%  "
              f"{avg_llm:6.2f}  {avg_sec:5.1f}s")

    overall = overall_pass / len(results) * 100
    print(f"\n=== 全部 {len(results)} trials 综合 pass rate: {overall:.1f}% ===")
    print(f"=== Phase 2 acceptance 目标 >95% : {'PASS ✓' if overall > 95 else 'FAIL ✗'} ===")

    # 失败的 trial 详细列出
    failures = [r for r in results if not r.success]
    if failures:
        print(f"\n=== {len(failures)} failures ===")
        for r in failures:
            print(f"  #{r.prompt_idx} {r.profile_id}: {r.error}")

    # 把结果存到文件方便对比
    out = {
        "total_trials": len(results),
        "pass_rate": overall,
        "elapsed_sec": total_time,
        "by_profile": {
            p: {
                "n": len(by_profile[p]),
                "passed": sum(1 for r in by_profile[p] if r.success),
                "avg_shots": sum(r.shot_count for r in by_profile[p]) / len(by_profile[p]),
                "repair_rate": sum(1 for r in by_profile[p] if r.repair_used) / len(by_profile[p]),
            }
            for p in PROFILE_IDS
        },
        "trials": [asdict(r) for r in results],
    }
    out_path = "/tmp/phase2_acceptance.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nfull results -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
