/* 音声入出力。端末内の Web Speech API を使うため、
   ウェイクワード待ちの音声を外部へ送らない（仕様書 20章・24章）。 */
(function (global) {
  "use strict";

  const Recognition = global.SpeechRecognition || global.webkitSpeechRecognition;

  const Speech = {
    supported: Boolean(Recognition),
    ttsSupported: "speechSynthesis" in global,
    listening: false,
    _recognition: null,
    _voice: null,

    /* --- 音声認識 --- */
    listen: function (handlers) {
      handlers = handlers || {};
      if (!Recognition) {
        handlers.onerror && handlers.onerror("この端末は音声入力に対応していません。Chromeをお試しください。");
        return;
      }
      if (this.listening) { this.stop(); return; }

      const recognition = new Recognition();
      recognition.lang = "ja-JP";
      recognition.interimResults = true;
      recognition.continuous = false;
      recognition.maxAlternatives = 1;

      let finalText = "";

      recognition.onstart = () => {
        this.listening = true;
        handlers.onstart && handlers.onstart();
      };
      recognition.onresult = (event) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) finalText += result[0].transcript;
          else interim += result[0].transcript;
        }
        handlers.oninterim && handlers.oninterim(finalText + interim);
      };
      recognition.onerror = (event) => {
        this.listening = false;
        const messages = {
          "no-speech": "音声が聞き取れませんでした。",
          "not-allowed": "マイクの使用が許可されていません。ブラウザの設定を確認してください。",
          "service-not-allowed": "マイクの使用が許可されていません。",
          "audio-capture": "マイクが見つかりませんでした。",
          "network": "音声認識サービスに接続できませんでした。"
        };
        handlers.onerror && handlers.onerror(messages[event.error] || ("音声認識に失敗しました（" + event.error + "）。"));
      };
      recognition.onend = () => {
        this.listening = false;
        handlers.onend && handlers.onend(finalText.trim());
      };

      this._recognition = recognition;
      try {
        recognition.start();
      } catch (err) {
        this.listening = false;
        handlers.onerror && handlers.onerror("音声入力を開始できませんでした。");
      }
    },

    stop: function () {
      if (this._recognition) {
        try { this._recognition.stop(); } catch (err) { /* 既に停止済み */ }
      }
      this.listening = false;
    },

    /* --- 読み上げ --- */
    pickVoice: function () {
      if (!this.ttsSupported) return null;
      if (this._voice) return this._voice;
      const voices = global.speechSynthesis.getVoices() || [];
      this._voice = voices.find(v => v.lang === "ja-JP")
                 || voices.find(v => v.lang && v.lang.indexOf("ja") === 0)
                 || null;
      return this._voice;
    },

    speak: function (text, options) {
      options = options || {};
      if (!this.ttsSupported || !text) return;
      global.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "ja-JP";
      utterance.rate = options.rate || 1.0;
      const voice = this.pickVoice();
      if (voice) utterance.voice = voice;
      if (options.onend) utterance.onend = options.onend;
      global.speechSynthesis.speak(utterance);
    },

    cancelSpeech: function () {
      if (this.ttsSupported) global.speechSynthesis.cancel();
    }
  };

  if (Speech.ttsSupported) {
    // 音声リストは非同期に読み込まれることがある
    global.speechSynthesis.onvoiceschanged = () => { Speech._voice = null; Speech.pickVoice(); };
  }

  global.Speech = Speech;
})(window);
