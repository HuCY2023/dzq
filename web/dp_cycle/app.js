/* global LightweightCharts */
(function () {
  /** 默认可视窗口：近 N 年（周 K 更可读；仍可拖/滚轮看全历史） */
  const DEFAULT_VIEW_YEARS = 5;
  /** 窗口内目标周 K 根数（过少会巨烛，过多会挤成毛刺） */
  const TARGET_BARS_IN_VIEW = 160;

  const CHART_OPTS = {
    layout: {
      background: { type: "solid", color: "#161b24" },
      textColor: "#9aa4b2",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: "#222833" },
      horzLines: { color: "#222833" },
    },
    rightPriceScale: {
      borderColor: "#2a3140",
      scaleMargins: { top: 0.05, bottom: 0.08 },
      entireTextOnly: true,
      autoScale: true,
    },
    timeScale: {
      borderColor: "#2a3140",
      timeVisible: false,
      secondsVisible: false,
      rightOffset: 4,
      barSpacing: 7,
      minBarSpacing: 3,
      lockVisibleTimeRangeOnResize: true,
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#f5c542", width: 1, style: 2, labelBackgroundColor: "#3a2f12" },
      horzLine: { color: "#3a4556", width: 1, style: 2 },
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
  };

  const state = {
    charts: [],
    syncingRange: false,
    syncingCross: false,
    lastRange: null,
    lastLogical: null,
    weeklyBars: 0,
    _syncUnlockTimer: null,
    /** @type {{paneId:string, points:any[], totalValued:number}[]} */
    paneSeries: [],
    /** @type {{margin:any, etf:any}|null} */
    weeklyOverlaySeries: null,
    /** @type {Record<string,{series:any,points:any[],togId:string,label:string}>|null} */
    marginBoardSeries: null,
  };

  function el(id) {
    return document.getElementById(id);
  }

  let _loadingTimer = null;
  function setLoading(on, text) {
    const node = el("loading");
    const label = el("loadingText");
    if (label && text) label.textContent = text;
    if (node) node.classList.toggle("hidden", !on);
    document.body.classList.toggle("is-loading", !!on);
    // 安全超时：30 秒后自动隐藏，防止 loading 永远不消失
    if (_loadingTimer) { clearTimeout(_loadingTimer); _loadingTimer = null; }
    if (on) {
      _loadingTimer = setTimeout(function () {
        if (node) node.classList.add("hidden");
        document.body.classList.remove("is-loading");
        _loadingTimer = null;
        var qs = el("qualitySummary");
        if (qs && /绘制|加载/.test(qs.textContent || "")) {
          qs.textContent = (qs.textContent || "") + " · 绘制超时已解除遮罩，可再点「从库加载」";
        }
      }, 20000);
    }
  }

  function fmtSpan(sp) {
    if (!sp || !sp.first || !sp.last) return "";
    const a = String(sp.first).slice(2);
    const b = String(sp.last).slice(2);
    return a === b ? a : a + "~" + b;
  }

  function setPaneCoverage(paneId, base, spanKey, data) {
    const pane = el(paneId);
    if (!pane) return;
    const label = pane.querySelector(".pane-label");
    if (!label) return;
    const spans = (data && data.coverage_span) || {};
    const notes = (data && data.coverage_note) || {};
    const sp = spans[spanKey];
    const note = notes[spanKey];
    let text = base;
    const s = fmtSpan(sp);
    if (s) text += " · 源 " + s;
    label.textContent = text;
    label.title = [note, s ? "源覆盖 " + s : ""].filter(Boolean).join(" · ") || text;
  }

  function ensurePaneCov(paneId) {
    const pane = el(paneId);
    if (!pane) return null;
    let node = pane.querySelector(".pane-cov");
    if (!node) {
      node = document.createElement("div");
      node.className = "pane-cov";
      pane.appendChild(node);
    }
    return node;
  }

  /** 缩放后：统计当前可见 logical 窗内有多少「真有数值」的点（whitespace 不计） */
  function refreshPaneVisibleHints() {
    const lr = state.lastLogical;
    state.paneSeries.forEach((ps) => {
      const node = ensurePaneCov(ps.paneId);
      if (!node) return;
      const pts = ps.points || [];
      const nAll = pts.length;
      if (!nAll) {
        node.textContent = "无数据";
        node.classList.add("is-empty-win");
        return;
      }
      let from = 0;
      let to = nAll - 1;
      if (lr && Number.isFinite(lr.from) && Number.isFinite(lr.to)) {
        from = Math.max(0, Math.floor(lr.from));
        to = Math.min(nAll - 1, Math.ceil(lr.to));
      }
      let hit = 0;
      for (let i = from; i <= to; i++) {
        const p = pts[i];
        if (!p) continue;
        const v = p.value != null ? +p.value : +p.close;
        if (Number.isFinite(v)) hit++;
      }
      const total = ps.totalValued || 0;
      if (hit === 0) {
        node.textContent = "本窗无点（源仅 " + total + " 点，拉近到有数据区间）";
        node.classList.add("is-empty-win");
      } else {
        node.textContent = "本窗 " + hit + " 点 / 源 " + total + " 点";
        node.classList.remove("is-empty-win");
      }
    });
  }

  function registerPaneSeries(paneId, points) {
    const valued = (points || []).filter((p) => {
      const v = p && (p.value != null ? +p.value : +p.close);
      return Number.isFinite(v);
    }).length;
    state.paneSeries.push({ paneId: paneId, points: points || [], totalValued: valued });
  }

  const SCORE_PANE = {
    main_force: "pane-mf-w",
    national_team: "pane-etf",
    margin: "pane-margin",
    divergence: "pane-gv",
    breadth: "pane-hl",
    unlock: "pane-unlock-m",
  };

  function applyOverlayToggles() {
    const ser = state.weeklyOverlaySeries;
    if (!ser) return;
    const mgOn = !!(el("togMargin") && el("togMargin").checked);
    const etfOn = !!(el("togEtf") && el("togEtf").checked);
    try {
      if (ser.margin) ser.margin.applyOptions({ visible: mgOn });
      if (ser.etf) ser.etf.applyOptions({ visible: etfOn });
      const wrap = state.charts.find((w) => w.chart && w.chart.__label === "周线");
      if (wrap && wrap.chart) {
        try {
          wrap.chart.priceScale("left").applyOptions({ visible: mgOn });
        } catch (_) {}
        const bits = [];
        if (mgOn && ser.margin && ser.margin.__dataCacheOv) {
          bits.push({ label: "融资", cache: ser.margin.__dataCacheOv });
        }
        if (etfOn && ser.etf && ser.etf.__dataCacheOv) {
          bits.push({ label: "宽基ETF", cache: ser.etf.__dataCacheOv });
        }
        wrap.chart.__overlays = bits;
      }
    } catch (_) {}
  }

  function applyMarginBoardToggles() {
    const bag = state.marginBoardSeries;
    if (!bag) return;
    const visible = [];
    Object.keys(bag).forEach((key) => {
      const item = bag[key];
      const on = !!(el(item.togId) && el(item.togId).checked);
      try {
        item.series.applyOptions({ visible: on });
      } catch (_) {}
      const lab = document.querySelector('#marginToggles .ov-tog[data-mg="' + key + '"]');
      if (lab) lab.classList.toggle("is-off", !on);
      if (on) visible.push(item);
    });
    const wrap = state.charts.find((w) => w.chart && w.chart.__label === "融资");
    if (wrap && wrap.chart) {
      if (visible.length) {
        wrap.chart.__primarySeries = visible[0].series;
        wrap.chart.__dataCache = visible[0].cache || [];
        wrap.chart.__overlays = visible.slice(1).map((v) => ({ label: v.label, cache: v.cache }));
      } else {
        wrap.chart.__dataCache = [];
        wrap.chart.__overlays = [];
      }
    }
    try {
      forceAllPriceAutoscale();
    } catch (_) {}
  }

  function makeMarginBoardAutoscale(chart, opts) {
    const padRatio = (opts && opts.pad) || 0.1;
    return function autoscaleInfoProvider() {
      const bag = state.marginBoardSeries;
      if (!bag) return null;
      const sets = [];
      Object.keys(bag).forEach((key) => {
        const item = bag[key];
        const on = !!(el(item.togId) && el(item.togId).checked);
        if (on && item.points && item.points.length) sets.push(item.points);
      });
      if (!sets.length) return null;
      let from = 0;
      let to = Math.max.apply(
        null,
        sets.map((p) => p.length)
      ) - 1;
      try {
        const lr = chart.timeScale().getVisibleLogicalRange();
        if (lr && Number.isFinite(lr.from) && Number.isFinite(lr.to)) {
          from = Math.max(0, Math.floor(lr.from));
          to = Math.min(to, Math.ceil(lr.to));
        }
      } catch (_) {}
      let lo = Infinity;
      let hi = -Infinity;
      sets.forEach((points) => {
        const b = Math.min(to, points.length - 1);
        for (let i = Math.max(0, from); i <= b; i++) {
          const p = points[i];
          if (!p) continue;
          const v = p.value != null ? +p.value : NaN;
          if (!Number.isFinite(v)) continue;
          lo = Math.min(lo, v);
          hi = Math.max(hi, v);
        }
      });
      if (!(Number.isFinite(lo) && Number.isFinite(hi) && hi >= lo)) return null;
      const pad = (hi - lo) * padRatio || Math.abs(hi) * 0.05 || 1;
      return { priceRange: { minValue: lo - pad, maxValue: hi + pad } };
    };
  }

  function scrollPane(paneId) {
    const node = paneId ? el(paneId) : null;
    if (!node) return;
    node.scrollIntoView({ behavior: "auto", block: "start" });
    document.querySelectorAll(".pane-nav button").forEach((btn) => {
      btn.classList.toggle("is-on", btn.dataset.pane === paneId);
    });
  }

  function focusDates(start, end) {
    const anchors = state.anchors || [];
    if (anchors.length < 2) return;
    const s = String(start || "").slice(0, 10);
    const e = String(end || s).slice(0, 10);
    let from = 0;
    let to = anchors.length - 1;
    for (let i = 0; i < anchors.length; i++) {
      if (anchors[i] >= s) {
        from = i;
        break;
      }
    }
    for (let i = anchors.length - 1; i >= 0; i--) {
      if (anchors[i] <= e) {
        to = i;
        break;
      }
    }
    if (to < from) {
      const swap = from;
      from = to;
      to = swap;
    }
    let a = Math.max(0, from - 6);
    let b = Math.min(anchors.length - 1, to + 6);
    if (b - a < 24) {
      const mid = (a + b) / 2;
      a = Math.max(0, Math.floor(mid - 16));
      b = Math.min(anchors.length - 1, Math.ceil(mid + 16));
    }
    applyLogicalToAll({ from: a - 0.5, to: b + 4 }, null);
    forceAllPriceAutoscale();
    scrollPane("pane-weekly");
  }

  function filterPeriods(phaseId) {
    const wrap = el("periodWrap");
    if (!wrap) return;
    wrap.querySelectorAll("tbody tr").forEach((tr) => {
      const show = !phaseId || tr.dataset.phase === phaseId;
      tr.classList.toggle("hidden", !show);
    });
  }

  function renderDiagnosis(data) {
    const stateNode = el("diagState");
    const summary = el("diagSummary");
    const score = el("diagScore");
    const items = el("diagItems");
    const gaps = el("diagGaps");
    const d = (data && data.diagnosis) || null;
    if (!stateNode) return;
    if (!d) {
      stateNode.textContent = "位置诊断还没有数据";
      return;
    }
    const fw = el("diagFramework");
    if (fw) fw.textContent = d.framework || "散户决策";
    const analysisDate = el("analysisDate");
    if (analysisDate) analysisDate.textContent = d.as_of ? "分析日期 " + d.as_of : "";
    const dec = d.decision || {};
    const dLab = el("decisionLabel");
    if (dLab) {
      dLab.textContent = dec.label || "—";
      dLab.className = "decision-label tone-" + (dec.tone || "neutral");
    }
    const dEq = el("decisionEquity");
    if (dEq) {
      dEq.innerHTML = dec.equity_text
        ? "建议仓位带 <strong>" + dec.equity_text + "</strong>"
        : "";
    }
    const dWhy = el("decisionWhy");
    if (dWhy) dWhy.textContent = dec.why || dec.hint || "";
    const dScale = el("decisionScale");
    if (dScale) {
      dScale.innerHTML = "";
      (d.decision_scale || []).forEach((step) => {
        const span = document.createElement("span");
        span.className = "d-step" + (step.active ? " active" : "");
        span.textContent = (step.label || "") + " " + (step.equity_text || "");
        dScale.appendChild(span);
      });
    }
    stateNode.textContent = d.state ? ("位置：" + d.state) : "";
    if (summary) summary.textContent = d.summary || "";
    const currentWeek = el("currentWeek");
    if (currentWeek) {
      const week = d.current_week || {};
      if (week.period_complete !== true && week.last_observation_date) {
        const total = week.main_force == null ? "" : "，主力累计 " + Number(week.main_force).toFixed(1) + " 亿";
        const completion = week.period_complete === false ? "本周未结束" : "本周是否收盘未核验";
        currentWeek.textContent = "本周截至 " + week.last_observation_date + "（" +
          week.observed_trading_days + " 个交易日）" + total + "；" + completion + "，未按完整周评分。";
      } else {
        currentWeek.textContent = "";
      }
    }

    const allItems = d.items || [];
    const infoItems = allItems.filter((it) => it.status === "info");
    const staleItems = allItems.filter((it) => it.status === "stale");
    const partialItems = allItems.filter((it) => it.status === "partial");
    const inScore = allItems.filter((it) => it.status === "ok" && it.score != null);
    const gapItems = allItems.filter((it) => !["info", "stale", "partial"].includes(it.status) && (it.status === "gap" || it.score == null));
    const total = d.score;
    const fmtSc = (v) => (v == null ? "—" : (v > 0 ? "+" + v : String(v)));

    if (score) {
      score.textContent = (inScore.length ? inScore.length + " 项进分" : "暂无进分项") +
        (infoItems.length ? " · " + infoItems.length + " 项仅观察" : "") +
        " · " + gapItems.length + " 项缺" +
        (staleItems.length ? " · " + staleItems.length + " 项陈旧" : "") +
        (partialItems.length ? " · " + partialItems.length + " 项部分覆盖" : "");
    }
    const big = el("scoreBig");
    if (big) {
      big.textContent = total == null ? "—" : fmtSc(total);
      big.classList.toggle("is-neg", total != null && total < 0);
      big.classList.toggle("is-pos", total != null && total > 0);
    }
    const logic = el("scoreLogic");
    if (logic) {
      logic.textContent = "日常约 ±1/±2；多项同负才会打出极端分（如作者 −9）";
    }

    // —— 打分明细：参与项 / 单项原因 / 汇总公式 ——
    const sumBar = el("scoreSumBar");
    if (sumBar) {
      sumBar.innerHTML =
        "<span class=\"ss-pill in\">进分 <b>" + inScore.length + "</b></span>" +
        (infoItems.length ? "<span class=\"ss-pill\">仅观察 <b>" + infoItems.length + "</b></span>" : "") +
        "<span class=\"ss-pill gap\">缺 <b>" + gapItems.length + "</b></span>" +
        (staleItems.length ? "<span class=\"ss-pill gap\">陈旧 <b>" + staleItems.length + "</b></span>" : "") +
        (partialItems.length ? "<span class=\"ss-pill gap\">部分覆盖 <b>" + partialItems.length + "</b></span>" : "") +
        "<span class=\"ss-pill total " +
        (total != null && total < 0 ? "neg" : total != null && total > 0 ? "pos" : "") +
        "\">汇总 <b>" + fmtSc(total) + "</b></span>";
    }
    const formula = el("scoreFormula");
    if (formula) {
      const parts = allItems.map((it) => {
        if (it.status === "info") {
          return "<span>" + (it.label || it.id) + "(观察)</span>";
        }
        if (it.status === "stale" || it.status === "partial") {
          return "<span class=\"sf-gap\">" + (it.label || it.id) + "(" +
            (it.status === "stale" ? "陈旧" : "部分覆盖") + ")</span>";
        }
        if (it.status === "gap" || it.score == null) {
          return "<span class=\"sf-gap\">" + (it.label || it.id) + "(缺)</span>";
        }
        const cls = it.score < 0 ? "sf-neg" : it.score > 0 ? "sf-pos" : "sf-flat";
        return "<span class=\"" + cls + "\">" + (it.label || it.id) + "(" + fmtSc(it.score) + ")</span>";
      });
      formula.innerHTML = parts.length
        ? parts.join(" <span class=\"sf-op\">+</span> ") +
          " <span class=\"sf-op\">=</span> <strong class=\"sf-total\">" + fmtSc(total) + "</strong>"
        : "";
    }
    const tbody = el("scoreTableBody");
    if (tbody) {
      tbody.innerHTML = "";
      allItems.forEach((it) => {
        const tr = document.createElement("tr");
        const isInfo = it.status === "info";
        const isGap = !isInfo && (it.status === "gap" || it.score == null);
        const statusLabel = isInfo ? "仅观察" : it.status === "stale" ? "陈旧" :
          it.status === "partial" ? "部分覆盖" : isGap ? "缺" : "进分";
        const scCls = isGap || isInfo ? "gap" : it.score < 0 ? "neg" : it.score > 0 ? "pos" : "flat";
        tr.className = "score-row " + scCls + (isGap || isInfo ? " is-gap" : " is-in");
        const paneId = SCORE_PANE[it.id] || "";
        if (paneId) {
          tr.classList.add("is-jump");
          tr.title = "跳到这张图";
          tr.addEventListener("click", () => scrollPane(paneId));
        }
        const reason = (it.note || "").trim() || (isGap ? "无序列，不进分" : "—");
        const ruleTip = it.rule ? "<div class=\"sr-rule\">规则：" + it.rule + "</div>" : "";
        tr.innerHTML =
          "<td><span class=\"sr-badge\">" + statusLabel + "</span></td>" +
          "<td class=\"sr-lab\">" + (it.label || it.id) + "</td>" +
          "<td class=\"sr-sc\">" + (isGap || isInfo ? "—" : fmtSc(it.score)) + "</td>" +
          "<td class=\"sr-why\">" + reason + ruleTip + "</td>";
        tbody.appendChild(tr);
      });
    }
    const hint = el("scoreBoardHint");
    if (hint) {
      hint.textContent = inScore.length
        ? "进分 " + inScore.map((it) => it.label).join("、") +
          (infoItems.length ? " · 仅观察 " + infoItems.map((it) => it.label).join("、") : "") +
          (staleItems.length ? " · 陈旧 " + staleItems.map((it) => it.label).join("、") : "") +
          (partialItems.length ? " · 部分覆盖 " + partialItems.map((it) => it.label).join("、") : "") +
          (gapItems.length ? " · 缺失 " + gapItems.map((it) => it.label).join("、") : "")
        : "有数才进分 · 缺的不记 0 · 点行跳图";
    }

    const triple = el("tripleRow");
    if (triple) {
      triple.innerHTML = "";
      (d.triple || []).forEach((card) => {
        const node = document.createElement("button");
        node.type = "button";
        const dir = card.direction || "";
        const st = card.status === "gap" ? "gap" :
          (card.score != null && card.score < 0 ? "neg" : card.score != null && card.score > 0 ? "pos" : "flat");
        node.className = "triple-card " + st + (dir ? " dir-" + dir : "");
        const paneId = SCORE_PANE[card.id] || "";
        node.innerHTML =
          "<div class=\"tc-lab\">" + (card.label || "") + "</div>" +
          "<div class=\"tc-score\">" + (card.score_text || "—") + "</div>" +
          "<div class=\"tc-role\">" + (card.role || "") + "</div>" +
          "<div class=\"tc-note\">" + (card.note || "") + "</div>";
        node.title = [card.rule, paneId ? "跳到这张图" : ""].filter(Boolean).join(" · ");
        if (paneId) node.addEventListener("click", () => scrollPane(paneId));
        triple.appendChild(node);
      });
    }
    const res = el("resonanceLine");
    if (res) {
      const r = d.resonance || {};
      res.className = "resonance tone-" + (r.tone || "gap");
      res.textContent = r.text || "";
    }

    const grid = el("nodeGrid");
    if (grid) {
      grid.innerHTML = "";
      (d.nodes || []).forEach((nd) => {
        const card = document.createElement("div");
        card.className = "node-card " + (nd.status || "gap");
        card.innerHTML = "<div class=\"nt\">" + (nd.title || "") + "</div><div>" + (nd.text || "") + "</div>";
        grid.appendChild(card);
      });
    }

    const red = d.red_light || {};
    const redGrid = el("redLightGrid");
    if (redGrid) {
      redGrid.innerHTML = "";
      (red.flags || []).forEach((f) => {
        const card = document.createElement("div");
        const unknown = f.status === "unknown" || f.on == null;
        card.className = "red-card " + (unknown ? "unknown" : f.on ? "on" : "off");
        card.innerHTML =
          "<div class=\"rt\">" + (f.label || "") + "</div>" +
          "<div class=\"rs\">" + (unknown ? "未知" : f.on ? "亮" : "灭") + "</div>" +
          "<div class=\"rn\">" + (f.note || "") + "</div>";
        redGrid.appendChild(card);
      });
    }
    const redNote = el("redLightNote");
    if (redNote) {
      redNote.textContent = red.note || "";
      redNote.className = "red-note" + (red.severe ? " severe" : "");
    }
    const redHint = el("redLightHint");
    if (redHint && red.lit != null) {
      redHint.textContent = "亮 " + red.lit + " · 已知 " + (red.known == null ? 4 : red.known) + "/4 · 偏离 · 高低比 · 高杠杆 · 外盘";
    }

    const kType = el("kTypeBlock");
    if (kType) {
      const k = d.k_type || {};
      kType.innerHTML = k.mode
        ? "<strong>" + k.mode + "</strong> — " + (k.hint || "")
        : "";
    }

    const layersHost = el("layersHost");
    if (layersHost) {
      layersHost.innerHTML = "";
      (d.layers || []).forEach((layer) => {
        const block = document.createElement("div");
        block.className = "layer-block";
        const chips = document.createElement("div");
        chips.className = "layer-chips";
        (layer.items || []).forEach((it) => {
          const chip = document.createElement("span");
          const isInfo = it.status === "info";
          const isGap = !isInfo && (it.status === "gap" || it.score == null);
          const scCls = isGap || isInfo ? "gap" : it.score < 0 ? "neg" : it.score > 0 ? "pos" : "flat";
          chip.className = "q-badge " + scCls;
          chip.title = (it.note || "") + (it.rule ? "\n规则：" + it.rule : "");
          chip.textContent =
            (it.label || it.id) +
            (isInfo ? " ·观察" : it.status === "stale" ? " ·陈旧" : it.status === "partial" ? " ·部分覆盖" : isGap ? " ·缺" : " " + (it.score > 0 ? "+" + it.score : String(it.score)));
          chips.appendChild(chip);
        });
        block.innerHTML =
          "<div class=\"lb-title\">" +
          (layer.title || "") +
          " <span>" +
          (layer.hint || "") +
          "</span></div>";
        block.appendChild(chips);
        layersHost.appendChild(block);
      });
    }

    if (items) items.innerHTML = "";

    const rules = el("itemRules");
    if (rules) {
      const monthRows = d.funds_month || [];
      const monthHtml = monthRows.length
        ? "<table class=\"funds-month\"><thead><tr><th>月</th><th>主力净额</th><th>宽基ETF份额变</th><th>融资余额变</th></tr></thead><tbody>" +
          monthRows.slice().reverse().map((r) => {
            const fmt = (v, unit) => v == null || !Number.isFinite(+v) ? "—" :
              ((+v > 0 ? "+" : "") + (+v).toFixed(unit === "份" ? 1 : 0));
            return "<tr><td>" + r.month + "</td><td>" + fmt(r.main_force) +
              "</td><td>" + fmt(r.national_team, "份") + "</td><td>" + fmt(r.margin) + "</td></tr>";
          }).join("") +
          "</tbody></table><div class=\"fm-note\">单位：亿 / 亿份。ETF 为份额变动旁证，≠汇金证金。</div>"
        : "<div class=\"fm-note\">月度对照暂无（主力或融资序列不足）</div>";
      rules.innerHTML =
        "<details><summary>月度资金对照（片子同款）</summary>" + monthHtml + "</details>";
    }
    const pos = el("diagPosition");
    const rail = el("phaseRail");
    const periods = el("periodWrap");
    if (pos) {
      const p = d.position || {};
      const decLab = (d.decision && d.decision.label) || p.decision_label || "";
      pos.innerHTML = p.stage
        ? "图上落在 <strong>" + (p.stage || "") + "</strong>（主导 <strong>" +
          (p.actor || "") + "</strong>）→ 决策 <strong>" + decLab + "</strong> · 仓位 <strong>" +
          (p.equity_text || "") + "</strong><br>" +
          (p.sleeve || "") + (p.basis ? " <span class=\"pos-basis\">依据：" + p.basis + "</span>" : "")
        : "";
    }
    if (rail) {
      rail.innerHTML = "";
      (d.phases || []).forEach((ph) => {
        const node = document.createElement("div");
        node.className = "phase-step " + (ph.side || "") + (ph.active ? " active" : "");
        node.dataset.phase = ph.id || "";
        node.title = "只看这一段";
        node.innerHTML = "<div class=\"who\">" + (ph.actor || "") + "</div><div>" +
          (ph.stage || "") + "</div><div class=\"band\">" + (ph.equity_text || "") + "</div>";
        rail.appendChild(node);
      });
      const active = rail.querySelector(".phase-step.active");
      if (active) rail.scrollLeft = Math.max(0, active.offsetLeft - 12);
      rail.onclick = (ev) => {
        const step = ev.target.closest(".phase-step");
        if (!step) return;
        const on = step.classList.contains("is-filter");
        rail.querySelectorAll(".is-filter").forEach((n) => n.classList.remove("is-filter"));
        if (on) {
          filterPeriods("");
          return;
        }
        step.classList.add("is-filter");
        filterPeriods(step.dataset.phase || "");
      };
    }
    if (periods) {
      const rows = d.periods || [];
      if (!rows.length) {
        periods.innerHTML = "";
        const hint = el("periodHint");
        if (hint) hint.textContent = "";
      } else {
        const body = rows.slice().reverse().map((r) => {
          const side = r.side === "sell" ? "side-sell" : r.side === "buy" ? "side-buy" : "";
          return "<tr data-start=\"" + r.start + "\" data-end=\"" + r.end + "\" data-phase=\"" +
            (r.phase_id || "") + "\"><td>" + r.start + " → " + r.end + "</td><td class=\"" + side + "\">" +
            r.actor + "</td><td>" + r.stage + "</td><td>" + r.equity_text + "</td><td>" +
            (r.basis || "") + "</td></tr>";
        }).join("");
        const hint = el("periodHint");
        if (hint) hint.textContent = "历史阶段：点一行跳图；点路径上的阶段只筛这一段。";
        periods.innerHTML = "<details><summary>历史阶段表</summary><table class=\"period-table\"><thead><tr><th>时期</th><th>主导资金</th><th>阶段</th><th>仓位带</th><th>依据</th></tr></thead><tbody>" +
          body + "</tbody></table></details>";
        periods.onclick = (ev) => {
          const tr = ev.target.closest("tr[data-start]");
          if (!tr || tr.classList.contains("hidden")) return;
          periods.querySelectorAll("tr.is-on").forEach((n) => n.classList.remove("is-on"));
          tr.classList.add("is-on");
          focusDates(tr.dataset.start, tr.dataset.end);
        };
      }
    }
    if (gaps) {
      const list = Array.isArray(d.gaps) ? d.gaps.filter(Boolean) : [];
      gaps.textContent = list.length ? ("扩展缺口：" + list.join("；")) : "";
    }
  }

  function renderQuality(data) {
    const bar = el("qualityBar");
    const sum = el("qualitySummary");
    const host = el("qualityBadges");
    if (!bar || !sum || !host) return;
    const q = (data && data.quality) || {};
    const cov = (data && data.coverage) || {};
    const spans = (data && data.coverage_span) || {};
    const notes = (data && data.coverage_note) || {};
    const overall = q.overall || "na";
    sum.className = "quality-summary " + (overall === "ok" || overall === "warn" || overall === "fail" ? overall : "");
    sum.textContent = q.summary || "交叉验证未运行";
    host.innerHTML = "";
    const badges = Array.isArray(q.badges) ? q.badges : [];
    const idToSpanKey = {
      main_force: "mf_week",
      northbound: "northbound_week",
      northbound_live: "northbound_week",
      national_team: "etf",
      etf: "etf",
      margin: "margin",
      growth_value: "growth_value",
      high_low: "high_low",
      weekly: "weekly",
      unlock: "unlock_month",
      unlock_live: "unlock_month",
    };
    badges.forEach((b) => {
      const span = document.createElement("span");
      const st = b.stable;
      const historical = b.source_status === "historical";
      const partial = b.source_status === "partial";
      span.className = "q-badge " + (historical ? "na" : partial ? "warn" : st === true ? "ok" : st === false ? "warn" : "na");
      const sk = idToSpanKey[b.id] || b.id;
      const rows = b.rows != null ? b.rows : cov[sk];
      const range = fmtSpan(spans[sk]);
      const tip = [b.note, notes[sk], range ? "覆盖 " + range : ""].filter(Boolean).join(" · ");
      span.title = tip;
      span.textContent =
        b.label +
        (range ? " " + range : rows != null ? " " + rows : "") +
        (historical ? " ·历史" : partial ? " ·部分覆盖" : st === true ? " ✓" : st === false ? " !" : "");
      host.appendChild(span);
    });
    if (!badges.length) {
      const keys = [
        ["weekly", "周K"],
        ["mf_month", "月主力"],
        ["mf_week", "周主力"],
        ["northbound_week", "北向"],
        ["etf", "宽基ETF"],
        ["margin", "两融"],
        ["growth_value", "偏离"],
        ["high_low", "广度"],
        ["unlock_month", "月解禁"],
        ["unlock_quarter", "季解禁"],
      ];
      keys.forEach(([k, label]) => {
        const span = document.createElement("span");
        const n = cov[k];
        const range = fmtSpan(spans[k]);
        span.className = "q-badge " + (n > 0 ? "ok" : "na");
        span.title = notes[k] || "";
        span.textContent = label + (range ? " " + range : n != null ? " " + n : "");
        host.appendChild(span);
      });
    }
  }

  function markPaneEmpty(paneId, empty) {
    const pane = el(paneId);
    if (!pane) return;
    pane.classList.toggle("is-empty", !!empty);
    const hint = pane.querySelector(".pane-empty");
    if (hint) hint.classList.toggle("hidden", !empty);
  }

  function toTime(v) {
    if (v == null) return null;
    const s = String(v).slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    return null;
  }

  function mapCandles(rows) {
    if (!rows || !rows.length) return [];
    return rows
      .map((r) => {
        const t = toTime(r.time || r.date);
        if (!t) return null;
        return {
          time: t,
          open: +r.open,
          high: +r.high,
          low: +r.low,
          close: +r.close,
        };
      })
      .filter((x) => x && Number.isFinite(x.close));
  }

  /** 周 K + 未来空白锚点：有 OHLC 画烛，没有就只留时间，避免 setData / 坐标轴被 NaN 弄坏 */
  function sanitizeCandleData(points) {
    return (points || [])
      .map((p) => {
        if (!p || p.time == null) return null;
        const o = +p.open;
        const h = +p.high;
        const l = +p.low;
        const c = +p.close;
        if ([o, h, l, c].every(Number.isFinite)) {
          return { time: p.time, open: o, high: h, low: l, close: c };
        }
        return { time: p.time };
      })
      .filter(Boolean);
  }

  function mapLine(rows, field) {
    if (!rows || !rows.length) return [];
    return rows
      .map((r) => {
        const t = toTime(r.time || r.date);
        const v = +r[field];
        if (!t || !Number.isFinite(v)) return null;
        return { time: t, value: v };
      })
      .filter(Boolean);
  }

  function mapHist(rows, field, opts) {
    const thr = opts && opts.threshold != null ? +opts.threshold : null;
    const okColor = (opts && opts.okColor) || "rgba(90,216,166,0.85)";
    const badColor = (opts && opts.badColor) || "rgba(180,100,60,0.9)";
    const negColor = (opts && opts.negColor) || "rgba(90,216,166,0.75)";
    return mapLine(rows, field).map((p) => {
      let color = p.value >= 0 ? "rgba(91,143,249,0.75)" : negColor;
      if (thr != null && Number.isFinite(thr) && p.value < thr) color = badColor;
      else if (thr != null && Number.isFinite(thr)) color = okColor;
      return { time: p.time, value: p.value, color };
    });
  }

  /** 警戒线用 priceLine，避免另开序列把价格轴撑出大片空白 */
  function addThresholdPriceLine(series, value) {
    if (!series || !Number.isFinite(value)) return;
    try {
      series.createPriceLine({
        price: value,
        color: "#c45c26",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: String(value),
      });
    } catch (_) {}
  }

  /**
   * 按「当前可见 logical」重算价格轴；可选 ref 线仅在离数据不太远时纳入。
   * points: {value|close|high|low|open}[]
   */
  function makeVisibleAutoscale(chart, points, opts) {
    const isCandle = !!(opts && opts.candle);
    const ref = opts && opts.ref != null ? +opts.ref : null;
    const padRatio = (opts && opts.pad) || 0.1;
    return function autoscaleInfoProvider() {
      if (!points || !points.length) return null;
      let from = 0;
      let to = points.length - 1;
      try {
        const lr = chart.timeScale().getVisibleLogicalRange();
        if (lr && Number.isFinite(lr.from) && Number.isFinite(lr.to)) {
          from = Math.max(0, Math.floor(lr.from));
          to = Math.min(points.length - 1, Math.ceil(lr.to));
        }
      } catch (_) {}
      let lo = Infinity;
      let hi = -Infinity;
      for (let i = from; i <= to; i++) {
        const p = points[i];
        if (!p) continue;
        if (isCandle) {
          const o = +p.open;
          const h = +p.high;
          const l = +p.low;
          const c = +p.close;
          if (![o, h, l, c].every(Number.isFinite)) continue;
          lo = Math.min(lo, l, o, c);
          hi = Math.max(hi, h, o, c);
        } else {
          const v = p.value != null ? +p.value : +p.close;
          if (!Number.isFinite(v)) continue;
          lo = Math.min(lo, v);
          hi = Math.max(hi, v);
        }
      }
      if (!(Number.isFinite(lo) && Number.isFinite(hi) && hi > lo)) return null;
      if (Number.isFinite(ref)) {
        const span = hi - lo || Math.abs(hi) * 0.1 || 1;
        // 警戒线离可见数据不太远才纳入，否则只留一点头顶空间
        if (ref >= lo - span * 0.35 && ref <= hi + span * 1.35) {
          lo = Math.min(lo, ref);
          hi = Math.max(hi, ref);
        }
      }
      const pad = (hi - lo) * padRatio || Math.abs(hi) * 0.05 || 1;
      return { priceRange: { minValue: lo - pad, maxValue: hi + pad } };
    };
  }

  function bindVisibleAutoscale(chart, series, points, opts) {
    if (!series) return;
    series.applyOptions({
      autoscaleInfoProvider: makeVisibleAutoscale(chart, points, opts),
    });
    try {
      series.priceScale().applyOptions({ autoScale: true });
    } catch (_) {}
  }

  function createChart(containerId, opts) {
    const host = el(containerId);
    if (!host) return null;
    const showTime = !!(opts && opts.showTimeAxis);
    const chart = LightweightCharts.createChart(host, {
      ...CHART_OPTS,
      width: Math.max(host.clientWidth, 100),
      height: Math.max(host.clientHeight, 80),
      timeScale: {
        ...CHART_OPTS.timeScale,
        visible: showTime,
        borderVisible: showTime,
      },
    });
    state.charts.push({ chart, host, showTime: showTime });
    return chart;
  }

  function bizDay(t) {
    if (t == null) return null;
    if (typeof t === "string") return t.slice(0, 10);
    if (typeof t === "number" && Number.isFinite(t)) {
      const d = new Date(t * 1000);
      if (!Number.isNaN(d.getTime())) return d.toISOString().slice(0, 10);
    }
    if (typeof t === "object" && t.year != null) {
      const m = String(t.month).padStart(2, "0");
      const d = String(t.day).padStart(2, "0");
      return `${t.year}-${m}-${d}`;
    }
    return String(t).slice(0, 10);
  }

  /**
   * 把副图数据对齐到周 K 锚点：无点的位置塞 whitespace。
   * 这样所有图 logical 索引一致，缩放才能真正联动。
   */
  function padToAnchors(points, anchors, snapDays) {
    const maxSnap = (snapDays != null ? snapDays : 40) * 86400000;
    if (!anchors || !anchors.length) return points || [];
    const valued = new Map();
    (points || []).forEach((p) => {
      const t = bizDay(p.time);
      if (!t) return;
      const ms = Date.parse(t);
      if (!Number.isFinite(ms)) return;
      let best = null;
      let bestD = Infinity;
      for (let i = 0; i < anchors.length; i++) {
        const d = Math.abs(Date.parse(anchors[i]) - ms);
        if (d < bestD) {
          bestD = d;
          best = anchors[i];
        }
      }
      if (best != null && bestD <= maxSnap) {
        const row = Object.assign({}, p, { time: best });
        valued.set(best, row);
      }
    });
    return anchors.map((t) => {
      if (valued.has(t)) return valued.get(t);
      return { time: t }; // whitespace：仅时间，无 value
    });
  }

  function unlockSyncSoon() {
    if (state._syncUnlockTimer) clearTimeout(state._syncUnlockTimer);
    state._syncUnlockTimer = setTimeout(() => {
      state.syncingRange = false;
      state._syncUnlockTimer = null;
    }, 60);
  }

  /** 全图统一 logical 窗（锚点对齐后等价于统一时间窗） */
  function applyLogicalToAll(lr, exceptChart) {
    if (!lr || !Number.isFinite(lr.from) || !Number.isFinite(lr.to)) return;
    state.lastLogical = { from: lr.from, to: lr.to };
    state.syncingRange = true;
    state.charts.forEach(({ chart }) => {
      if (exceptChart && chart === exceptChart) return;
      try {
        chart.timeScale().setVisibleLogicalRange(lr);
      } catch (_) {}
    });
    try {
      const master = state.charts[0];
      if (master) {
        const tr = master.chart.timeScale().getVisibleRange();
        if (tr && tr.from != null) {
          state.lastRange = {
            from: bizDay(tr.from),
            to: bizDay(tr.to),
          };
        }
      }
    } catch (_) {}
    unlockSyncSoon();
    refreshPaneVisibleHints();
  }

  function nearestCachePoint(cache, time) {
    if (!cache || !cache.length || time == null) return null;
    const key = bizDay(time) || String(time).slice(0, 10);
    for (let i = 0; i < cache.length; i++) {
      if (cache[i].time === key && Number.isFinite(cache[i].value)) return cache[i];
    }
    const ms = Date.parse(key);
    if (!Number.isFinite(ms)) return null;
    let best = null;
    let bestD = Infinity;
    for (let i = 0; i < cache.length; i++) {
      if (!Number.isFinite(cache[i].value)) continue;
      const d = Math.abs(Date.parse(cache[i].time) - ms);
      if (d < bestD) {
        bestD = d;
        best = cache[i];
      }
    }
    if (best && bestD <= 40 * 86400000) return best;
    return null;
  }

  function wireSync() {
    if (state.charts.length < 2) return;

    // 时间轴：任意面板缩放/平移 → 全部套同一 logical 窗
    state.charts.forEach(({ chart }) => {
      chart.timeScale().subscribeVisibleLogicalRangeChange((lr) => {
        if (state.syncingRange || !lr) return;
        if (
          state.lastLogical &&
          Math.abs(state.lastLogical.from - lr.from) < 0.01 &&
          Math.abs(state.lastLogical.to - lr.to) < 0.01
        ) {
          return;
        }
        applyLogicalToAll(lr, chart);
        forceAllPriceAutoscale();
      });
    });

    // 十字线：同步时间；价格取各图自身点，避免串量纲
    state.charts.forEach(({ chart: source }) => {
      source.subscribeCrosshairMove((param) => {
        if (state.syncingCross) return;
        const rawT = param && param.time != null ? param.time : null;
        const label = el("hoverTime");
        const t = rawT != null ? bizDay(rawT) : null;
        if (label) {
          const bits = [];
          const fmtV = (val) =>
            Math.abs(val) >= 100
              ? Math.round(val).toLocaleString("zh-CN")
              : (Math.round(val * 10) / 10).toString();
          state.charts.forEach(({ chart: target }) => {
            if (!t || !target.__label) return;
            const pt = nearestCachePoint(target.__dataCache, t);
            if (pt && Number.isFinite(pt.value)) {
              bits.push(target.__label + " " + fmtV(pt.value));
            }
            (target.__overlays || []).forEach((ov) => {
              const op = nearestCachePoint(ov.cache, t);
              if (op && Number.isFinite(op.value)) bits.push(ov.label + " " + fmtV(op.value));
            });
          });
          const text = t ? (bits.length ? t + " · " + bits.join(" · ") : "十字线 " + t) : "十字线：—";
          label.textContent = text;
          label.title = text;
        }
        state.syncingCross = true;
        state.charts.forEach(({ chart: target }) => {
          if (target === source) return;
          try {
            if (!t) {
              target.clearCrosshairPosition();
              return;
            }
            const ser = target.__primarySeries;
            const pt = nearestCachePoint(target.__dataCache, t);
            if (ser && pt && Number.isFinite(pt.value)) {
              target.setCrosshairPosition(pt.value, pt.time, ser);
            } else {
              target.clearCrosshairPosition();
            }
          } catch (_) {}
        });
        state.syncingCross = false;
      });
    });
  }

  function cacheSeries(chart, series, points) {
    if (!chart) return;
    chart.__primarySeries = series;
    chart.__dataCache = (points || [])
      .map((p) => ({
        time: p.time,
        value: p.value != null ? p.value : p.close,
      }))
      .filter((p) => p.time != null && Number.isFinite(p.value));
  }

  /** 默认近 DEFAULT_VIEW_YEARS 年；全历史仍可通过缩放拖到 */
  function defaultTimeRange(data) {
    const weekly = (data.weekly || [])
      .map((r) => toTime(r.time || r.date))
      .filter(Boolean);
    if (weekly.length < 2) {
      const keys = [
        "margin",
        "northbound_weekly",
        "growth_value",
        "high_low",
        "main_force_weekly",
        "main_force",
        "etf",
      ];
      let best = [];
      keys.forEach((k) => {
        const ts = (data[k] || [])
          .map((r) => toTime(r.time || r.date))
          .filter(Boolean)
          .sort();
        if (ts.length > best.length) best = ts;
      });
      if (best.length >= 2) return { from: best[0], to: best[best.length - 1] };
      return null;
    }

    const to = weekly[weekly.length - 1];
    const toMs = Date.parse(to);
    const spanMs = DEFAULT_VIEW_YEARS * 365.25 * 86400000;
    let fromIdx = 0;
    for (let i = weekly.length - 1; i >= 0; i--) {
      if (toMs - Date.parse(weekly[i]) >= spanMs) {
        fromIdx = i;
        break;
      }
    }
    // 用根数再夹一次：目标约 TARGET_BARS_IN_VIEW 根，避免巨烛或毛刺
    const byBars = Math.max(0, weekly.length - TARGET_BARS_IN_VIEW);
    fromIdx = Math.max(fromIdx, byBars);
    return { from: weekly[fromIdx], to: to };
  }

  function forceAllPriceAutoscale() {
    state.charts.forEach((wrap) => {
      const ser = wrap.chart && wrap.chart.__primarySeries;
      if (!ser) return;
      try {
        // 先关再开，逼 LWC 按当前可见窗 / provider 重算
        ser.priceScale().applyOptions({ autoScale: false });
        ser.priceScale().applyOptions({ autoScale: true });
      } catch (_) {}
    });
  }

  function fitAll(data) {
    const masterWrap = state.charts[0];
    if (!masterWrap) return;
    const weekly = (data && data.weekly) || [];
    const realN = state.realBars || (Array.isArray(weekly) ? weekly.length : 0);
    const n = (state.anchors && state.anchors.length) || realN;

    state.syncingRange = true;
    try {
      if (n >= 2 && realN >= 2) {
        const from = Math.max(0, realN - TARGET_BARS_IN_VIEW);
        state.weeklyBars = n;
        const lr = { from: from - 0.5, to: n - 1 + 2 };
        state.lastLogical = lr;
        masterWrap.chart.timeScale().setVisibleLogicalRange(lr);
        state.charts.forEach(({ chart }, i) => {
          if (i === 0) return;
          try {
            chart.timeScale().setVisibleLogicalRange(lr);
          } catch (_) {}
        });
      } else {
        const range = defaultTimeRange(data || {});
        if (range) {
          masterWrap.chart.timeScale().setVisibleRange(range);
        } else {
          masterWrap.chart.timeScale().fitContent();
        }
        const lr = masterWrap.chart.timeScale().getVisibleLogicalRange();
        if (lr) {
          state.lastLogical = lr;
          state.charts.forEach(({ chart }, i) => {
            if (i === 0) return;
            try {
              chart.timeScale().setVisibleLogicalRange(lr);
            } catch (_) {}
          });
        }
      }
      try {
        const tr = masterWrap.chart.timeScale().getVisibleRange();
        if (tr && tr.from != null) {
          state.lastRange = { from: bizDay(tr.from), to: bizDay(tr.to) };
        }
      } catch (_) {}
    } catch (_) {
      try {
        masterWrap.chart.timeScale().fitContent();
      } catch (__) {}
    }
    unlockSyncSoon();
    setTimeout(() => forceAllPriceAutoscale(), 90);
  }

  function destroyAll() {
    state.charts.forEach(({ chart }) => {
      try {
        chart.remove();
      } catch (_) {}
    });
    state.charts = [];
    state.lastRange = null;
    state.lastLogical = null;
    state.weeklyOverlaySeries = null;
    state.marginBoardSeries = null;
  }

  function render(payload) {
    const empty = el("empty");
    const data = payload || {};
    state.payload = data;
    setLoading(true, "正在绘制图表…");
    try {
      _renderInner(data, empty);
    } catch (err) {
      console.error("renderDpCycle failed", err);
      const qs = el("qualitySummary");
      if (qs) qs.textContent = "绘制失败: " + (err && err.message ? err.message : String(err));
      setLoading(false);
    }
  }

  function _renderInner(data, empty) {
    const weekly = mapCandles(data.weekly || []);
    const mfMonthRows = data.main_force_monthly || [];
    const mfWeekRows = data.main_force_weekly || data.main_force || [];
    const nbRows = data.northbound_weekly || [];
    const etfRows = data.etf || [];
    const marginRows = data.margin || [];
    const gvRows = data.growth_value || [];
    const hlRows = data.high_low || [];
    const unlockMonthRows = data.unlock_monthly || [];
    const unlockQuarterRows = data.unlock_quarterly || [];
    const hasAny =
      weekly.length ||
      mfMonthRows.length ||
      mfWeekRows.length ||
      nbRows.length ||
      etfRows.length ||
      marginRows.length ||
      gvRows.length ||
      hlRows.length ||
      unlockMonthRows.length ||
      unlockQuarterRows.length;

    const etfFlowText = el("etfFlowText");
    if (etfFlowText) {
      const flow = data.etf_flow || {};
      const coverage = flow.coverage || {};
      if (!flow.date) {
        etfFlowText.textContent = flow.reason || "尚无可用的四基金份额和净值快照。";
      } else {
        const amount = Number.isFinite(Number(flow.flow_yi)) && flow.flow_yi != null
          ? Number(flow.flow_yi).toFixed(2) + " 亿元" : "不可计算";
        const components = (flow.components || []).map((item) =>
          item.code + " " + (item.flow_yi == null ? "缺" : Number(item.flow_yi).toFixed(2))
        ).join("，");
        etfFlowText.textContent = flow.date + " · " + (coverage.comparable || 0) + "/" +
          (coverage.expected || 4) + " 只可比较 · 份额×净值估算 " + amount +
          (components ? "；逐只（亿元）：" + components : "") +
          (flow.reason ? "；" + flow.reason.replace(/[。；]+$/, "") : "") +
          ((flow.source || {}).events_recorded === false ? "；旧导入的份额事件表未留存，版本不可追溯" : "") +
          "。估算可能包含转托管，非实际现金流，也不能识别交易主体；历史不用于可交易回测。该快照需手动重新导入。";
      }
    }
    const holderText = el("etfHolderText");
    if (holderText) {
      const disclosures = data.etf_holder_disclosures || {};
      holderText.textContent = disclosures.display || "尚未核验持有人报告。";
      (disclosures.items || []).forEach((item) => {
        const row = document.createElement("div");
        if (item.status === "not_verified") {
          row.textContent = item.code + "：尚未核验当时可用的报告。";
        } else {
          row.textContent = item.code + "：报告期 " + item.period_end + "，公告 " + item.published_date +
            (item.status === "not_disclosed" ? "，未列实名。" : "，已披露实名。 ");
          const named = item.status === "named_disclosure" ? item : item.last_named;
          if (named && named.direct_shares != null) {
            row.textContent += named.period_end + " 汇金投资与汇金资管直接持有合计 " +
              (Number(named.direct_shares) / 1e8).toFixed(2) + " 亿份（历史快照）。";
          }
        }
        [[item.source_url, " 本期报告"], [(item.last_named || {}).source_url, " 历史报告"]].forEach(([url, label]) => {
          if (url && url.startsWith("https://")) {
            const link = document.createElement("a");
            link.href = url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = label;
            row.appendChild(link);
          }
        });
        holderText.appendChild(row);
      });
    }
    const turnoverText = el("turnoverText");
    if (turnoverText) {
      const turnover = data.turnover_observation || {};
      if (!turnover.date) {
        turnoverText.textContent = turnover.reason || "尚未导入本地成交集中度快照。";
      } else {
        const share = turnover.shares_pct || {};
        turnoverText.textContent = turnover.date + " · 本地有效成交 " + turnover.stocks +
          " 只 · 前1%/3%/5%成交占比 " + ["1", "3", "5"].map((p) => Number(share[p]).toFixed(2) + "%").join(" / ") +
          " · 前5% " + turnover.top_counts["5"] + " 只合计 " + Number(turnover.top5_amount_yi).toFixed(2) + " 亿元" +
          "；源报告请求截止 " + turnover.source_requested_as_of + "，生成于 " +
          (turnover.source_generated_at || "未知").replace("T", " ") + "。" + turnover.note;
      }
    }
    try { renderQuality(data); } catch (e) { console.warn("renderQuality", e); }
    try { renderDiagnosis(data); } catch (e) { console.warn("renderDiagnosis", e); }

    if (!hasAny) {
      if (empty) empty.classList.remove("hidden");
      destroyAll();
      setLoading(false);
      return;
    }
    if (empty) empty.classList.add("hidden");

    destroyAll();
    state.paneSeries = [];

    const sub = el("subtitle");
    if (sub && data.subtitle) {
      sub.textContent = data.subtitle;
      sub.title = data.subtitle;
    }

    markPaneEmpty("pane-weekly", !weekly.length);
    markPaneEmpty("pane-mf-m", !mfMonthRows.length);
    markPaneEmpty("pane-mf-w", !mfWeekRows.length);
    markPaneEmpty("pane-nb", !nbRows.length);
    markPaneEmpty("pane-etf", !etfRows.length);
    markPaneEmpty("pane-margin", !marginRows.length);
    markPaneEmpty("pane-gv", !gvRows.length);
    markPaneEmpty("pane-hl", !hlRows.length);
    markPaneEmpty("pane-unlock-m", !unlockMonthRows.length);
    markPaneEmpty("pane-unlock-q", !unlockQuarterRows.length);

    setPaneCoverage("pane-weekly", "指数周线", "weekly", data);
    setPaneCoverage(
      "pane-mf-m",
      "01 主力 · 月净额（亿）· 线 −4000",
      "mf_month",
      data
    );
    setPaneCoverage(
      "pane-mf-w",
      "01 主力 · 周净额（亿）· 线 −2000",
      "mf_week",
      data
    );
    setPaneCoverage(
      "pane-nb",
      "北向 · 周净买（亿）",
      "northbound_week",
      data
    );
    setPaneCoverage(
      "pane-etf",
      "02 核心宽基ETF份额（亿份）· 不能识别持有人",
      "etf",
      data
    );
    setPaneCoverage("pane-margin", "03 融资余额（亿）· 全市场 + 分板", "margin", data);
    setPaneCoverage("pane-gv", "04 成长/价值偏离", "growth_value", data);
    setPaneCoverage(
      "pane-hl",
      data.high_low_label || "05 新高−新低（右）· 差值（左）",
      "high_low",
      data
    );
    setPaneCoverage(
      "pane-unlock-m",
      "06 解禁 · 月（亿）· 含未来半年 · 柱全市场 线科创",
      "unlock_month",
      data
    );
    setPaneCoverage(
      "pane-unlock-q",
      "06 解禁 · 季（亿）· 含未来半年 · 柱全市场 线科创",
      "unlock_quarter",
      data
    );

    // 周 K 时间锚点：所有副图 pad 到同一根时间轴，才能 logical 联动
    const tail = (data.axis_tail || []).map((t) => toTime(t)).filter(Boolean);
    const realAnchors = weekly.map((c) => c.time).filter(Boolean);
    const anchors = realAnchors.concat(tail.filter((t) => !realAnchors.includes(t)));
    state.realBars = realAnchors.length;
    state.weeklyBars = anchors.length;
    state.anchors = anchors;
    state.payload = data;
    state.weeklyOverlaySeries = null;

    const c0 = createChart("chart-weekly", { showTimeAxis: false });
    if (c0) c0.__label = "周线";
    if (c0) {
      try {
        c0.applyOptions({
          leftPriceScale: {
            visible: true,
            borderColor: "#2a3140",
            scaleMargins: { top: 0.15, bottom: 0.1 },
          },
        });
      } catch (_) {}
      const candle = c0.addCandlestickSeries({
        upColor: "#ef5350",
        downColor: "#26a69a",
        borderUpColor: "#ef5350",
        borderDownColor: "#26a69a",
        wickUpColor: "#ef5350",
        wickDownColor: "#26a69a",
        lastValueVisible: true,
        priceLineVisible: false,
        priceScaleId: "right",
      });
      const weeklyPlot = sanitizeCandleData(padToAnchors(weekly, anchors, 3));
      try {
        candle.setData(weeklyPlot.length ? weeklyPlot : weekly);
      } catch (_) {
        try {
          candle.setData(weekly);
        } catch (__) {}
      }
      bindVisibleAutoscale(c0, candle, weeklyPlot.length ? weeklyPlot : weekly, { candle: true, pad: 0.1 });
      cacheSeries(c0, candle, weekly);
      registerPaneSeries("pane-weekly", weekly);
      markPaneEmpty("pane-weekly", !weekly.length);

      try {
        const marginOv = padToAnchors(mapLine(marginRows, "value"), anchors, 10);
        const etfOv = padToAnchors(mapLine(etfRows, "value"), anchors, 10);
        const mgLine = c0.addLineSeries({
          color: "rgba(146,112,202,0.9)",
          lineWidth: 2,
          priceScaleId: "left",
          lastValueVisible: true,
          priceLineVisible: false,
        });
        mgLine.setData(marginOv);
        const etfLine = c0.addLineSeries({
          color: "rgba(246,189,22,0.95)",
          lineWidth: 2,
          priceScaleId: "etf",
          lastValueVisible: true,
          priceLineVisible: false,
        });
        etfLine.setData(etfOv);
        c0.priceScale("right").applyOptions({
          autoScale: true,
          scaleMargins: { top: 0.08, bottom: 0.12 },
        });
        c0.priceScale("left").applyOptions({
          autoScale: true,
          scaleMargins: { top: 0.15, bottom: 0.1 },
        });
        c0.priceScale("etf").applyOptions({
          autoScale: true,
          visible: false,
          borderVisible: false,
          scaleMargins: { top: 0.12, bottom: 0.12 },
        });
        const mgCache = (marginOv || []).filter((p) => p.time != null && Number.isFinite(+p.value)).map((p) => ({ time: p.time, value: +p.value }));
        const etfCache = (etfOv || []).filter((p) => p.time != null && Number.isFinite(+p.value)).map((p) => ({ time: p.time, value: +p.value }));
        mgLine.__dataCacheOv = mgCache;
        etfLine.__dataCacheOv = etfCache;
        c0.__overlays = [
          { label: "融资", cache: mgCache },
          { label: "宽基ETF", cache: etfCache },
        ];
        state.weeklyOverlaySeries = { margin: mgLine, etf: etfLine };
        applyOverlayToggles();
      } catch (_) {
        c0.__overlays = [];
        state.weeklyOverlaySeries = null;
      }
    }

    // 周 K 下方：打分曲线（−10～10）
    const scoreRows = data.score_curve || (data.diagnosis && data.diagnosis.score_curve) || [];
    markPaneEmpty("pane-score", !scoreRows.length);
    const cScore = createChart("chart-score", { showTimeAxis: true });
    if (cScore) cScore.__label = "打分";
    if (cScore && scoreRows.length) {
      const scorePts = padToAnchors(mapLine(scoreRows, "value"), anchors, 10);
      const scoreLine = cScore.addLineSeries({
        color: "#f5c542",
        lineWidth: 2,
        lastValueVisible: true,
        priceLineVisible: false,
        priceFormat: { type: "price", precision: 0, minMove: 1 },
      });
      scoreLine.setData(scorePts);
      try {
        scoreLine.createPriceLine({
          price: 0,
          color: "#556274",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Solid,
          axisLabelVisible: false,
        });
        scoreLine.createPriceLine({
          price: -2,
          color: "#6a5520",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: "日常下沿",
        });
        scoreLine.createPriceLine({
          price: 2,
          color: "#2f5a42",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: "日常上沿",
        });
      } catch (_) {}
      scoreLine.applyOptions({
        autoscaleInfoProvider: () => ({ priceRange: { minValue: -10, maxValue: 10 } }),
      });
      try {
        scoreLine.priceScale().applyOptions({ autoScale: true, scaleMargins: { top: 0.08, bottom: 0.08 } });
      } catch (_) {}
      cacheSeries(cScore, scoreLine, scorePts);
      registerPaneSeries("pane-score", scorePts);
    }

    const monthLine = data.author_month_line != null ? +data.author_month_line : -4000;
    const weekLine = data.author_week_line != null ? +data.author_week_line : -2000;

    const c1m = createChart("chart-mf-m", { showTimeAxis: false });
    if (c1m) c1m.__label = "主力月";
    if (c1m) {
      const histM = c1m.addHistogramSeries({
        priceFormat: { type: "price", precision: 1, minMove: 0.1 },
      });
      const mfMonth = padToAnchors(
        mapHist(mfMonthRows, "value", { threshold: monthLine }),
        anchors,
        45
      );
      histM.setData(mfMonth);
      bindVisibleAutoscale(c1m, histM, mfMonth, { ref: monthLine, pad: 0.12 });
      addThresholdPriceLine(histM, monthLine);
      cacheSeries(c1m, histM, mfMonth);
      registerPaneSeries("pane-mf-m", mfMonth);
    }

    const c1w = createChart("chart-mf-w", { showTimeAxis: false });
    if (c1w) c1w.__label = "主力周";
    if (c1w) {
      const histW = c1w.addHistogramSeries({
        priceFormat: { type: "price", precision: 1, minMove: 0.1 },
      });
      const mfWeek = padToAnchors(
        mapHist(mfWeekRows, "value", { threshold: weekLine }),
        anchors,
        10
      );
      histW.setData(mfWeek);
      bindVisibleAutoscale(c1w, histW, mfWeek, { ref: weekLine, pad: 0.12 });
      addThresholdPriceLine(histW, weekLine);
      cacheSeries(c1w, histW, mfWeek);
      registerPaneSeries("pane-mf-w", mfWeek);
    }

    const cNb = createChart("chart-nb", { showTimeAxis: false });
    if (cNb) cNb.__label = "北向";
    if (cNb) {
      const histNb = cNb.addHistogramSeries({
        priceFormat: { type: "price", precision: 1, minMove: 0.1 },
      });
      const nbPts = padToAnchors(
        mapHist(nbRows, "value", {
          okColor: "rgba(91,143,249,0.75)",
          negColor: "rgba(244,102,74,0.75)",
        }),
        anchors,
        10
      );
      histNb.setData(nbPts);
      bindVisibleAutoscale(cNb, histNb, nbPts, { pad: 0.12 });
      cacheSeries(cNb, histNb, nbPts);
      registerPaneSeries("pane-nb", nbPts);
    }

    const c2 = createChart("chart-etf", { showTimeAxis: false });
    if (c2) c2.__label = "宽基ETF";
    if (c2) {
      const etf = c2.addLineSeries({ color: "#f6bd16", lineWidth: 2 });
      const etfPts = padToAnchors(mapLine(etfRows, "value"), anchors, 10);
      etf.setData(etfPts);
      bindVisibleAutoscale(c2, etf, etfPts, { pad: 0.12 });
      cacheSeries(c2, etf, etfPts);
      registerPaneSeries("pane-etf", etfPts);
    }

    const c3 = createChart("chart-margin", { showTimeAxis: false });
    if (c3) c3.__label = "融资";
    if (c3) {
      const MG_SPECS = [
        { key: "all", field: "value", color: "#9270ca", width: 2, togId: "togMgAll", label: "全市场" },
        { key: "main", field: "main", color: "#5b8ff8", width: 1, togId: "togMgMain", label: "主板" },
        { key: "sz", field: "sz", color: "#5ad8a6", width: 1, togId: "togMgSz", label: "深市" },
        { key: "star", field: "star", color: "#f6bd16", width: 1, togId: "togMgStar", label: "科创板" },
        { key: "chinext", field: "chinext", color: "#e8684a", width: 1, togId: "togMgChinext", label: "创业板" },
      ];
      const bag = {};
      MG_SPECS.forEach((spec) => {
        const ser = c3.addLineSeries({ color: spec.color, lineWidth: spec.width });
        const pts = padToAnchors(mapLine(marginRows, spec.field), anchors, 10);
        ser.setData(pts);
        const cache = (pts || [])
          .filter((p) => p.time != null && Number.isFinite(+p.value))
          .map((p) => ({ time: p.time, value: +p.value }));
        bag[spec.key] = {
          series: ser,
          points: pts,
          cache: cache,
          togId: spec.togId,
          label: spec.label,
        };
      });
      state.marginBoardSeries = bag;
      const provider = makeMarginBoardAutoscale(c3, { pad: 0.1 });
      Object.keys(bag).forEach((k) => {
        try {
          bag[k].series.applyOptions({ autoscaleInfoProvider: provider });
          bag[k].series.priceScale().applyOptions({ autoScale: true });
        } catch (_) {}
      });
      cacheSeries(c3, bag.all.series, bag.all.points);
      registerPaneSeries("pane-margin", bag.all.points);
      applyMarginBoardToggles();
    }

    const c4 = createChart("chart-gv", { showTimeAxis: false });
    if (c4) c4.__label = "偏离";
    if (c4) {
      const gv = c4.addLineSeries({ color: "#5b8ff8", lineWidth: 2 });
      const gvPts = padToAnchors(mapLine(gvRows, "value"), anchors, 10);
      gv.setData(gvPts);
      bindVisibleAutoscale(c4, gv, gvPts, { ref: 17, pad: 0.12 });
      addThresholdPriceLine(gv, 17);
      cacheSeries(c4, gv, gvPts);
      registerPaneSeries("pane-gv", gvPts);
    }

    // 底图：新高/新低走右轴，差值走左轴（量纲差大会互相压扁）
    const c5 = createChart("chart-hl", { showTimeAxis: false });
    if (c5) c5.__label = "广度";
    if (c5) {
      try {
        c5.applyOptions({
          leftPriceScale: {
            visible: true,
            borderColor: "#2a3140",
            scaleMargins: { top: 0.08, bottom: 0.1 },
            autoScale: true,
          },
        });
      } catch (_) {}
      const hi = c5.addLineSeries({ color: "#ef5350", lineWidth: 1, priceScaleId: "right" });
      const lo = c5.addLineSeries({ color: "#26a69a", lineWidth: 1, priceScaleId: "right" });
      const diff = c5.addLineSeries({
        color: "#9270ca",
        lineWidth: 1,
        priceScaleId: "left",
      });
      const hiPts = padToAnchors(mapLine(hlRows, "new_high"), anchors, 10);
      const loPts = padToAnchors(mapLine(hlRows, "new_low"), anchors, 10);
      const diffPts = padToAnchors(mapLine(hlRows, "diff"), anchors, 10);
      hi.setData(hiPts);
      lo.setData(loPts);
      diff.setData(diffPts);
      hi.applyOptions({
        autoscaleInfoProvider: () => {
          let from = 0;
          let to = hiPts.length - 1;
          try {
            const lr = c5.timeScale().getVisibleLogicalRange();
            if (lr && Number.isFinite(lr.from)) {
              from = Math.max(0, Math.floor(lr.from));
              to = Math.min(hiPts.length - 1, Math.ceil(lr.to));
            }
          } catch (_) {}
          let loV = Infinity;
          let hiV = -Infinity;
          for (let i = from; i <= to; i++) {
            const a = hiPts[i] && hiPts[i].value;
            const b = loPts[i] && loPts[i].value;
            if (Number.isFinite(a)) {
              loV = Math.min(loV, a);
              hiV = Math.max(hiV, a);
            }
            if (Number.isFinite(b)) {
              loV = Math.min(loV, b);
              hiV = Math.max(hiV, b);
            }
          }
          if (!(hiV > loV)) return null;
          const pad = (hiV - loV) * 0.1 || 1;
          return { priceRange: { minValue: loV - pad, maxValue: hiV + pad } };
        },
      });
      lo.applyOptions({ autoscaleInfoProvider: () => null });
      bindVisibleAutoscale(c5, diff, diffPts, { pad: 0.12 });
      try {
        hi.priceScale().applyOptions({ autoScale: true });
        diff.priceScale().applyOptions({ autoScale: true });
      } catch (_) {}
      cacheSeries(c5, hi, hiPts);
      registerPaneSeries("pane-hl", hiPts);
    }

    function drawUnlock(containerId, paneId, rows) {
      const chart = createChart(containerId, { showTimeAxis: containerId === "chart-unlock-q" });
      if (!chart) return;
      chart.__label = containerId === "chart-unlock-q" ? "解禁季" : "解禁月";
      const allPts = padToAnchors(mapHist(rows.map((r) => ({ time: r.time, value: r.all })), "value"), anchors, 12);
      const starPts = padToAnchors(mapLine(rows.map((r) => ({ time: r.time, value: r.star })), "value"), anchors, 12);
      const hist = chart.addHistogramSeries({
        priceFormat: { type: "price", precision: 0, minMove: 1 },
        color: "rgba(91,143,249,0.75)",
      });
      const line = chart.addLineSeries({ color: "#f6bd16", lineWidth: 2 });
      hist.setData(allPts);
      line.setData(starPts);
      bindVisibleAutoscale(chart, hist, allPts, { pad: 0.12 });
      line.applyOptions({ autoscaleInfoProvider: () => null });
      cacheSeries(chart, hist, allPts);
      registerPaneSeries(paneId, allPts);
    }
    drawUnlock("chart-unlock-m", "pane-unlock-m", unlockMonthRows);
    drawUnlock("chart-unlock-q", "pane-unlock-q", unlockQuarterRows);

    wireSync();
    onResize();
    fitAll(data);
    refreshPaneVisibleHints();
    // 布局稳定后再套一次可视窗，避免首屏高度未算准 / 价格轴仍用全历史
    requestAnimationFrame(() => {
      onResize();
      fitAll(data);
      refreshPaneVisibleHints();
      setTimeout(() => {
        onResize();
        fitAll(data);
        forceAllPriceAutoscale();
        refreshPaneVisibleHints();
        setLoading(false);
      }, 150);
    });
  }

  function onResize() {
    state.charts.forEach(({ chart, host }) => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (w > 0 && h > 0) {
        chart.applyOptions({ width: w, height: h });
      }
    });
    // resize 后重套统一 logical 窗
    if (state.syncingRange) return;
    if (state.lastLogical) {
      applyLogicalToAll(state.lastLogical, null);
    }
  }

  window.addEventListener("resize", onResize);
  if (typeof ResizeObserver !== "undefined") {
    let roTimer = null;
    const ro = new ResizeObserver(() => {
      if (roTimer) clearTimeout(roTimer);
      roTimer = setTimeout(onResize, 50);
    });
    [
      "chart-weekly",
      "chart-score",
      "chart-mf-m",
      "chart-mf-w",
      "chart-nb",
      "chart-etf",
      "chart-margin",
      "chart-gv",
      "chart-hl",
      "chart-unlock-m",
      "chart-unlock-q",
    ].forEach((id) => {
      const node = el(id);
      if (node) ro.observe(node);
    });
  }

  const paneNav = el("paneNav");
  if (paneNav) {
    paneNav.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button");
      if (!btn) return;
      if (btn.dataset.reset) {
        if (state.payload) fitAll(state.payload);
        scrollPane("pane-weekly");
        return;
      }
      if (btn.dataset.pane) scrollPane(btn.dataset.pane);
    });
  }

  ["togMargin", "togEtf"].forEach((id) => {
    const node = el(id);
    if (node) node.addEventListener("change", applyOverlayToggles);
  });

  ["togMgAll", "togMgMain", "togMgSz", "togMgStar", "togMgChinext"].forEach((id) => {
    const node = el(id);
    if (node) node.addEventListener("change", applyMarginBoardToggles);
  });

  window.renderDpCycle = render;
  window.setDpCycleLoading = setLoading;
  window.__dpCycleReady = true;
  window.__dpCycleState = state;

  if (window.__DP_CYCLE_PAYLOAD__) {
    render(window.__DP_CYCLE_PAYLOAD__);
  } else {
    el("empty").classList.remove("hidden");
    const qs = el("qualitySummary");
    if (qs) qs.textContent = "等待 GUI「从库加载」或打开带数据的离线页";
  }
})();
