/* ウェイクワード判定のテスト。
 *
 *   node tests/test_wakeword.js
 *
 * 音声認識は「ねえAI」を「ねえエーアイ」「ねえ愛」「ねーAI」などに揺らして返す。
 * どこまで拾い、どこで拾わないかをここで固定しておく。
 */
"use strict";

const fs = require("fs");
const path = require("path");

// ブラウザ用のファイルをそのまま読み込むための最小限のスタブ
global.window = global;
global.document = { addEventListener() {} };
// Node 24 の navigator は読み取り専用。無い場合だけ補う。
if (typeof globalThis.navigator === "undefined") {
  Object.defineProperty(globalThis, "navigator", { value: {}, configurable: true });
}
const source = fs.readFileSync(
  path.join(__dirname, "..", "frontend", "js", "wakeword.js"), "utf8"
);
eval(source);

const WakeWord = global.WakeWord;
const PHRASE = WakeWord.normalize("ねえAI");

let failures = 0;

function check(label, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures++;
  console.log(
    (ok ? "  OK  " : "  NG  ") + label.padEnd(30) +
    (ok ? "" : "期待: " + JSON.stringify(expected) + " / 実際: " + JSON.stringify(actual))
  );
}

function matches(heard) {
  return WakeWord.normalize(heard).indexOf(PHRASE) !== -1;
}

console.log("\n■ 呼びかけとして拾うべきもの");
[
  "ねえAI", "ねえai", "ねえエーアイ", "ネエエーアイ", "ねぇAI", "ねえ愛",
  "ねえアイ", "ねーAI", "ねえ、AI", "ねえAI 明日の天気は？", "ねえエーアイ、今日の予定は"
].forEach(heard => check(heard, matches(heard), true));

console.log("\n■ 拾ってはいけないもの");
[
  "AI", "エーアイ", "今日はいい天気", "ねえ、ちょっと", "あいさつ", "映画", ""
].forEach(heard => check(heard || "(空文字)", matches(heard), false));

console.log("\n■ 呼びかけの後ろに続いた質問の切り出し");
[
  ["ねえAI 明日の天気は？", "明日の天気は？"],
  ["ねえエーアイ、今日の予定は", "今日の予定は"],
  ["ねえAI", ""],
  ["ねーAI 何時に家出たらいい", "何時に家出たらいい"],
  ["ねえ愛、20分後に洗濯物教えて", "20分後に洗濯物教えて"]
].forEach(([heard, expected]) => check(heard, WakeWord._sliceOriginal(heard, PHRASE), expected));

console.log("\n■ 複数の呼びかけを登録した場合");
(function () {
  const phrases = "ねえAI/ヘイAI/おーいAI".split("/").map(p => WakeWord.normalize(p));
  const hit = heard => {
    const value = WakeWord.normalize(heard);
    return phrases.some(phrase => value.indexOf(phrase) !== -1);
  };
  check("ヘイAI", hit("ヘイAI"), true);
  check("へいエーアイ", hit("へいエーアイ"), true);
  check("おーいAI", hit("おーいAI"), true);
  check("おはよう", hit("おはよう"), false);
})();

console.log(
  failures === 0
    ? "\n結果: すべて期待どおり\n"
    : `\n結果: ${failures} 件が期待と違います\n`
);
process.exit(failures === 0 ? 0 : 1);
