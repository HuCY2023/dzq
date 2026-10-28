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
  "redLightGrid", "redLightHint", "redLightNote", "analysisDate", "etfFlowText", "etfHolderText", "turnoverText"];
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

window.renderDpCycle({ turnover_observation: {status: "partial", date: "2026-10-22", stocks: 5568,
  source_requested_as_of: "2026-10-26", top_counts: {"1": 56, "3": 168, "5": 279},
  shares_pct: {"1": 20.3854, "3": 35.4228, "5": 44.7365}, top5_amount_yi: 6892.4342,
  note: "证券名录未核对，不参与评分，成交额不是净流入。"}, etf_holder_disclosures: {coverage: {verified_reports: 4, expected: 4},
  display: "已核验基金报告 4/4 只；未披露不等于零，历史快照不能作为当前持仓。", items: [
  {code: "510300", status: "not_disclosed", period_end: "2026-06-30", published_date: "2026-08-29",
   source_url: "https://example.com/current.pdf", last_named: {period_end: "2025-12-31", direct_shares: 73513073833, source_url: "https://example.com/prior.pdf"}},
  {code: "159919", status: "not_disclosed", period_end: "2026-06-30", published_date: "2026-08-31",
   source_url: "https://static.cninfo.com.cn/example.pdf", last_named: {period_end: "2025-12-31", direct_shares: 35498494985}}]},
  etf_flow: {status: "partial", date: "2026-10-26", flow_yi: 217.1622, source: {events_recorded: false},
  coverage: {comparable: 4, expected: 4}, reason: "截至日覆盖未知",
  components: [{code: "159919", flow_yi: 18.2621}]}, diagnosis: {
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
assert.match(elements.etfFlowText.textContent, /2026-10-26.*4\/4.*217\.16/);
assert.match(elements.etfFlowText.textContent, /截至日覆盖未知/);
assert.match(elements.etfFlowText.textContent, /估算.*非.*现金流/);
assert.match(elements.etfFlowText.textContent, /事件表.*未留存/);
assert.match(elements.etfFlowText.textContent, /手动.*导入/);
assert.match(elements.etfHolderText.textContent, /4\/4.*不能.*当前/);
assert.match(elements.etfHolderText.children[0].textContent, /510300.*2026-06-30.*2026-08-29.*未列实名.*2025-12-31.*735\.13.*历史/);
assert.equal(elements.etfHolderText.children[0].children[0].href, "https://example.com/current.pdf");
assert.equal(elements.etfHolderText.children[0].children[1].href, "https://example.com/prior.pdf");
assert.match(elements.etfHolderText.children[1].textContent, /159919.*2026-08-31.*354\.98.*历史/);
assert.match(elements.turnoverText.textContent, /2026-10-22.*5568.*20\.39%.*35\.42%.*44\.74%.*6892\.43/);
assert.match(elements.turnoverText.textContent, /2026-10-26.*证券名录.*不参与评分.*不是净流入/);
assert.match(elements.redLightGrid.children[0].innerHTML, />未知</);
assert.match(elements.redLightGrid.children[1].innerHTML, />灭</);
assert.match(elements.redLightHint.textContent, /已知 1\/4/);
window.renderDpCycle({ diagnosis: { current_week: {
  period_end: "2026-10-30", last_observation_date: "2026-10-30",
  observed_trading_days: 5, period_complete: null, main_force: -2500,
} } });
assert.match(elements.currentWeek.textContent, /2026-10-30.*5.*收盘.*未核验/);
console.log("web status display: ok");
