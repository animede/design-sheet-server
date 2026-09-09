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

const VARIANT_IMAGE = { color: "_color", lineart: "_lineart", partial: "_partial" };

async function init() {
  META = await (await fetch("/api/meta")).json();

  const defaultViews = new Set([
    "front", "back", "left", "right", "front_left_45", "front_right_45",
  ]);
  $("viewChecks").innerHTML = META.views.map((v) =>
    `<label><input type="checkbox" name="view" value="${v.key}" ${defaultViews.has(v.key) ? "checked" : ""}> ${v.label}</label>`
  ).join("");
  $("variantChecks").innerHTML = META.variants.map((v) =>
    `<label><input type="checkbox" name="variant" value="${v.key}" checked> ${v.label}</label>`
  ).join("");
  $("partialTarget").value = META.defaults.partial_target;
  $("partialColor").value = META.defaults.partial_color;
  $("heroView").innerHTML = META.views.map((v) =>
    `<option value="${v.key}" ${v.key === "front" ? "selected" : ""}>${v.label}</option>`
  ).join("");

  checkHealth();
  setInterval(checkHealth, 15000);

  $("imageInput").addEventListener("change", onFileSelected);
  $("startBtn").addEventListener("click", startJob);
  $("recomposeBtn").addEventListener("click", recompose);

  // ドラッグ&ドロップ(エリア上のハイライト + ドロップで画像を選択)
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
  drop.addEventListener("drop", (e) => {
    const f = [...(e.dataTransfer?.files || [])].find((f) => f.type.startsWith("image/"));
    if (!f) return;
    const dt = new DataTransfer();
    dt.items.add(f);
    $("imageInput").files = dt.files;
    onFileSelected();
  });
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

async function startJob() {
  const f = $("imageInput").files[0];
  if (!f) return;
  const views = selectedValues("view");
  const variants = selectedValues("variant");
  const err = $("formError");
  err.hidden = true;

  const fd = new FormData();
  fd.append("image", f);
  fd.append("seed", $("seed").value || "-1");
  fd.append("views", views.join(","));
  fd.append("variants", variants.join(","));
  fd.append("stylize", $("stylize").checked ? "true" : "false");
  fd.append("partial_target", $("partialTarget").value);
  fd.append("partial_color", $("partialColor").value);
  fd.append("quant", $("quant").value);
  fd.append("layout", $("layout").value);
  fd.append("hero_variant", $("heroVariant").value);
  fd.append("hero_view", $("heroView").value);

  $("startBtn").disabled = true;
  try {
    const resp = await fetch("/api/sheet/generate", { method: "POST", body: fd });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || resp.status);
    currentJobId = body.job_id;
    buildCellGrid(views, variants);
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
  fd.append("variants", selectedValues("variant").join(","));
  fd.append("layout", $("layout").value);
  fd.append("hero_variant", $("heroVariant").value);
  fd.append("hero_view", $("heroView").value);
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
  const varlabel = Object.fromEntries(META.variants.map((v) => [v.key, v.label]));
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
  if (job.status === "stylizing") {
    line.textContent = "1/4: 入力をイラスト化しています…";
  } else if (job.status === "views") {
    const cs = job.charsheet;
    line.textContent = cs && cs.total
      ? `2/4: 多視点を生成中(${cs.progress}/${cs.total} 方向)…`
      : "2/4: 多視点生成を開始しています…";
  } else if (job.status === "variants") {
    line.textContent = "3/4: 線画・部分彩色を生成しています…";
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
      const key = `${view}${VARIANT_IMAGE[variant]}`;
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
