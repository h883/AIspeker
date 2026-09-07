/* Raspberry Pi 上のバックエンドとの通信をまとめる。
   APIキーは一切フロントに持たない（仕様書 21章）。 */
(function (global) {
  "use strict";

  async function request(path, options) {
    let response;
    try {
      response = await fetch(path, options);
    } catch (err) {
      // オフライン・Pi 停止時（仕様書 23章）
      throw new Error(navigator.onLine
        ? "サーバーに接続できません。Raspberry Piが起動しているか確認してください。"
        : "インターネットに接続されていません。");
    }
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      if (!response.ok) throw new Error("サーバーエラー (" + response.status + ")");
      return {};
    }
    const data = await response.json();
    if (!response.ok && !("ok" in data)) {
      throw new Error(data.error || data.detail || "サーバーエラー (" + response.status + ")");
    }
    return data;
  }

  const json = (method, body) => ({
    method: method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  global.API = {
    health:        ()               => request("/api/health"),
    status:        ()               => request("/api/status"),
    home:          ()               => request("/api/home"),
    chat:          (message, hist)  => request("/api/chat", json("POST", { message: message, history: hist || [] })),
    history:       (limit)          => request("/api/history?limit=" + (limit || 50)),
    clearHistory:  ()               => request("/api/history", { method: "DELETE" }),
    calendar:      (days)           => request("/api/calendar?days=" + (days || 7)),
    addEvent:      (event)          => request("/api/calendar/local", json("POST", event)),
    deleteEvent:   (id)             => request("/api/calendar/local/" + id, { method: "DELETE" }),
    weather:       (loc, date)      => request("/api/weather?" + new URLSearchParams(
                                        Object.entries({ location: loc, date: date }).filter(e => e[1])
                                      )),
    route:         (params)         => request("/api/route?" + new URLSearchParams(
                                        Object.entries(params || {}).filter(e => e[1])
                                      )),
    reminders:     ()               => request("/api/reminders"),
    addReminder:   (r)              => request("/api/reminders", json("POST", r)),
    deleteReminder:(id)             => request("/api/reminders/" + id, { method: "DELETE" }),
    dueReminders:  ()               => request("/api/reminders/due"),
    settings:      ()               => request("/api/settings"),
    saveSettings:  (values)         => request("/api/settings", json("PUT", { values: values })),

    /* --- 初回セットアップ --- */
    setupState:    ()               => request("/api/setup"),
    testGemini:    (key, model)     => request("/api/setup/test-gemini",
                                        json("POST", { api_key: key, model: model })),
    saveKeys:      (keys)           => request("/api/setup/keys", json("POST", keys)),
    saveProfile:   (values)         => request("/api/setup/profile", json("POST", { values: values })),
    completeSetup: ()               => request("/api/setup/complete", { method: "POST" }),
    reopenSetup:   ()               => request("/api/setup/reopen", { method: "POST" })
  };
})(window);
