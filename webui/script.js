// 天気図VLM解説 デモUI。history/index.json（daily_update.py が毎日追加する）と、手動キュレーションの
// data.json（2026-09-25のサンプル）を日付選択で切り替えて表示する静的ビュー。
let DATA = null;
let readerMode = "general";

const SAMPLE_OPTION = { value: "sample", label: "サンプル (2026-09-25・手動検証)", path: "data.json" };

async function loadIndex() {
  const options = [];
  try {
    const res = await fetch("history/index.json");
    if (res.ok) {
      const idx = await res.json();
      idx.dates.forEach((d) => options.push({ value: d, label: `${d}（自動更新）`, path: `history/${d}/data.json` }));
    }
  } catch (e) {
    console.warn("history/index.json が読めません（初回実行前は正常）", e);
  }
  options.push(SAMPLE_OPTION);
  return options;
}

async function main() {
  const options = await loadIndex();
  const select = document.getElementById("date-select");
  select.innerHTML = "";
  options.forEach((opt) => {
    const el = document.createElement("option");
    el.value = opt.value;
    el.textContent = opt.label;
    el.dataset.path = opt.path;
    select.appendChild(el);
  });
  select.addEventListener("change", () => loadEntry(select.selectedOptions[0].dataset.path));

  document.getElementById("btn-general").addEventListener("click", () => setReaderMode("general"));
  document.getElementById("btn-kids").addEventListener("click", () => setReaderMode("kids"));

  await loadEntry(select.selectedOptions[0].dataset.path);
}

async function loadEntry(path) {
  const res = await fetch(path);
  DATA = await res.json();
  readerMode = "general";
  document.getElementById("btn-general").classList.add("active");
  document.getElementById("btn-kids").classList.remove("active");

  // 更新時刻（updated_jst）があれば優先。無い旧データ・サンプルは天気図の対象時刻を表示
  document.getElementById("chart-time").textContent = DATA.updated_jst
    ? `${DATA.updated_jst} 更新`
    : `${DATA.chart_time_jst} 時点`;
  document.getElementById("pattern-badge").textContent = DATA.pattern;
  setConfidenceBadge(DATA.confidence);

  renderImageTabs();
  renderExplain();
  renderGuardrail();
}

function setConfidenceBadge(conf) {
  const el = document.getElementById("confidence-badge");
  el.textContent = `確信度 ${conf.toFixed(2)}`;
  el.classList.remove("high", "mid", "low");
  if (conf >= 0.7) el.classList.add("high");
  else if (conf >= 0.6) el.classList.add("mid");
  else el.classList.add("low");
}

function renderImageTabs() {
  const tabs = document.getElementById("image-tabs");
  tabs.innerHTML = "";
  DATA.images.forEach((img, i) => {
    const btn = document.createElement("button");
    btn.textContent = img.label;
    if (i === 0) btn.classList.add("active");
    btn.addEventListener("click", () => {
      document.getElementById("chart-image").src = img.src;
      tabs.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
    });
    tabs.appendChild(btn);
  });
  document.getElementById("chart-image").src = DATA.images[0].src;
}

function setReaderMode(mode) {
  readerMode = mode;
  document.getElementById("btn-general").classList.toggle("active", mode === "general");
  document.getElementById("btn-kids").classList.toggle("active", mode === "kids");
  renderExplain();
}

function renderExplain() {
  const content = DATA[readerMode] && DATA[readerMode].today ? DATA[readerMode] : DATA.general;
  document.getElementById("today-text").textContent = content.today;
  document.getElementById("tomorrow-text").textContent = content.tomorrow;

  const evList = document.getElementById("evidence-list");
  evList.innerHTML = "";
  (content.evidence || []).forEach((e) => {
    const li = document.createElement("li");
    li.textContent = e;
    evList.appendChild(li);
  });

  // 気圧の谷の判読結果（等圧線の凹み × 衛星の雲域 × アメダス降水）を根拠欄の末尾に追記
  const cloudLabel = { yes: "雲域と一致", partial: "雲域と一部一致", no: "雲域と不一致" };
  (DATA.troughs || []).forEach((t) => {
    const li = document.createElement("li");
    li.textContent = `気圧の谷: ${t.location}（${cloudLabel[t.cloud_match] || t.cloud_match}／降水あり: ${t.rain_stations}）`;
    evList.appendChild(li);
  });

  const cpList = document.getElementById("checkpoints-list");
  cpList.innerHTML = "";
  (content.check_points || []).forEach((c) => {
    const li = document.createElement("li");
    li.textContent = c;
    cpList.appendChild(li);
  });

  document.getElementById("caveats-text").textContent = DATA.caveats;
}

function renderGuardrail() {
  const tbody = document.querySelector("#guardrail-table tbody");
  tbody.innerHTML = "";
  DATA.guardrail_checks.forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="verdict ${c.verdict}">${c.verdict}</span></td>
      <td>${c.label}</td>
      <td>${c.detail}</td>
    `;
    tbody.appendChild(tr);
  });

  const acc = DATA.accuracy_note;
  document.getElementById("accuracy-note").textContent = acc
    ? `複数事例での検証（${acc.n}件・7パターン）: 正答 ${acc.correct}/${acc.n}。` +
      `${acc.strong.join("・")}は強いが、${acc.weak}`
    : "この日の判定は毎朝自動実行（GitHub Actions）。過去の複数事例での正答率は67%（15事例、README参照）。";

  document.getElementById("source-text").textContent = DATA.source;
}

main();
