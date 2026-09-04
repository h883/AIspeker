/* 画面制御とアプリ全体の流れ（仕様書 14〜19章）。 */
(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  const state = {
    view: "home",
    history: [],      // Gemini へ渡す直近の会話
    settings: {},
    homeTimer: null,
    dueTimer: null
  };

  /* ================= 共通 UI ================= */

  function showToast(message, ms) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => { toast.hidden = true; }, ms || 2600);
  }

  function switchView(name) {
    state.view = name;
    $$(".view").forEach(el => el.classList.toggle("is-active", el.id === "view-" + name));
    $$(".tabbar button").forEach(el => el.classList.toggle("is-active", el.dataset.view === name));
    const loaders = {
      home: loadHome,
      calendar: loadCalendar,
      weather: loadWeather,
      reminders: loadReminders,
      history: loadHistory,
      settings: loadSettings
    };
    if (loaders[name]) loaders[name]();
    window.scrollTo(0, 0);
  }

  /* AI 状態表示（仕様書 15章） */
  const ORB_STATES = {
    idle:      { cls: "",            label: "何か話してね" },
    standby:   { cls: "is-standby",   label: "" },   // ラベルは呼びかけの言葉に差し替える
    listening: { cls: "is-listening", label: "聞いています…" },
    thinking:  { cls: "is-thinking",  label: "考えています…" },
    fetching:  { cls: "is-fetching",  label: "情報を確認しています…" },
    speaking:  { cls: "is-listening", label: "お答えします" },
    error:     { cls: "is-error",     label: "エラーが発生しました" }
  };

  function setOrb(stateName) {
    const orb = $("#orb");
    const info = ORB_STATES[stateName] || ORB_STATES.idle;
    orb.className = "orb " + info.cls;
    if (stateName === "standby") {
      const phrase = (state.settings.wake_word || "ねえAI").split("/")[0];
      $("#orb-label").textContent = "「" + phrase + "」と呼んでね";
    } else {
      $("#orb-label").textContent = info.label;
    }
  }

  /** 一連のやり取りが終わったときの落ち着き先。待受中なら待受へ戻る。 */
  function settle() {
    setOrb(WakeWord.running ? "standby" : "idle");
  }

  function showAnswer(text, meta) {
    $("#answer").hidden = false;
    $("#answer-text").textContent = text;
    $("#answer-meta").textContent = meta || "";
  }

  /* ================= 会話 ================= */

  /** 読み上げが終わるまで待つ。読み上げOFFなら即座に解決する。 */
  function speakAndWait(text) {
    return new Promise(resolve => {
      if (state.settings.tts_enabled === false || !text) { resolve(); return; }
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      Speech.speak(text, { rate: state.settings.tts_rate || 1.0, onend: finish });
      // onend が来ない端末があるので、長さから見積もった時間で必ず解決させる
      setTimeout(finish, 1500 + text.length * 130);
    });
  }

  async function ask(message, options) {
    options = options || {};
    if (!message) return;

    if (options.fromChat) appendBubble("user", message);
    setOrb("thinking");

    // ツール利用が始まっていそうな頃合いで表示を切り替える（体感の説明）
    const fetchingTimer = setTimeout(() => {
      if (state.view === "home") setOrb("fetching");
    }, 1800);

    let data;
    try {
      data = await API.chat(message, state.history.slice(-6));
    } catch (err) {
      clearTimeout(fetchingTimer);
      setOrb("error");
      const text = err.message;
      if (options.fromChat) appendBubble("err", text); else showAnswer(text, "");
      await speakAndWait(text);
      setTimeout(settle, 1200);
      return;
    }
    clearTimeout(fetchingTimer);

    if (!data.ok) {
      setOrb("error");
      if (options.fromChat) appendBubble("err", data.text); else showAnswer(data.text, "");
      await speakAndWait(data.text);
      setTimeout(settle, 1200);
      return;
    }

    state.history.push({ user: message, assistant: data.text });
    if (state.history.length > 8) state.history.shift();

    const meta = data.tools && data.tools.length ? "使用: " + data.tools.join(", ") : "";
    if (options.fromChat) appendBubble("ai", data.text); else showAnswer(data.text, meta);

    setOrb("speaking");
    await speakAndWait(data.text);
    settle();
  }

  function startListening(onText, options) {
    options = options || {};
    Speech.cancelSpeech();
    Speech.listen({
      onstart: () => { setOrb("listening"); $("#orb-transcript").textContent = ""; },
      oninterim: (text) => { $("#orb-transcript").textContent = text; },
      onerror: (message) => {
        if (options.quiet) return;          // 待受中の「聞き取れませんでした」は出さない
        setOrb("error");
        showToast(message);
        setTimeout(settle, 2000);
      },
      onend: (text) => {
        $("#orb-transcript").textContent = text;
        if (text) onText(text);
        else if (options.onSilence) options.onSilence();
        else settle();
      }
    });
  }

  /* ================= ウェイクワード常時待受（仕様書 20章） ================= */

  function updateWakeToggle() {
    const button = $("#wake-toggle");
    const on = WakeWord.running;
    button.setAttribute("aria-pressed", on ? "true" : "false");
    $("#wake-toggle-text").textContent = on ? "常時待受 オン" : "常時待受 オフ";
  }

  /** 呼びかけを検知してから、質問を聞いて答えるまでの一連の流れ。 */
  async function runWakeCycle(spokenRest) {
    if (state.settings.wake_chime !== false) WakeWord.chime();

    // 「ねえAI、明日の天気は？」のように続けて言われたら、そのまま処理する
    if (spokenRest && spokenRest.length >= 2) {
      $("#orb-transcript").textContent = spokenRest;
      switchView("home");
      await ask(spokenRest, { fromChat: false });
      WakeWord.resume();
      settle();
      return;
    }

    switchView("home");
    const ack = (state.settings.wake_ack || "").trim();
    if (ack) await speakAndWait(ack);

    let handled = false;
    const backToStandby = () => {
      if (handled) return;
      handled = true;
      WakeWord.resume();
      settle();
    };

    // 呼びかけたまま黙っている場合は待受へ戻す
    const timeout = setTimeout(backToStandby, (state.settings.wake_timeout_seconds || 8) * 1000);

    startListening(async (text) => {
      clearTimeout(timeout);
      if (handled) return;
      handled = true;
      await ask(text, { fromChat: false });
      WakeWord.resume();
      settle();
    }, {
      quiet: true,
      onSilence: () => { clearTimeout(timeout); backToStandby(); }
    });
  }

  function startWakeWord(options) {
    options = options || {};
    const started = WakeWord.start({
      phrase: state.settings.wake_word || "ねえAI",
      onDetect: runWakeCycle,
      onError: (message) => {
        showToast(message, 5000);
        updateWakeToggle();
        settle();
      }
    });
    updateWakeToggle();
    if (started) {
      if (options.announce) showToast("常時待受をオンにしました", 2200);
      settle();
    }
    return started;
  }

  function stopWakeWord(options) {
    WakeWord.stop();
    updateWakeToggle();
    settle();
    if (options && options.announce) showToast("常時待受をオフにしました", 2000);
  }

  function appendBubble(kind, text) {
    const log = $("#chat-log");
    const bubble = document.createElement("div");
    bubble.className = "bubble " + kind;
    bubble.textContent = text;
    log.appendChild(bubble);
    bubble.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  /* ================= ホーム ================= */

  function updateClock() {
    const now = new Date();
    const days = ["日", "月", "火", "水", "木", "金", "土"];
    $("#clock").textContent = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
    $("#today").textContent = (now.getMonth() + 1) + "月" + now.getDate() + "日(" + days[now.getDay()] + ")";
  }

  function formatEventTime(iso) {
    if (!iso) return "";
    if (iso.length === 10) return "終日";
    const date = new Date(iso);
    if (isNaN(date)) return iso.slice(11, 16);
    return String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
  }

  async function loadHome() {
    let data;
    try {
      data = await API.home();
    } catch (err) {
      $("#home-weather").textContent = "取得できません";
      $("#home-weather-sub").textContent = err.message;
      return;
    }

    const weather = data.weather;
    if (weather && weather.ok) {
      $("#home-weather").textContent = weather.weather + " " + Math.round(weather.temperature_max) + "℃";
      $("#home-weather-sub").textContent =
        "最低 " + Math.round(weather.temperature_min) + "℃ / 降水確率 " +
        (weather.precipitation_probability != null ? weather.precipitation_probability + "%" : "—");
    } else {
      $("#home-weather").textContent = "取得できません";
      $("#home-weather-sub").textContent = (weather && weather.error) || "";
    }

    const event = data.next_event;
    if (event) {
      $("#home-event").textContent = formatEventTime(event.start) + " " + event.title;
      $("#home-event-sub").textContent = event.location || "";
    } else if (data.calendar && data.calendar.ok) {
      $("#home-event").textContent = "予定なし";
      $("#home-event-sub").textContent = "";
    } else {
      $("#home-event").textContent = "取得できません";
      $("#home-event-sub").textContent = (data.calendar && data.calendar.error) || "";
    }

    const route = data.route;
    $("#home-route-title").textContent = (state.settings.school_name || "学校") + "まで";
    if (route && route.ok) {
      $("#home-route").textContent = "約" + route.duration_minutes + "分";
      $("#home-route-sub").textContent = route.arrival_time.slice(11) + " 着予定";
    } else {
      $("#home-route").textContent = "—";
      $("#home-route-sub").textContent = (route && route.error) || "";
    }
  }

  /* ================= 予定 ================= */

  async function loadCalendar() {
    const container = $("#calendar-list");
    container.innerHTML = "";
    let data;
    try {
      data = await API.calendar(7);
    } catch (err) {
      container.innerHTML = "";
      container.appendChild(errorBox(err.message));
      return;
    }
    if (!data.ok) { container.appendChild(errorBox(data.error)); return; }
    if (!data.events.length) { container.innerHTML = '<p class="empty">7日以内の予定はありません。</p>'; return; }

    data.events.forEach(event => {
      const item = document.createElement("div");
      item.className = "item";
      const dateLabel = event.start.slice(5, 10).replace("-", "/");
      item.innerHTML =
        '<div><p class="item-title"></p><p class="item-sub"></p></div>' +
        '<span class="item-time"></span>';
      item.querySelector(".item-title").textContent = event.title;
      item.querySelector(".item-sub").textContent = [dateLabel, event.location].filter(Boolean).join(" · ");
      item.querySelector(".item-time").textContent = formatEventTime(event.start);
      if (event.source === "local") {
        const del = document.createElement("button");
        del.className = "tiny";
        del.textContent = "削除";
        del.onclick = async () => { await API.deleteEvent(event.id); loadCalendar(); };
        item.appendChild(del);
      }
      container.appendChild(item);
    });
  }

  function errorBox(message) {
    const box = document.createElement("div");
    box.className = "error-box";
    box.textContent = message || "情報を取得できませんでした。";
    return box;
  }

  /* ================= 経路 ================= */

  async function searchRoute(params) {
    const container = $("#route-result");
    container.innerHTML = '<p class="empty">検索中…</p>';
    let data;
    try {
      data = await API.route(params);
    } catch (err) {
      container.innerHTML = ""; container.appendChild(errorBox(err.message)); return;
    }
    container.innerHTML = "";
    if (!data.ok) {
      container.appendChild(errorBox(data.error + (data.hint ? "\n" + data.hint : "")));
      return;
    }

    const summary = document.createElement("div");
    summary.className = "item";
    summary.innerHTML = '<div><p class="item-title"></p><p class="item-sub"></p></div><span class="item-time"></span>';
    summary.querySelector(".item-title").textContent = "約" + data.duration_minutes + "分 / " + data.distance_km + "km";
    summary.querySelector(".item-sub").textContent = data.origin + " → " + data.destination;
    summary.querySelector(".item-time").textContent = data.departure_time.slice(11) + " 発";
    container.appendChild(summary);

    data.steps.forEach(step => {
      const item = document.createElement("div");
      item.className = "item";
      const title = step.type === "transit"
        ? (step.line || step.vehicle) + " " + step.from + " → " + step.to
        : (step.instruction || "移動");
      const sub = step.type === "transit"
        ? [step.headsign, step.departure && (step.departure + " 発")].filter(Boolean).join(" · ")
        : (step.distance_m ? step.distance_m + "m" : "");
      item.innerHTML = '<div><p class="item-title"></p><p class="item-sub"></p></div><span class="item-time"></span>';
      item.querySelector(".item-title").textContent = title;
      item.querySelector(".item-sub").textContent = sub;
      item.querySelector(".item-time").textContent = step.duration_min ? step.duration_min + "分" : "";
      container.appendChild(item);
    });
  }

  /* ================= 天気 ================= */

  async function loadWeather() {
    const container = $("#weather-detail");
    container.innerHTML = '<p class="empty">読み込み中…</p>';
    let data;
    try {
      data = await API.weather();
    } catch (err) {
      container.innerHTML = ""; container.appendChild(errorBox(err.message)); return;
    }
    container.innerHTML = "";
    if (!data.ok) { container.appendChild(errorBox(data.error)); return; }

    const head = document.createElement("div");
    head.className = "item";
    head.innerHTML = '<div><p class="item-title"></p><p class="item-sub"></p></div><span class="item-time"></span>';
    head.querySelector(".item-title").textContent = data.location + " · " + data.weather;
    head.querySelector(".item-sub").textContent =
      "最高 " + Math.round(data.temperature_max) + "℃ / 最低 " + Math.round(data.temperature_min) + "℃";
    head.querySelector(".item-time").textContent =
      (data.precipitation_probability != null ? data.precipitation_probability + "%" : "—");
    container.appendChild(head);

    data.hourly.filter((_, i) => i % 3 === 0).forEach(hour => {
      const item = document.createElement("div");
      item.className = "item";
      item.innerHTML = '<div><p class="item-title"></p><p class="item-sub"></p></div><span class="item-time"></span>';
      item.querySelector(".item-title").textContent = hour.time + " " + hour.weather;
      item.querySelector(".item-sub").textContent =
        hour.temperature != null ? Math.round(hour.temperature) + "℃" : "";
      item.querySelector(".item-time").textContent =
        hour.precipitation_probability != null ? hour.precipitation_probability + "%" : "";
      container.appendChild(item);
    });
  }

  /* ================= リマインダー ================= */

  async function loadReminders() {
    const container = $("#reminder-list");
    container.innerHTML = "";
    let data;
    try {
      data = await API.reminders();
    } catch (err) { container.appendChild(errorBox(err.message)); return; }
    if (!data.reminders.length) { container.innerHTML = '<p class="empty">登録中のリマインダーはありません。</p>'; return; }

    data.reminders.forEach(reminder => {
      const item = document.createElement("div");
      item.className = "item";
      item.innerHTML = '<div><p class="item-title"></p><p class="item-sub"></p></div>';
      item.querySelector(".item-title").textContent = reminder.title;
      item.querySelector(".item-sub").textContent = reminder.datetime;
      const del = document.createElement("button");
      del.className = "tiny";
      del.textContent = "削除";
      del.onclick = async () => { await API.deleteReminder(reminder.id); loadReminders(); };
      item.appendChild(del);
      container.appendChild(item);
    });
  }

  async function pollDueReminders() {
    let data;
    try { data = await API.dueReminders(); } catch (err) { return; }
    (data.due || []).forEach(reminder => {
      showToast("⏰ " + reminder.message, 6000);
      if (state.settings.tts_enabled !== false) Speech.speak(reminder.message);
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification("リマインダー", { body: reminder.message });
      }
    });
    if ((data.due || []).length && state.view === "reminders") loadReminders();
  }

  /* ================= 履歴 ================= */

  async function loadHistory() {
    const container = $("#history-list");
    container.innerHTML = "";
    let data;
    try { data = await API.history(50); } catch (err) { container.appendChild(errorBox(err.message)); return; }
    if (!data.items.length) { container.innerHTML = '<p class="empty">履歴はありません。</p>'; return; }

    data.items.forEach(row => {
      const item = document.createElement("div");
      item.className = "item";
      item.innerHTML = '<div><p class="item-title"></p><p class="item-sub"></p></div><span class="item-time"></span>';
      item.querySelector(".item-title").textContent = row.user_message;
      item.querySelector(".item-sub").textContent = row.assistant_message;
      item.querySelector(".item-time").textContent = row.timestamp.slice(5, 16).replace("T", " ");
      container.appendChild(item);
    });
  }

  /* ================= 設定 ================= */

  async function loadSettings() {
    let data, status;
    try {
      data = await API.settings();
      status = await API.status();
    } catch (err) {
      $("#status-panel").innerHTML = "";
      $("#status-panel").appendChild(errorBox(err.message));
      return;
    }
    state.settings = data.settings;

    const rows = [
      ["Gemini API", status.gemini, status.gemini_model],
      ["経路 API", status.routes, status.routes ? "Google Routes" : "キー未設定"],
      ["天気 API", status.weather, "Open-Meteo"],
      ["Google カレンダー", status.google_calendar, status.google_calendar ? "接続済み" : "未接続"],
      ["音声入力", Speech.supported, Speech.supported ? "Web Speech API" : "非対応ブラウザ"],
      ["読み上げ", Speech.ttsSupported, Speech.ttsSupported ? "SpeechSynthesis" : "非対応ブラウザ"],
      ["常時待受", WakeWord.running,
        WakeWord.running ? "「" + (state.settings.wake_word || "").split("/")[0] + "」で反応"
                         : (window.isSecureContext ? "オフ" : "HTTPSが必要")]
    ];
    $("#status-panel").innerHTML = rows.map(row =>
      '<div class="status-row"><span class="' + (row[1] ? "badge-ok" : "badge-off") + '">' +
      (row[1] ? "● " : "○ ") + row[0] + '</span><span>' + row[2] + "</span></div>"
    ).join("");

    Object.entries(state.settings).forEach(([key, value]) => {
      const field = document.querySelector('[name="' + key + '"]');
      if (!field) return;
      if (field.type === "checkbox") field.checked = Boolean(value);
      else field.value = value;
    });
  }

  async function saveSettings(event) {
    event.preventDefault();
    const values = {};
    $$("#settings-form [name]").forEach(field => {
      if (field.type === "checkbox") values[field.name] = field.checked;
      else if (field.type === "number") values[field.name] = Number(field.value || 0);
      else values[field.name] = field.value;
    });
    try {
      const data = await API.saveSettings(values);
      state.settings = data.settings;
      $("#settings-saved").hidden = false;
      setTimeout(() => { $("#settings-saved").hidden = true; }, 2000);

      // 呼びかけの言葉や有効・無効の変更を、その場で待受へ反映する
      WakeWord.stop();
      if (state.settings.wake_word_enabled) startWakeWord();
      updateWakeToggle();

      loadHome();
    } catch (err) {
      showToast(err.message);
    }
  }

  /* ================= 起動 ================= */

  function bindEvents() {
    $$(".tabbar button").forEach(button => {
      button.onclick = () => switchView(button.dataset.view);
    });
    $$("[data-goto]").forEach(card => {
      card.onclick = () => switchView(card.dataset.goto);
    });

    // 最初のタップで音を鳴らせる状態にしておく
    document.addEventListener("click", () => WakeWord.primeAudio(), { once: true });

    $("#orb").onclick = () => {
      // 音声が使えない環境ではテキスト入力にフォールバックする
      if (!window.isSecureContext || !Speech.supported) { switchView("chat"); return; }
      if (Speech.listening) { Speech.stop(); return; }
      // 待受中は呼びかけと同じ流れに乗せる（マイクの奪い合いを避ける）
      if (WakeWord.running) { WakeWord.pause(); runWakeCycle(""); return; }
      startListening(text => ask(text, { fromChat: false }));
    };

    $("#wake-toggle").onclick = () => {
      WakeWord.primeAudio();
      if (WakeWord.running) {
        stopWakeWord({ announce: true });
        API.saveSettings({ wake_word_enabled: false }).catch(() => {});
        state.settings.wake_word_enabled = false;
      } else if (startWakeWord({ announce: true })) {
        API.saveSettings({ wake_word_enabled: true }).catch(() => {});
        state.settings.wake_word_enabled = true;
      }
    };

    $("#chat-mic").onclick = () => startListening(text => {
      $("#chat-input").value = text;
      ask(text, { fromChat: true });
    });

    $("#chat-form").onsubmit = (event) => {
      event.preventDefault();
      const input = $("#chat-input");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      ask(text, { fromChat: true });
    };

    $("#event-form").onsubmit = async (event) => {
      event.preventDefault();
      try {
        await API.addEvent({
          title: $("#event-title").value.trim(),
          start: $("#event-start").value,
          location: $("#event-location").value.trim()
        });
        $("#event-form").reset();
        loadCalendar();
        showToast("予定を追加しました");
      } catch (err) { showToast(err.message); }
    };

    $("#route-form").onsubmit = (event) => {
      event.preventDefault();
      searchRoute({
        origin: $("#route-origin").value.trim(),
        destination: $("#route-destination").value.trim(),
        arrival_time: $("#route-arrival").value
      });
    };

    $("#reminder-form").onsubmit = async (event) => {
      event.preventDefault();
      try {
        const data = await API.addReminder({
          title: $("#reminder-title").value.trim(),
          datetime: $("#reminder-when").value.trim()
        });
        if (!data.ok) { showToast(data.error); return; }
        $("#reminder-form").reset();
        loadReminders();
        showToast(data.datetime + " に通知します");
      } catch (err) { showToast(err.message); }
    };

    $("#history-clear").onclick = async () => {
      await API.clearHistory();
      state.history = [];
      loadHistory();
      showToast("履歴を削除しました");
    };

    $("#settings-form").onsubmit = saveSettings;

    window.addEventListener("offline", () => showToast("インターネットに接続されていません。"));
  }

  async function init() {
    bindEvents();
    updateClock();
    setInterval(updateClock, 10000);

    try {
      const data = await API.settings();
      state.settings = data.settings;
    } catch (err) {
      showToast(err.message);
    }

    // マイクは https か localhost でしか使えない。理由を先に伝えておく。
    if (!window.isSecureContext) {
      $("#orb-label").textContent = "タップして文字で質問";
      $("#wake-toggle").hidden = true;
      showToast("HTTPSで開くと音声入力と常時待受が使えます（README参照）", 5000);
    } else if (!Speech.supported) {
      $("#wake-toggle").hidden = true;
      showToast("このブラウザは音声入力に非対応です。Chromeをお試しください。", 5000);
    } else if (state.settings.wake_word_enabled) {
      startWakeWord();
    } else {
      updateWakeToggle();
    }

    loadHome();
    state.homeTimer = setInterval(() => { if (state.view === "home") loadHome(); }, 5 * 60 * 1000);
    state.dueTimer = setInterval(pollDueReminders, 30 * 1000);

    if ("Notification" in window && Notification.permission === "default") {
      // 通知許可はユーザー操作のタイミングで求める
      $("#orb").addEventListener("click", () => Notification.requestPermission(), { once: true });
    }

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => { /* PWA なしでも動作する */ });
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
