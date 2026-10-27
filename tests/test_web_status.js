const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

class Element {
  constructor() {
    this.innerHTML = "";
    this.textContent = "";
    this.children = [];
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  appendChild(child) { this.children.push(child); }
  addEventListener() {}
}

const names = ["diagState", "diagScore", "scoreBig", "scoreLogic", "scoreSumBar",
  "scoreFormula", "scoreTableBody", "scoreBoardHint", "layersHost", "currentWeek", "empty",
  "redLightGrid", "redLightHint", "redLightNote", "analysisDate"];
const elements = Object.fromEntries(names.map((name) => [name, new Element()]));
const document = {
  getElementById: (name) => elements[name] || null,
  createElement: () => new Element(),
  body: new Element(),
};
const window = { addEventListener() {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/dp_cycle/app.js"), "utf8"), {
  window, document, LightweightCharts: { CrosshairMode: { Normal: 0 } },
  setTimeout: () => 1, clearTimeout() {}, console,
});

window.renderDpCycle({ diagnosis: {
  score: 0,
  as_of: "2026-10-27",
  current_week: { period_end: "2026-10-30", last_observation_date: "2026-10-26",
    observed_trading_days: 1, period_complete: false, main_force: -278.67 },
  red_light: { lit: 0, known: 1, flags: [
    { label: "偏离极值", on: null, status: "unknown" },
    { label: "高杠杆", on: false, status: "off" },
  ] },
  items: [
    { id: "main_force", label: "主力", status: "ok", score: 0 },
    { id: "us_crowding", label: "美股拥挤度", status: "info", score: null, note: "有数据" },
    { id: "iv", label: "隐含波动率", status: "gap", score: null },
    { id: "old", label: "旧融资", status: "stale", score: null },
    { id: "partial", label: "部分ETF", status: "partial", score: null },
  ],
  layers: [{ items: [{ id: "us_crowding", label: "美股拥挤度", status: "info", score: null }] }],
} });

assert.match(elements.diagScore.textContent, /1 项进分.*1 项仅观察.*1 项缺/);
assert.match(elements.scoreTableBody.children[1].innerHTML, /仅观察/);
assert.doesNotMatch(elements.scoreTableBody.children[1].innerHTML, />缺</);
assert.match(elements.scoreTableBody.children[2].innerHTML, />缺</);
assert.match(elements.scoreTableBody.children[3].innerHTML, />陈旧</);
assert.match(elements.scoreTableBody.children[4].innerHTML, />部分覆盖</);
assert.match(elements.scoreFormula.innerHTML, /美股拥挤度\(观察\)/);
assert.match(elements.scoreBoardHint.textContent, /仅观察.*美股拥挤度/);
assert.match(elements.currentWeek.textContent, /2026-10-26.*1.*交易日.*未结束/);
assert.match(elements.analysisDate.textContent, /分析日期.*2026-10-27/);
assert.match(elements.redLightGrid.children[0].innerHTML, />未知</);
assert.match(elements.redLightGrid.children[1].innerHTML, />灭</);
assert.match(elements.redLightHint.textContent, /已知 1\/4/);
window.renderDpCycle({ diagnosis: { current_week: {
  period_end: "2026-10-30", last_observation_date: "2026-10-30",
  observed_trading_days: 5, period_complete: null, main_force: -2500,
} } });
assert.match(elements.currentWeek.textContent, /2026-10-30.*5.*收盘.*未核验/);
console.log("web status display: ok");
