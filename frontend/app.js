// 状态机: starting -> plan -> character_sheets -> shots -> stitch -> done
const PHASES = ["plan", "character_sheets", "shots", "stitch"];

const $ = (sel) => document.querySelector(sel);
const form = $("#run-form");
const statusBar = $("#status-bar");
const phasesEl = $("#phases");
const shotsEl = $("#shots-detail");
const finalEl = $("#final");
const logEl = $("#log");

let currentEventSource = null;
let shotState = {};   // shot_id -> {index, status, keyframe_url, video_url, ...}
let plannedShots = [];

function setPhase(phase) {
  // 标记 phase 之前的 done, 自己 active
  const idx = PHASES.indexOf(phase);
  for (const li of phasesEl.querySelectorAll("li")) {
    const p = li.dataset.phase;
    li.classList.remove("active", "done", "failed");
    const myIdx = PHASES.indexOf(p);
    if (myIdx < idx) li.classList.add("done");
    else if (myIdx === idx) li.classList.add("active");
  }
}

function markAllDone() {
  for (const li of phasesEl.querySelectorAll("li")) {
    li.classList.remove("active");
    li.classList.add("done");
  }
}

function setPhaseDetail(phase, detail) {
  const li = phasesEl.querySelector(`li[data-phase="${phase}"]`);
  if (li) li.querySelector(".phase-detail").textContent = detail;
}

function setStatus(text, kind = "") {
  statusBar.textContent = text;
  statusBar.className = "status " + kind;
}

function appendLog(line) {
  logEl.textContent += line + "\n";
  logEl.scrollTop = logEl.scrollHeight;
}

function renderShots() {
  const sorted = Object.values(shotState).sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
  shotsEl.innerHTML = sorted.map(shotCard).join("");
}

function shotCard(s) {
  const stage = (() => {
    if (s.status === "done") return "完成";
    if (s.status === "failed") return "失败 (重试 " + (s.retry_count || 0) + "/2)";
    if (s.video_url) return "等待 critic";
    if (s.keyframe_url) return "生成视频中…";
    return "生成关键帧中…";
  })();
  const thumbs = [];
  if (s.keyframe_url) thumbs.push(`<a href="${s.keyframe_url}" target="_blank">关键帧</a>`);
  if (s.video_url) thumbs.push(`<a href="${s.video_url}" target="_blank">视频</a>`);
  return `
    <div class="shot">
      <div class="shot-header">
        <span class="shot-id">shot[${s.index}] ${s.shot_id}</span>
        <span class="shot-status ${s.status}">${s.status}</span>
      </div>
      <div class="shot-progress">${stage}${s.duration_sec ? ` · ${s.duration_sec}s` : ""}</div>
      ${thumbs.length ? `<div class="shot-thumbs">${thumbs.join("")}</div>` : ""}
    </div>
  `;
}

function resolveVideoUrl(url) {
  // file:///tmp/... -> /api/video?path=/tmp/...
  if (url.startsWith("file://")) {
    return "/api/video?path=" + encodeURIComponent(url.slice(7));
  }
  return url;
}

function handleEvent(evt) {
  appendLog(JSON.stringify(evt));

  switch (evt.type) {
    case "started":
      setPhase("plan");
      setStatus(`运行中 · ${evt.thread_id}`, "running");
      break;

    case "plan_done":
      plannedShots = evt.shots || [];
      // 预先把骨架塞进 shotState, 这样 shots 阶段时立即看到所有占位卡
      shotState = {};
      for (const s of plannedShots) {
        shotState[s.shot_id] = { ...s, status: "pending" };
      }
      setPhaseDetail("plan", `${evt.shot_count} shot · ${evt.characters.length} char`);
      setPhase("character_sheets");
      renderShots();
      break;

    case "character_sheets_done": {
      const total = evt.characters.reduce((a, c) => a + (c.ref_image_urls?.length || 0), 0);
      setPhaseDetail("character_sheets", `${evt.characters.length} 角色 × 多角度 = ${total} 张`);
      setPhase("shots");
      break;
    }

    case "shot_progress": {
      const s = evt.shot;
      shotState[s.shot_id] = { ...shotState[s.shot_id], ...s };
      renderShots();
      const doneCount = Object.values(shotState).filter(x => x.status === "done").length;
      setPhaseDetail("shots", `${doneCount} / ${plannedShots.length} done`);
      break;
    }

    case "stitch_done":
      setPhase("stitch");
      setPhaseDetail("stitch", evt.final_video_url ? "ok" : "skipped");
      if (evt.final_video_url) {
        $("#final-video").src = resolveVideoUrl(evt.final_video_url);
        finalEl.hidden = false;
      }
      break;

    case "completed":
      markAllDone();
      const cost = evt.total_cost_usd ? `$${evt.total_cost_usd.toFixed(4)}` : "$0.00";
      const okCount = (evt.shots || []).filter(s => s.status === "done").length;
      setStatus(`完成 · ${okCount}/${(evt.shots||[]).length} shots · ~${cost}`, "done");
      $("#final-meta").textContent = `${okCount} shots · cost ~${cost}`;
      if (evt.final_video_url) {
        $("#final-video").src = resolveVideoUrl(evt.final_video_url);
        finalEl.hidden = false;
      }
      break;

    case "error":
      setStatus("错误: " + evt.error, "error");
      break;

    case "done":
      $("#submit-btn").disabled = false;
      if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
      }
      break;
  }
}

async function startRun(req) {
  // reset UI
  shotState = {};
  plannedShots = [];
  shotsEl.innerHTML = "";
  finalEl.hidden = true;
  logEl.textContent = "";
  for (const li of phasesEl.querySelectorAll("li")) {
    li.classList.remove("active", "done", "failed");
    li.querySelector(".phase-detail").textContent = "";
  }

  setStatus("提交中…", "running");
  const r = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    setStatus("启动失败: " + r.statusText, "error");
    $("#submit-btn").disabled = false;
    return;
  }
  const { thread_id } = await r.json();
  setStatus(`已启动 · ${thread_id}`, "running");

  currentEventSource = new EventSource(`/api/runs/${thread_id}/events`);
  currentEventSource.onmessage = (e) => {
    try {
      handleEvent(JSON.parse(e.data));
    } catch (err) {
      appendLog("[parse error] " + e.data);
    }
  };
  currentEventSource.onerror = () => {
    // EventSource 在 stream 结束后会自动重连; 我们已经在 'done' 里手动关了
    if (currentEventSource && currentEventSource.readyState === EventSource.CLOSED) return;
  };
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  $("#submit-btn").disabled = true;
  startRun({
    user_prompt: $("#prompt").value.trim(),
    target_duration_sec: parseFloat($("#duration").value),
    dry_run: $("#dry-run").checked,
  });
});

$("#toggle-log").addEventListener("click", () => {
  const hidden = logEl.hidden;
  logEl.hidden = !hidden;
  $("#toggle-log").textContent = hidden ? "收起 ▴" : "展开 ▾";
});
