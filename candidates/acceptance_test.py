"""Phase 5.1 end-to-end acceptance.

启 server → 跑 interactive 流程 → 1 char × 2 variants → select → 1 shot × 2 variants → select → stitch.
预算: ~$6-10 (1 char @ $0.12 + 1 shot × 2 candidates @ $3 each = $6.12), ~8-12 min.

用法:
  unset all_proxy http_proxy https_proxy
  uv run python -m candidates.acceptance_test
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx


BASE = "http://127.0.0.1:8001"  # 默认避开 8000 (用户可能在跑别的)
TIMEOUT = httpx.Timeout(connect=10.0, read=1200.0, write=30.0, pool=60.0)


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as client:

        # 1. start interactive run (短时长强制 planner 出 1 个 shot)
        print("=== 1. POST /api/runs interactive ===")
        t0 = time.time()
        r = await client.post("/api/runs", json={
            "user_prompt": "A young Chinese man called Yan sits at his desk and opens a book",
            "target_duration_sec": 5,  # 强制 1 shot
            "mode": "interactive",
            "dry_run": False,
            "profile_id": "short_drama",
        })
        r.raise_for_status()
        d = r.json()
        tid = d["thread_id"]
        sb = d["storyboard"]
        chars = sb["characters"]
        shots = sb["shots"]
        print(f"  ({time.time()-t0:.1f}s) tid={tid}")
        print(f"  characters: {[c['char_id'] for c in chars]}")
        print(f"  shots:      {[s['shot_id'] for s in shots]}")
        assert chars, "expected at least 1 character from planner"
        assert shots, "expected at least 1 shot from planner"
        char_id = chars[0]["char_id"]
        shot_id = shots[0]["shot_id"]

        # 2. propose 2 character variants
        print(f"\n=== 2. propose 2 character variants for {char_id} (~30s, ~$0.12) ===")
        t0 = time.time()
        r = await client.post(f"/api/runs/{tid}/characters/{char_id}/propose", params={"n": 2})
        r.raise_for_status()
        char_variants = r.json()["variants"]
        print(f"  ({time.time()-t0:.1f}s) produced {len(char_variants)} variants")
        for v in char_variants:
            print(f"  variant_index={v['variant_index']} ref={v['ref_image_url'][-50:]}")
        assert len(char_variants) >= 1, "expected at least 1 character variant"

        # 3. select first
        chosen_char_var = char_variants[0]["variant_index"]
        print(f"\n=== 3. select character variant={chosen_char_var} ===")
        r = await client.post(f"/api/runs/{tid}/characters/{char_id}/select",
                              params={"variant": chosen_char_var})
        r.raise_for_status()
        print(f"  → {r.json()}")

        # 4. propose 2 shot variants
        print(f"\n=== 4. propose 2 shot variants for {shot_id} (~10 min, ~$6) ===")
        t0 = time.time()
        r = await client.post(f"/api/runs/{tid}/shots/{shot_id}/propose", params={"n": 2})
        r.raise_for_status()
        shot_variants = r.json()["variants"]
        print(f"  ({time.time()-t0:.1f}s) produced {len(shot_variants)} variants")
        for v in shot_variants:
            print(f"  variant_index={v['variant_index']} score={v.get('identity_score')} "
                  f"backend={v.get('backend_used')} url={v.get('video_url','')[-50:]}")
        # 至少 1 个成功 (其它可能 transient 失败)
        ok = [v for v in shot_variants if v.get("video_url")]
        assert ok, f"no successful shot variants: {shot_variants}"

        # 5. select first ok
        chosen_shot_var = ok[0]["variant_index"]
        print(f"\n=== 5. select shot variant={chosen_shot_var} ===")
        r = await client.post(f"/api/runs/{tid}/shots/{shot_id}/select",
                              params={"variant": chosen_shot_var})
        r.raise_for_status()
        print(f"  → {r.json()}")

        # 6. tree
        print(f"\n=== 6. GET /tree ===")
        r = await client.get(f"/api/runs/{tid}/tree")
        r.raise_for_status()
        tree = r.json()
        for c in tree["characters"]:
            n_total = len(c["candidates"])
            n_sel = sum(1 for v in c["candidates"] if v["selected"])
            print(f"  char {c['char_id']}: {n_total} candidates, {n_sel} selected")
        for s in tree["shots"]:
            n_total = len(s["candidates"])
            n_sel = sum(1 for v in s["candidates"] if v["selected"])
            print(f"  shot {s['shot_id']}: {n_total} candidates, {n_sel} selected")

        # 7. stitch
        print(f"\n=== 7. POST /stitch ===")
        t0 = time.time()
        r = await client.post(f"/api/runs/{tid}/stitch")
        r.raise_for_status()
        sd = r.json()
        print(f"  ({time.time()-t0:.1f}s) final_video_url={sd['final_video_url']}")
        print(f"  size={sd['size_bytes']} shots={sd['shot_count']}")
        # 验证文件存在
        local_path = sd["final_video_url"].replace("file://", "")
        assert os.path.isfile(local_path), f"final mp4 not on disk: {local_path}"
        print(f"  ✓ mp4 file exists: {local_path}")

        print("\n=== ALL PASS ===")


if __name__ == "__main__":
    asyncio.run(main())
