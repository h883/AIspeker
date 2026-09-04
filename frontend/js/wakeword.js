/* ウェイクワード常時待受（仕様書 20章）。
   「ねえAI」と呼びかけると質問の聞き取りを始める。

   検出はすべてこの端末のブラウザ内で行い、検出前の音声を
   こちらのサーバーや Gemini へ送ることはない。

   注意: 端末内といっても Chrome の音声認識自体は Google の
   サーバーを使う。完全にオフラインで待受したい場合は
   Raspberry Pi に USB マイクを付けて openWakeWord を動かす構成になる
   （README のウェイクワードの節を参照）。 */
(function (global) {
  "use strict";

  const Recognition = global.SpeechRecognition || global.webkitSpeechRecognition;

  /* ---------- 日本語のゆれを吸収する ---------- */

  const SMALL_KANA = { "ぁ": "あ", "ぃ": "い", "ぅ": "う", "ぇ": "え", "ぉ": "お",
                       "っ": "つ", "ゃ": "や", "ゅ": "ゆ", "ょ": "よ", "ゎ": "わ" };

  // 長音符を直前の文字の母音に開く（「ねーAI」を「ねえAI」と同じ形にするため）
  const VOWEL_OF = {};
  [["あ", "あかさたなはまやらわがざだばぱ"],
   ["い", "いきしちにひみりぎじぢびぴ"],
   ["う", "うくすつぬふむゆるぐずづぶぷ"],
   ["え", "えけせてねへめれげぜでべぺ"],
   ["お", "おこそとのほもよろをごぞどぼぽ"]].forEach(([vowel, row]) => {
    for (const ch of row) VOWEL_OF[ch] = vowel;
  });

  // 「AI」は エーアイ / アイ / 愛 / eye などに化けるので、どれも ai に寄せる。
  // 長いパターンから順に当てないと「ねえエーアイ」の「え」を食ってしまう。
  const AI_READINGS = [
    [/ええあい/g, "ai"],
    [/えいあい/g, "ai"],
    [/愛/g, "ai"],
    [/eye/g, "ai"],
    [/あい/g, "ai"]
  ];

  function normalize(text) {
    if (!text) return "";
    let value = text.normalize("NFKC").toLowerCase();

    // カタカナ → ひらがな
    value = value.replace(/[ァ-ヶ]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0x60));
    // 小書き文字 → 通常の文字
    value = value.replace(/[ぁぃぅぇぉっゃゅょゎ]/g, ch => SMALL_KANA[ch] || ch);
    // 長音符を母音に開く（削るのではなく開くことで「ねー」と「ねえ」が揃う）
    value = value.replace(/[ー〜~]/g, (_, index, whole) => VOWEL_OF[whole[index - 1]] || "");
    // 記号・空白を落とす
    value = value.replace(/[\s.,、。!！?？「」『』・]/g, "");

    AI_READINGS.forEach(pair => { value = value.replace(pair[0], pair[1]); });
    return value;
  }

  /** 設定文字列（"ねえAI/ヘイAI" のようにスラッシュ区切り）を照合用の配列にする。 */
  function parsePhrases(raw) {
    return String(raw || "")
      .split("/")
      .map(part => normalize(part))
      .filter(part => part.length >= 2);
  }

  /* ---------- 反応音 ---------- */

  let audioContext = null;

  /** 最初のタップで AudioContext を起こしておく（無操作だと音が鳴らせないため）。 */
  function primeAudio() {
    try {
      const Ctx = global.AudioContext || global.webkitAudioContext;
      if (!Ctx) return;
      if (!audioContext) audioContext = new Ctx();
      if (audioContext.state === "suspended") audioContext.resume();
    } catch (err) {
      /* 音が鳴らせなくても待受自体は動く */
    }
  }

  function chime() {
    try {
      const Ctx = global.AudioContext || global.webkitAudioContext;
      if (!Ctx) return;
      if (!audioContext) audioContext = new Ctx();
      if (audioContext.state === "suspended") audioContext.resume();

      const now = audioContext.currentTime;
      [[880, 0], [1320, 0.09]].forEach(([frequency, offset]) => {
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        oscillator.type = "sine";
        oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0.0001, now + offset);
        gain.gain.exponentialRampToValueAtTime(0.15, now + offset + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.14);
        oscillator.connect(gain).connect(audioContext.destination);
        oscillator.start(now + offset);
        oscillator.stop(now + offset + 0.16);
      });
    } catch (err) {
      /* 音が鳴らせなくても待受自体は続ける */
    }
  }

  /* ---------- 常時待受エンジン ---------- */

  const WakeWord = {
    supported: Boolean(Recognition),
    running: false,      // 待受を有効にしているか
    paused: false,       // 聞き取り・読み上げ中で一時停止しているか
    normalize: normalize,
    chime: chime,
    primeAudio: primeAudio,

    _recognition: null,
    _phrases: [],
    _onDetect: null,
    _onError: null,
    _onHeard: null,
    _restartTimer: null,
    _pendingTimer: null,
    _failures: 0,
    _startedAt: 0,
    _wakeLock: null,

    /**
     * 待受を開始する。
     * options: { phrase, onDetect(rest), onHeard(text), onError(message) }
     */
    start: function (options) {
      options = options || {};
      if (!this.supported) {
        options.onError && options.onError("この端末は常時待受に対応していません。Chromeをお試しください。");
        return false;
      }
      if (!global.isSecureContext) {
        options.onError && options.onError("常時待受にはHTTPS接続が必要です。");
        return false;
      }

      this._phrases = parsePhrases(options.phrase || "ねえAI");
      if (!this._phrases.length) {
        options.onError && options.onError("ウェイクワードが短すぎます。2文字以上にしてください。");
        return false;
      }
      this._onDetect = options.onDetect || null;
      this._onHeard = options.onHeard || null;
      this._onError = options.onError || null;

      this.running = true;
      this.paused = false;
      this._failures = 0;
      this._acquireWakeLock();
      this._listen();
      return true;
    },

    stop: function () {
      this.running = false;
      this.paused = false;
      clearTimeout(this._restartTimer);
      this._abort();
      this._releaseWakeLock();
    },

    /** 質問の聞き取りや読み上げの間だけマイクを譲る。 */
    pause: function () {
      if (!this.running || this.paused) return;
      this.paused = true;
      clearTimeout(this._restartTimer);
      this._abort();
    },

    resume: function () {
      if (!this.running || !this.paused) return;
      this.paused = false;
      this._failures = 0;
      // マイクの解放を待ってから握り直す
      this._restartTimer = setTimeout(() => this._listen(), 350);
    },

    /** 呼びかけを検知した。マイクを譲ってから通知する。 */
    _fire: function (spokenRest) {
      clearTimeout(this._pendingTimer);
      this._pendingTimer = null;
      if (this.paused || !this.running) return;
      this.pause();
      this._onDetect && this._onDetect(spokenRest);
    },

    _abort: function () {
      clearTimeout(this._pendingTimer);
      this._pendingTimer = null;
      const recognition = this._recognition;
      this._recognition = null;
      if (!recognition) return;
      recognition.onend = null;
      recognition.onerror = null;
      recognition.onresult = null;
      try { recognition.abort(); } catch (err) { /* 既に停止済み */ }
    },

    _scheduleRestart: function (delay) {
      if (!this.running || this.paused) return;
      clearTimeout(this._restartTimer);
      this._restartTimer = setTimeout(() => this._listen(), delay);
    },

    _listen: function () {
      if (!this.running || this.paused || this._recognition) return;

      const recognition = new Recognition();
      recognition.lang = "ja-JP";
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

      recognition.onstart = () => {
        this._startedAt = Date.now();
        this._failures = 0;
      };

      recognition.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          const heard = result[0].transcript;
          this._onHeard && this._onHeard(heard);

          const normalized = normalize(heard);
          const matched = this._phrases.find(phrase => normalized.indexOf(phrase) !== -1);
          if (!matched) continue;

          // 「ねえAI、明日の天気は？」のように続けて話した分を拾う
          const position = normalized.indexOf(matched);
          const rest = normalized.slice(position + matched.length);

          if (result.isFinal) {
            const spokenRest = rest.length >= 3 ? this._sliceOriginal(heard, matched) : "";
            this._fire(spokenRest);
            return;
          }

          if (rest.length >= 3) {
            // まだ話している途中。最後まで聞いてから処理するため、単独呼びかけの発火は取り消す
            clearTimeout(this._pendingTimer);
            this._pendingTimer = null;
            return;
          }

          // 呼びかけだけが聞こえた状態。少し待って続きが来なければ反応する
          if (!this._pendingTimer) {
            this._pendingTimer = setTimeout(() => {
              this._pendingTimer = null;
              this._fire("");
            }, 900);
          }
          return;
        }
      };

      recognition.onerror = (event) => {
        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          this.stop();
          this._onError && this._onError("マイクの使用が許可されていないため、常時待受を停止しました。");
          return;
        }
        if (event.error === "aborted") return;   // pause / stop によるもの
        // no-speech と network は待受では日常的に起きるので、黙って復帰する
        this._failures += 1;
      };

      recognition.onend = () => {
        this._recognition = null;
        if (!this.running || this.paused) return;

        // すぐ終わるのが続く場合は間隔を空けて暴走を防ぐ
        const lasted = Date.now() - this._startedAt;
        if (lasted < 500) this._failures += 1; else this._failures = 0;

        if (this._failures >= 8) {
          this.stop();
          this._onError && this._onError("常時待受を維持できませんでした。設定から入れ直してください。");
          return;
        }
        this._scheduleRestart(Math.min(300 * Math.pow(2, this._failures), 8000));
      };

      this._recognition = recognition;
      try {
        recognition.start();
      } catch (err) {
        // 直前のインスタンスがまだ掴んでいる場合はここに来る
        this._recognition = null;
        this._failures += 1;
        this._scheduleRestart(600);
      }
    },

    /** 正規化前のテキストから、ウェイクワードより後ろの部分を取り出す。 */
    _sliceOriginal: function (heard, normalizedPhrase) {
      // 正規化で文字数が変わるため、先頭から少しずつ伸ばして対応位置を探す
      for (let cut = 1; cut <= heard.length; cut++) {
        if (normalize(heard.slice(0, cut)).indexOf(normalizedPhrase) !== -1) {
          return heard.slice(cut).replace(/^[\s、。,.・!！?？]+/, "").trim();
        }
      }
      return "";
    },

    /* 画面が消えると待受も止まるため、点灯を維持する */
    _acquireWakeLock: async function () {
      if (!("wakeLock" in navigator) || this._wakeLock) return;
      try {
        this._wakeLock = await navigator.wakeLock.request("screen");
        this._wakeLock.addEventListener("release", () => { this._wakeLock = null; });
      } catch (err) {
        /* 取得できなくても待受は動く */
      }
    },

    _releaseWakeLock: function () {
      if (!this._wakeLock) return;
      try { this._wakeLock.release(); } catch (err) { /* 解放済み */ }
      this._wakeLock = null;
    }
  };

  // 画面に戻ってきたら待受と画面点灯を張り直す
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !WakeWord.running) return;
    WakeWord._acquireWakeLock();
    if (!WakeWord.paused && !WakeWord._recognition) WakeWord._scheduleRestart(300);
  });

  global.WakeWord = WakeWord;
})(window);
