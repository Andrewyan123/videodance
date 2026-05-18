// Wizard state machine + API calls.
// 4 steps: INPUT → CHARS → SHOTS → RESULT
// 长流程靠 SSE (/api/runs/{tid}/events) 推进度; 关键 API 是 sync 等结果返回.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let state = {
  tid: null,            // run thread_id
  storyboard: null,     // full storyboard from server
  evtSource: null,      // EventSource for /events
  step: 1,
  charSelections: {},   // char_id → variant_index
  shotSelections: {},   // shot_id → variant_index
};

// ----- utilities -----

function logEvt(line) {
  const log = $("#log");
  log.textContent += (typeof line === "string" ? line : JSON.stringify(line)) + "\n";
  log.scrollTop = log.scrollHeight;
}

function gotoStep(n) {
  state.step = n;
  for (let i = 1; i <= 4; i++) {
    $(`#step-${i}`).classList.toggle("hidden", i !== n);
    const li = document.querySelector(`.steps li[data-step="${i}"]`);
    li.classList.toggle("active", i === n);
    li.classList.toggle("done", i < n);
  }
  window.scrollTo(0, 0);
}

function resolveLocalUrl(u) {
  // file:///private/tmp/... → /api/asset?path=...
  if (!u) return u;
  if (u.startsWith("file://")) {
    return "/api/asset?path=" + encodeURIComponent(u.slice(7));
  }
  return u;  // 远程 https URL 直用
}

function startEventStream(tid) {
  if (state.evtSource) state.evtSource.close();
  const es = new EventSource(`/api/runs/${tid}/events`);
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      logEvt(data);
      handleEvent(data);
    } catch (err) {
      logEvt("[parse error] " + e.data);
    }
  };
  es.onerror = () => { /* auto-reconnect by browser */ };
  state.evtSource = es;
}

function handleEvent(ev) {
  // 把后端 propose_* 事件展示在对应 progress 上
  const t = ev.type;
  if (t && t.startsWith("propose_character")) {
    const cid = ev.char_id;
    const node = document.querySelector(`.char-progress[data-char="${cid}"]`);
    if (node) {
      const label = describeEvent(ev);
      node.textContent = label;
      if (t === "propose_character_complete") {
        // refresh that character's variants from tree
        setTimeout(() => refreshTreeAndRender(), 400);
      }
    }
  }
  if (t && t.startsWith("propose_shot")) {
    const sid = ev.shot_id;
    const node = document.querySelector(`.shot-progress[data-shot="${sid}"]`);
    if (node) {
      node.textContent = describeEvent(ev);
      if (t === "propose_shot_complete") {
        setTimeout(() => refreshTreeAndRender(), 400);
      }
    }
  }
}

function describeEvent(ev) {
  switch (ev.type) {
    case "propose_character_start":     return `start: 抽 ${ev.n} 个变体...`;
    case "propose_character_progress":  return `variant ${ev.variant_index}: ${ev.stage}`;
    case "propose_character_done_variant": return `variant ${ev.variant_index} done`;
    case "propose_character_complete":  return `完成 ${ev.produced} 个变体`;
    case "propose_character_error":     return `variant ${ev.variant_index} ERROR`;
    case "propose_shot_start":          return `start: 抽 ${ev.n} 段视频...`;
    case "propose_shot_progress":       return `variant ${ev.variant_index}: ${ev.stage}` +
                                              (ev.seed ? ` seed=${ev.seed}` : "");
    case "propose_shot_done_variant":   return `variant ${ev.variant_index} done` +
                                              (ev.identity_score != null ? ` (id=${ev.identity_score.toFixed(2)})` : "");
    case "propose_shot_complete":       return `完成 ${ev.produced} 段`;
    case "propose_shot_error":          return `variant ${ev.variant_index} ERROR`;
    default: return ev.type || "(unknown)";
  }
}

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${method} ${path} → ${r.status}: ${text.slice(0, 300)}`);
  }
  return r.json();
}

// ----- Step 1: INPUT -----

$("#input-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#start-btn").disabled = true;
  $("#step1-hint").textContent = "planner 工作中…";

  try {
    const body = {
      user_prompt: $("#prompt").value.trim(),
      target_duration_sec: parseFloat($("#duration").value),
      profile_id: $("#profile").value,
      dry_run: $("#dry-run").checked,
      mode: "interactive",
    };
    const data = await api("POST", "/api/runs", body);
    state.tid = data.thread_id;
    state.storyboard = data.storyboard;
    logEvt(`thread_id=${state.tid}, ${state.storyboard.shots.length} shots, ${state.storyboard.characters.length} chars`);

    startEventStream(state.tid);
    renderChars();
    gotoStep(2);
  } catch (err) {
    alert("启动失败: " + err.message);
    $("#step1-hint").textContent = "启动失败: " + err.message;
  } finally {
    $("#start-btn").disabled = false;
  }
});

// ----- Step 2: CHARS -----

function renderChars() {
  const container = $("#chars-container");
  container.innerHTML = "";
  for (const c of state.storyboard.characters) {
    const block = document.createElement("div");
    block.className = "char-block";
    block.dataset.char = c.char_id;
    block.innerHTML = `
      <div class="char-block-header">
        <span class="char-name">${c.name} <code style="font-size:11px;color:#86868b">@${c.char_id}</code></span>
      </div>
      <p class="char-desc">${escapeHtml(c.description || "(无描述)")}</p>
      <div class="char-variants" data-char="${c.char_id}"></div>
      <div class="regen-row">
        <label>抽 <input type="number" min="1" max="8" value="3" class="regen-n"> 个</label>
        <button class="regen" data-char="${c.char_id}">生成候选</button>
        <span class="char-progress" data-char="${c.char_id}"></span>
      </div>
    `;
    container.appendChild(block);
  }
  container.querySelectorAll(".regen").forEach(btn => {
    btn.addEventListener("click", () => proposeChar(btn.dataset.char,
                          btn.closest(".regen-row").querySelector(".regen-n").valueAsNumber));
  });
  refreshTreeAndRender();
}

async function proposeChar(charId, n) {
  const btn = document.querySelector(`.regen[data-char="${charId}"]`);
  btn.disabled = true;
  const prog = document.querySelector(`.char-progress[data-char="${charId}"]`);
  prog.textContent = "请求中…";
  try {
    await api("POST", `/api/runs/${state.tid}/characters/${charId}/propose?n=${n}`);
    // events 已经实时更新; final 一次 tree 刷新
    await refreshTreeAndRender();
    prog.textContent = "";
  } catch (err) {
    prog.textContent = "ERR: " + err.message;
  } finally {
    btn.disabled = false;
  }
}

async function selectChar(charId, variantIdx) {
  await api("POST", `/api/runs/${state.tid}/characters/${charId}/select?variant=${variantIdx}`);
  state.charSelections[charId] = variantIdx;
  await refreshTreeAndRender();
  updateCharsNextButton();
}

function updateCharsNextButton() {
  const needAll = state.storyboard.characters.length;
  const have = Object.keys(state.charSelections).length;
  $("#chars-next").disabled = have < needAll;
}

$("#chars-back").addEventListener("click", () => gotoStep(1));
$("#chars-next").addEventListener("click", () => {
  renderShots();
  gotoStep(3);
});

// ----- Step 3: SHOTS (CSS grid tree) -----

function renderShots() {
  const tree = $("#shots-tree");
  tree.innerHTML = `<div class="tree-grid"><div class="tree-cols" id="tree-cols"></div></div>`;
  const cols = $("#tree-cols");
  for (const s of state.storyboard.shots) {
    const col = document.createElement("div");
    col.className = "tree-shot";
    col.dataset.shot = s.shot_id;
    col.innerHTML = `
      <div class="shot-title">Shot ${s.index} · ${s.shot_id}</div>
      <div class="shot-meta">${s.duration_sec}s · ${escapeHtml((s.action || "").slice(0, 60))}</div>
      <div class="shot-variants" data-shot="${s.shot_id}"></div>
      <div class="regen-row">
        <label>抽 <input type="number" min="1" max="5" value="2" class="regen-n"> 段</label>
        <button class="regen" data-shot="${s.shot_id}">生成</button>
      </div>
      <span class="shot-progress" data-shot="${s.shot_id}" style="font-size:11px;color:#0071e3"></span>
    `;
    cols.appendChild(col);
  }
  cols.querySelectorAll(".regen").forEach(btn => {
    btn.addEventListener("click", () => proposeShot(btn.dataset.shot,
                          btn.closest(".regen-row").querySelector(".regen-n").valueAsNumber));
  });
  refreshTreeAndRender();
}

async function proposeShot(shotId, n) {
  const btn = document.querySelector(`.regen[data-shot="${shotId}"]`);
  btn.disabled = true;
  const prog = document.querySelector(`.shot-progress[data-shot="${shotId}"]`);
  prog.textContent = "请求中… (每段视频约 4-10 分钟)";
  try {
    await api("POST", `/api/runs/${state.tid}/shots/${shotId}/propose?n=${n}`);
    await refreshTreeAndRender();
    prog.textContent = "";
  } catch (err) {
    prog.textContent = "ERR: " + err.message;
  } finally {
    btn.disabled = false;
  }
}

async function selectShot(shotId, variantIdx) {
  await api("POST", `/api/runs/${state.tid}/shots/${shotId}/select?variant=${variantIdx}`);
  state.shotSelections[shotId] = variantIdx;
  await refreshTreeAndRender();
  updateShotsStitchButton();
}

function updateShotsStitchButton() {
  const need = state.storyboard.shots.length;
  const have = Object.keys(state.shotSelections).length;
  $("#shots-stitch").disabled = have < need;
}

$("#shots-back").addEventListener("click", () => gotoStep(2));
$("#shots-stitch").addEventListener("click", async () => {
  gotoStep(4);
  await doStitch();
});

// ----- Step 4: STITCH -----

async function doStitch() {
  const result = $("#final-result");
  result.innerHTML = `<p class="muted" id="stitch-status">拼接中…</p>`;
  try {
    const data = await api("POST", `/api/runs/${state.tid}/stitch`);
    const url = resolveLocalUrl(data.final_video_url);
    result.innerHTML = `
      <video src="${url}" controls preload="metadata"></video>
      <div class="final-meta">
        <span>${data.shot_count} shots</span>
        <span>${(data.size_bytes / 1024 / 1024).toFixed(1)} MB</span>
        <a href="${url}" download class="link">下载</a>
      </div>
    `;
  } catch (err) {
    result.innerHTML = `<p class="muted" style="color:#c0392b">拼接失败: ${err.message}</p>`;
  }
}

$("#result-back").addEventListener("click", () => gotoStep(3));
$("#result-restart").addEventListener("click", () => {
  state = { tid: null, storyboard: null, evtSource: null, step: 1,
            charSelections: {}, shotSelections: {} };
  $("#prompt").value = "";
  gotoStep(1);
});

// ----- /tree refresh (data fetch + DOM render) -----

async function refreshTreeAndRender() {
  if (!state.tid) return;
  const tree = await api("GET", `/api/runs/${state.tid}/tree`);

  // chars
  for (const c of tree.characters) {
    const wrap = document.querySelector(`.char-variants[data-char="${c.char_id}"]`);
    if (!wrap) continue;
    wrap.innerHTML = "";
    for (const v of c.candidates) {
      const card = document.createElement("div");
      card.className = "char-variant" + (v.selected ? " selected" : "");
      card.dataset.variant = v.variant_index;
      const url = resolveLocalUrl(v.ref_image_url);
      card.innerHTML = `
        <img src="${url}" alt="v${v.variant_index}" />
        <div class="label">v${v.variant_index} ${v.selected ? "★" : ""}</div>
      `;
      card.addEventListener("click", () => selectChar(c.char_id, v.variant_index));
      wrap.appendChild(card);
    }
    if (c.candidates.some(v => v.selected)) {
      state.charSelections[c.char_id] = c.candidates.find(v => v.selected).variant_index;
    }
  }
  updateCharsNextButton();

  // shots
  for (const s of tree.shots) {
    const wrap = document.querySelector(`.shot-variants[data-shot="${s.shot_id}"]`);
    if (!wrap) continue;
    wrap.innerHTML = "";
    for (const v of s.candidates) {
      const card = document.createElement("div");
      const failed = !v.video_url || v.error;
      card.className = "shot-variant" + (v.selected ? " selected" : "") + (failed ? " failed" : "");
      card.dataset.variant = v.variant_index;
      if (failed) {
        card.innerHTML = `
          <div style="padding:14px;font-size:11px;color:#c0392b;">
            v${v.variant_index} FAILED<br/>${escapeHtml((v.error || "no video").slice(0, 80))}
          </div>
        `;
      } else {
        const url = resolveLocalUrl(v.video_url);
        const idLabel = v.identity_score != null
          ? `<span class="score ${v.identity_score < 0.3 ? 'low' : ''}">id=${v.identity_score.toFixed(2)}</span>`
          : '';
        card.innerHTML = `
          <video src="${url}" preload="metadata" muted></video>
          <div class="label"><span>v${v.variant_index} ${v.selected ? '★' : ''}</span>${idLabel}</div>
        `;
        const vid = card.querySelector("video");
        card.addEventListener("mouseenter", () => vid.play().catch(()=>{}));
        card.addEventListener("mouseleave", () => { vid.pause(); vid.currentTime = 0; });
        card.addEventListener("click", () => selectShot(s.shot_id, v.variant_index));
      }
      wrap.appendChild(card);
    }
    if (s.candidates.some(v => v.selected)) {
      state.shotSelections[s.shot_id] = s.candidates.find(v => v.selected).variant_index;
    }
  }
  updateShotsStitchButton();
}

// ----- utils -----

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}

$("#toggle-log").addEventListener("click", () => {
  const log = $("#log");
  log.hidden = !log.hidden;
  $("#toggle-log").textContent = log.hidden ? "展开 ▾" : "收起 ▴";
});
