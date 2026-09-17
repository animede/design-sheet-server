"use strict";
/* マルチキャラクターシート作成 UI。
   ポーリングでのDOM再構築はちらつきの原因になるため(diffusers-server 57番の教訓)、
   セルのタイルは一度だけ作り、以降はステータスと画像srcの差分のみ更新する。 */

const $ = (id) => document.getElementById(id);

let META = null;
let currentJobId = null;
let pollTimer = null;
const cellEls = {};       // "view/variant" -> {root, img, cap}
const loadedImages = new Set();

async function init() {
  // D&Dと基本操作は /api/meta の取得に失敗しても生かす(先に登録する)
  $("imageInput").addEventListener("change", onFileSelected);
  $("startBtn").addEventListener("click", startJob);
  $("recomposeBtn").addEventListener("click", recompose);
  setupDragDrop();

  META = await (await fetch("/api/meta")).json();

  const defaultViews = new Set([
    "front", "back", "left", "right", "front_left_45", "front_right_45",
  ]);
  $("viewChecks").innerHTML = META.views.map((v) =>
    `<label><input type="checkbox" name="view" value="${v.key}" ${defaultViews.has(v.key) ? "checked" : ""}> ${v.label}</label>`
  ).join("");
  $("modeChoices").innerHTML = META.modes.map((m) =>
    `<label class="mode-choice"><input type="radio" name="mode" value="${m.key}" ${m.key === META.defaults.mode ? "checked" : ""}><span>${m.label}</span></label>`
  ).join("") +
    '<label class="mode-choice"><input type="radio" name="mode" value="mix"><span>MIX(変種一覧)</span></label>';
  // MIXは表現モード(リアル/アニメ/イラスト/ちびキャラ…)から複数選ぶ
  const defaultVariants = new Set(["real", "anime", "illustration", "chibi"]);
  $("variantChecks").innerHTML = META.modes.map((m) =>
    `<label><input type="checkbox" name="variant" value="${m.key}" ${defaultVariants.has(m.key) ? "checked" : ""}> ${m.label}</label>`
  ).join("");
  $("heroVariant").innerHTML = META.modes.map((m) =>
    `<option value="${m.key}" ${m.key === "illustration" ? "selected" : ""}>${m.label}</option>`
  ).join("") + '<option value="none">なし(グリッドのみ)</option>';
  const defaultSizes = new Set(META.defaults.sizes);
  $("sizeChecks").innerHTML = META.sizes.map((s) =>
    `<label><input type="checkbox" name="size" value="${s.key}" ${defaultSizes.has(s.key) ? "checked" : ""}> ${s.label}</label>`
  ).join("");
  $("partialTarget").value = META.defaults.partial_target;
  $("partialColor").value = META.defaults.partial_color;
  document.querySelectorAll('input[name="mode"], input[name="variant"]').forEach((el) =>
    el.addEventListener("change", updateModeSettings)
  );
  updateModeSettings();

  checkHealth();
  setInterval(checkHealth, 15000);
  loadHistory();
}

// 過去ジョブの一覧(再起動・リロード後も生成済みシートへ辿り着けるように)
async function loadHistory() {
  const list = $("historyList");
  try {
    const body = await (await fetch("/api/sheet/jobs")).json();
    const jobs = (body.jobs || []).filter((j) => j.sheet_ready);
    if (!jobs.length) {
      list.innerHTML = '<li class="muted">まだありません</li>';
      return;
    }
    const modeLabel = Object.fromEntries((META?.modes || []).map((m) => [m.key, m.label]));
    list.innerHTML = jobs.map((j) => {
      const when = j.started_at ? new Date(j.started_at * 1000).toLocaleString("ja-JP") : j.job_id;
      return `<li>
        <a href="#" class="history-open" data-job="${j.job_id}">${when}</a>
        <span class="muted">${modeLabel[j.mode] || j.mode || ""}</span>
        <a href="/api/sheet/jobs/${j.job_id}/sheet.png" download>sheet.png</a>
        <a href="/api/sheet/jobs/${j.job_id}/download.zip" download>ZIP</a>
      </li>`;
    }).join("");
    list.querySelectorAll(".history-open").forEach((a) =>
      a.addEventListener("click", (e) => {
        e.preventDefault();
        openJob(a.dataset.job);
      })
    );
  } catch (e) {
    list.innerHTML = '<li class="muted">履歴の取得に失敗しました</li>';
  }
}

// 過去ジョブを結果パネルへ復元表示する
async function openJob(jobId) {
  const job = await (await fetch(`/api/sheet/jobs/${jobId}`)).json();
  if (!job || !job.views) return;
  currentJobId = jobId;
  const views = Object.keys(job.views);
  const variants = views.length ? Object.keys(job.views[views[0]]) : [];
  loadedImages.clear();
  buildCellGrid(views, variants);
  $("sheetArea").hidden = true;
  await poll();
}

// ドラッグ&ドロップ(エリア上のハイライト + ドロップで画像を選択)
function setupDragDrop() {
  // 枠を外して落とした時にブラウザが画像ページへ遷移するのを防ぐ
  ["dragover", "drop"].forEach((ev) =>
    window.addEventListener(ev, (e) => e.preventDefault())
  );
  const drop = $("fileDrop");
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      drop.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      drop.classList.remove("dragover");
    })
  );
  drop.addEventListener("drop", async (e) => {
    const f = [...(e.dataTransfer?.files || [])].find((f) => f.type.startsWith("image/"));
    if (f) return setInputFile(f);
    // 他タブの画像をドラッグした場合は File が無く URL だけ来る
    const url = (e.dataTransfer?.getData("text/uri-list") || e.dataTransfer?.getData("text/plain") || "").split("\n")[0].trim();
    if (!url) return;
    $("fileHint").textContent = "画像を取得中…";
    try {
      let resp;
      try {
        resp = await fetch(url, { mode: "cors" });
        if (!resp.ok) throw new Error(resp.status);
      } catch (_) {
        // 他オリジン(別ポートのアプリ)はCORSで弾かれるためサーバ経由で取得
        resp = await fetch(`/api/fetch-image?url=${encodeURIComponent(url)}`);
        if (!resp.ok) throw new Error(resp.status);
      }
      const blob = await resp.blob();
      if (!blob.type.startsWith("image/")) throw new Error("画像ではありません");
      const name = url.startsWith("data:") ? "dropped.png"
        : (decodeURIComponent(url.split("/").pop().split("?")[0]) || "dropped.png");
      setInputFile(new File([blob], name, { type: blob.type }));
    } catch (err) {
      $("fileHint").textContent = "この画像は直接取り込めませんでした。保存してから選択してください。";
    }
  });
}

function setInputFile(f) {
  const dt = new DataTransfer();
  dt.items.add(f);
  $("imageInput").files = dt.files;
  onFileSelected();
}

async function checkHealth() {
  const el = $("health");
  try {
    const h = await (await fetch("/api/health")).json();
    if (h.ok) {
      el.textContent = `バックエンド接続OK(${h.image_server_url}、空きVRAM ${h.vram_free_gb ? h.vram_free_gb.toFixed(1) : "?"}GB)`;
      el.className = "health ok";
    } else {
      el.textContent = `バックエンド未接続: ${h.error}`;
      el.className = "health err";
    }
  } catch (e) {
    el.textContent = "ヘルスチェック失敗";
    el.className = "health err";
  }
}

function onFileSelected() {
  const f = $("imageInput").files[0];
  if (!f) return;
  const img = $("inputPreview");
  img.src = URL.createObjectURL(f);
  img.hidden = false;
  $("fileHint").textContent = f.name;
  $("startBtn").disabled = false;
}

function selectedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((el) => el.value);
}

function selectedMode() {
  return document.querySelector('input[name="mode"]:checked')?.value || "illustration";
}

function updateModeSettings() {
  const mode = selectedMode();
  $("partialSettings").hidden = mode !== "partial" && !(mode === "mix" && selectedValues("variant").includes("partial"));
  $("mixSettings").hidden = mode !== "mix";
  $("sizeSection").hidden = mode === "mix";
}

async function startJob() {
  const f = $("imageInput").files[0];
  if (!f) return;
  const views = selectedValues("view");
  const mode = selectedMode();
  const isMix = mode === "mix";
  const sizes = selectedValues("size");
  const variants = selectedValues("variant");
  const err = $("formError");
  err.hidden = true;
  if (!views.length || (!isMix && !sizes.length)) {
    err.textContent = "ビューと表示サイズを1つ以上選択してください。";
    err.hidden = false;
    return;
  }
  if (isMix && !variants.length) {
    err.textContent = "MIXする変種を1つ以上選択してください。";
    err.hidden = false;
    return;
  }

  const fd = new FormData();
  fd.append("image", f);
  fd.append("seed", $("seed").value || "-1");
  fd.append("views", views.join(","));
  if (isMix) {
    fd.append("mode", "mix");
    fd.append("variants", variants.join(","));
    fd.append("hero_variant", $("heroVariant").value);
    fd.append("hero_view", "front");
  } else {
    fd.append("mode", mode);
    fd.append("sizes", sizes.join(","));
  }
  fd.append("partial_target", $("partialTarget").value);
  fd.append("partial_color", $("partialColor").value);
  fd.append("quant", $("quant").value);
  fd.append("layout", $("layout").value);

  $("startBtn").disabled = true;
  try {
    const resp = await fetch("/api/sheet/generate", { method: "POST", body: fd });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || resp.status);
    currentJobId = body.job_id;
    buildCellGrid(views, isMix ? variants : [mode]);
    $("sheetArea").hidden = true;
    loadedImages.clear();
    pollTimer = setInterval(poll, 1500);
  } catch (e) {
    err.textContent = `開始に失敗しました: ${e.message}`;
    err.hidden = false;
    $("startBtn").disabled = false;
  }
}

async function recompose() {
  if (!currentJobId) return;
  const msg = $("recomposeMsg");
  msg.textContent = "再配置中…";
  const fd = new FormData();
  fd.append("views", selectedValues("view").join(","));
  fd.append("sizes", selectedValues("size").join(","));
  fd.append("layout", $("layout").value);
  try {
    const resp = await fetch(`/api/sheet/jobs/${currentJobId}/recompose`, { method: "POST", body: fd });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || resp.status);
    $("sheetImg").src = `/api/sheet/jobs/${currentJobId}/sheet.png?v=${body.sheet_rev}`;
    msg.textContent = body.skipped_views.length
      ? `完了(未生成のためスキップ: ${body.skipped_views.join(", ")})`
      : "完了";
  } catch (e) {
    msg.textContent = `失敗: ${e.message}`;
  }
}

function buildCellGrid(views, variants) {
  const grid = $("cellGrid");
  grid.innerHTML = "";
  for (const k in cellEls) delete cellEls[k];
  const vlabel = Object.fromEntries(META.views.map((v) => [v.key, v.label]));
  const varlabel = Object.fromEntries([
    ...META.variants.map((v) => [v.key, v.label]),
    ...META.modes.map((v) => [v.key, v.label]),
  ]);
  for (const view of views) {
    const row = document.createElement("div");
    row.className = "view-row";
    row.innerHTML = `<div class="row-label">${vlabel[view]}</div>`;
    const cells = document.createElement("div");
    cells.className = "cells";
    for (const variant of variants) {
      const cell = document.createElement("div");
      cell.className = "cell";
      cell.innerHTML = `<div class="thumb"><img alt=""></div><div class="cap">${varlabel[variant]}: 待機</div>`;
      cells.appendChild(cell);
      cellEls[`${view}/${variant}`] = {
        root: cell, img: cell.querySelector("img"), cap: cell.querySelector(".cap"),
        label: varlabel[variant],
      };
    }
    row.appendChild(cells);
    grid.appendChild(row);
  }
}

const STATUS_JA = { pending: "待機", running: "生成中", done: "完了", error: "失敗" };

async function poll() {
  if (!currentJobId) return;
  let job;
  try {
    job = await (await fetch(`/api/sheet/jobs/${currentJobId}`)).json();
  } catch (e) {
    return; // 一時的な取得失敗は次回ポーリングに任せる
  }

  const badge = $("jobStatus");
  badge.textContent = job.status;
  badge.className = "status-badge" + (job.status === "done" ? " done" : job.status === "error" ? " error" : "");

  const line = $("progressLine");
  if (job.status === "styling" || job.status === "stylizing") {
    line.textContent = "1/4: 選択した表現に整えています…";
  } else if (job.status === "views") {
    const cs = job.charsheet;
    line.textContent = cs && cs.total
      ? `2/4: 多視点を生成中(${cs.progress}/${cs.total} 方向)…`
      : "2/4: 多視点生成を開始しています…";
  } else if (job.status === "finishing" || job.status === "variants") {
    line.textContent = "3/4: 各ビューの仕上げを生成しています…";
  } else if (job.status === "composing") {
    line.textContent = "4/4: シートを合成しています…";
  } else if (job.status === "error") {
    line.textContent = `エラー: ${job.error}`;
  } else if (job.status === "done") {
    const errs = Object.keys(job.cell_errors || {}).length;
    line.textContent = errs ? `完了(${errs}セルは失敗)` : "完了しました";
  }

  // セル差分更新(画像srcは一度だけ設定)
  for (const [view, variants] of Object.entries(job.views || {})) {
    for (const [variant, status] of Object.entries(variants)) {
      const cell = cellEls[`${view}/${variant}`];
      if (!cell) continue;
      cell.root.className = `cell ${status}`;
      cell.cap.textContent = `${cell.label}: ${STATUS_JA[status] || status}`;
      const key = `${view}_${variant}`;
      if (status === "done" && !loadedImages.has(key)) {
        loadedImages.add(key);
        cell.img.src = `/api/sheet/jobs/${currentJobId}/images/${key}.png`;
        cell.img.style.display = "block";
      }
    }
  }

  if (job.status === "done" || job.status === "error") {
    clearInterval(pollTimer);
    pollTimer = null;
    $("startBtn").disabled = false;
    if (job.sheet_ready) {
      $("sheetImg").src = `/api/sheet/jobs/${currentJobId}/sheet.png`;
      $("sheetLink").href = `/api/sheet/jobs/${currentJobId}/sheet.png`;
      $("zipLink").href = `/api/sheet/jobs/${currentJobId}/download.zip`;
      $("sheetArea").hidden = false;
    }
  }
}

init();
