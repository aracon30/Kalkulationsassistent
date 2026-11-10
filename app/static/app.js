/* HETA Kalkulationsassistent – Vanilla JavaScript, keine externen Abhängigkeiten */
(function () {
  "use strict";

  function fmt(v, d) {
    if (v === null || v === undefined || v === "") return "–";
    return Number(v).toLocaleString("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---------- Import-Center: Jobfortschritt ---------- */
  function pollJobs() {
    var status = document.getElementById("job-status");
    var bar = document.getElementById("job-bar");
    var cur = document.getElementById("job-current");
    var wasBusy = null;
    function tick() {
      fetch("/api/jobs/current", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var j = data.job;
          if (!j) { status.textContent = "Noch kein Job gestartet."; return; }
          var label = (j.type === "scan" ? "Scan" : "Import") + " #" + j.id + ": " + j.status;
          if (j.progress_total) label += " – " + j.progress_done + " / " + j.progress_total;
          if (!j.running) label += " (Ende " + (j.finished_at || "").replace("T", " ") + ")";
          status.textContent = label;
          var pct = j.progress_total ? Math.round((j.progress_done / j.progress_total) * 100) : (j.running ? 5 : 100);
          bar.style.width = pct + "%";
          cur.textContent = j.running ? (j.current_item || "") : "";
          document.querySelectorAll("button[type=submit]").forEach(function (b) {
            if (b.id === "cancel-btn") b.disabled = !data.busy;
            else if (b.closest("form[action^='/import/']")) b.disabled = data.busy;
          });
          if (wasBusy === true && !data.busy) { window.location.reload(); return; }
          wasBusy = data.busy;
          setTimeout(tick, data.busy ? 1000 : 4000);
        })
        .catch(function () { setTimeout(tick, 5000); });
    }
    tick();
  }

  /* ---------- PDF-Vorschau (serverseitig gerenderte Seiten) ---------- */
  function initViewer() {
    var viewer = document.getElementById("viewer");
    var select = document.getElementById("doc-select");
    if (!viewer) return;
    var img = document.getElementById("viewer-img");
    // scale 1 = Seitenbreite eingepasst; Server rendert passend aufgelöst nach
    var state = { doc: viewer.dataset.doc, page: 1, scale: 1, pages: Number(document.getElementById("pgs").textContent) || 1 };
    function render() {
      var renderZoom = Math.min(4, Math.max(1, +(1.5 * state.scale).toFixed(2)));
      img.src = "/dokumente/" + state.doc + "/seite/" + state.page + ".png?zoom=" + renderZoom;
      img.style.width = (state.scale * 100) + "%";
      document.getElementById("pg").textContent = state.page;
      document.getElementById("pgs").textContent = state.pages;
      document.getElementById("zoom-label").textContent = state.scale === 1 ? "Breite" : Math.round(state.scale * 100) + " %";
      document.getElementById("open-pdf").href = "/dokumente/" + state.doc + "/datei";
      var ai = document.getElementById("ai-link");
      if (ai) ai.href = "/dokumente/" + state.doc + "/ki";
    }
    document.querySelectorAll(".viewer-tools [data-act]").forEach(function (b) {
      b.addEventListener("click", function () {
        var a = b.dataset.act;
        if (a === "prev" && state.page > 1) state.page--;
        if (a === "next" && state.page < state.pages) state.page++;
        if (a === "zoomin") state.scale = Math.min(4, +(state.scale + 0.25).toFixed(2));
        if (a === "zoomout") state.scale = Math.max(0.5, +(state.scale - 0.25).toFixed(2));
        if (a === "fit") state.scale = 1;
        render();
      });
    });
    if (select) {
      select.addEventListener("change", function () {
        var opt = select.options[select.selectedIndex];
        state.doc = select.value; state.page = 1; state.pages = Number(opt.dataset.pages) || 1;
        render();
      });
    }
    HETA.showPdf = function (docId, page) {
      if (select) {
        for (var i = 0; i < select.options.length; i++) {
          if (select.options[i].value === String(docId)) { select.selectedIndex = i; state.pages = Number(select.options[i].dataset.pages) || 1; }
        }
      }
      state.doc = String(docId); state.page = page || 1; render();
      viewer.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
  }

  /* ---------- Quellenangaben ---------- */
  var METHODS = { profil_label: "Importprofil (Beschriftung)", profil_zelle: "Importprofil (feste Zelle)", pdf_text: "PDF-Textextraktion",
                  ki: "KI-Vorschlag", manuell: "Manuelle Korrektur" };
  var STATUS = { verifiziert: "Verifiziert", pruefbedarf: "Prüfbedarf", nicht_importierbar: "Nicht importierbar" };

  function initSources() {
    var el = document.getElementById("sources-data");
    var panel = document.getElementById("src-panel");
    if (!el || !panel) return;
    var sources = JSON.parse(el.textContent || "{}");
    var body = document.getElementById("src-body");
    panel.querySelector(".close").addEventListener("click", function () { panel.hidden = true; });
    function show(key) {
      var list = sources[key] || [];
      body.innerHTML = list.map(function (s) {
        var rows = [
          ["Wert", esc(s.value) + (s.unit ? " " + esc(s.unit) : "")],
          ["Datei", esc(s.file)],
          ["Pfad", '<span class="mono">' + esc(s.path) + "</span>"],
        ];
        if (s.sheet) rows.push(["Tabellenblatt", esc(s.sheet)]);
        if (s.cell) rows.push(["Zelle", '<span class="mono">' + esc(s.cell) + "</span>"]);
        if (s.label) rows.push(["Beschriftung", esc(s.label)]);
        if (s.formula) rows.push(["Formel", '<span class="mono">' + esc(s.formula) + "</span>"]);
        if (s.formula) rows.push(["Gespeichertes Ergebnis", s.cached === null ? "<em>fehlt</em>" : esc(s.cached)]);
        if (s.page) rows.push(["Seite", esc(s.page) + ' – <a href="#" data-pdf="' + s.document_id + '" data-page="' + s.page + '">in Vorschau zeigen</a>']);
        if (s.text) rows.push(["Textstelle", "„" + esc(s.text) + "“"]);
        rows.push(["Methode", esc(METHODS[s.method] || s.method)]);
        rows.push(["Sicherheit", s.confidence === null ? "–" : fmt(s.confidence * 100, 0) + " %"]);
        rows.push(["Status", '<span class="badge ' + (s.status === "verifiziert" ? "ok" : "warn") + '">' + esc(STATUS[s.status] || s.status) + "</span>"]);
        if (s.message) rows.push(["Hinweis", esc(s.message)]);
        return '<dl class="src-entry">' + rows.map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>"; }).join("") + "</dl>";
      }).join("") || '<p class="muted">Keine Quelle gespeichert.</p>';
      panel.hidden = false;
      body.querySelectorAll("[data-pdf]").forEach(function (a) {
        a.addEventListener("click", function (e) { e.preventDefault(); if (HETA.showPdf) HETA.showPdf(a.dataset.pdf, Number(a.dataset.page)); });
      });
    }
    document.querySelectorAll("[data-src]").forEach(function (v) {
      v.addEventListener("click", function () { show(v.dataset.src); });
      v.addEventListener("keydown", function (e) { if (e.key === "Enter") show(v.dataset.src); });
    });
  }

  /* ---------- Neue Anfrage: Materialauszug + Vorkalkulation ---------- */
  function num(v) {
    var t = String(v === undefined || v === null ? "" : v).trim();
    if (!t) return null;
    if (/^-?\d{1,3}(\.\d{3})+(,\d+)?$/.test(t)) t = t.replace(/\./g, "");  // 1.234,56
    t = t.replace(",", ".");
    var n = Number(t);
    return isNaN(n) ? null : n;
  }
  function initInquiry() {
    var form = document.getElementById("calc-form");
    if (!form) return;
    var useSum = document.getElementById("use-mat-sum");
    var matInput = form.querySelector("input[name=material_cost]");
    var tbody = document.querySelector("#calc-table tbody");
    var timer = null;
    function materials() {
      var rows = [], sum = 0;
      document.querySelectorAll("#mat-table tbody tr").forEach(function (tr) {
        var g = function (c) { return num(tr.querySelector(c).value); };
        var inc = tr.querySelector(".m-inc").checked, qty = g(".m-qty"), len = g(".m-len"), price = g(".m-price"), apz = g(".m-apz") || 0, mfg = g(".m-mfg") || 0;
        var cost = (price !== null && qty !== null) ? (len || 1) * qty * price + apz + mfg : null;
        tr.querySelector(".m-cost").textContent = cost === null ? "–" : fmt(cost, 2);
        tr.style.opacity = inc ? "1" : ".45";
        if (inc && cost !== null) sum += cost;
        rows.push({ i: Number(tr.dataset.i), qty: qty, length: len, price: price, apz: apz, mfg: mfg, include: inc });
      });
      var el = document.getElementById("mat-sum");
      if (el) el.textContent = fmt(sum, 2) + " €";
      return { rows: rows, sum: sum };
    }
    function collect() {
      var values = {};
      form.querySelectorAll("input").forEach(function (i) {
        var v = num(i.value);
        if (v !== null && i.dataset.rate) v = v / 100;
        values[i.name] = v;
      });
      return values;
    }
    function run() {
      var m = materials();
      if (useSum && useSum.checked && matInput) {
        matInput.value = m.sum ? m.sum.toFixed(2).replace(".", ",") : "";
        matInput.readOnly = true;
      } else if (matInput) { matInput.readOnly = false; }
      var values = collect();
      if (useSum && !useSum.checked) values._manual_material = 1;
      document.getElementById("calc_inputs_json").value = JSON.stringify(values);
      document.getElementById("material_json").value = JSON.stringify(m.rows);
      fetch("/api/anfragen/berechnen", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ inputs: values }) })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          tbody.innerHTML = res.lines.map(function (l) {
            return '<tr class="' + (l.subtotal ? "sum-row" : "") + '"><td>' + esc(l.label) + (l.detail ? '<div class="small muted">' + esc(l.detail) + "</div>" : "") +
              '</td><td class="num">' + fmt(l.amount, 2) + "</td></tr>";
          }).join("");
          document.getElementById("r-total").textContent = fmt(res.total_cost, 2) + " €";
          document.getElementById("r-price").textContent = res.complete ? fmt(res.price_fca, 2) + " €" : "unvollständig";
          document.getElementById("r-kg").textContent = res.price_per_kg === null ? "–" : fmt(res.price_per_kg, 2);
          var q = document.getElementById("r-qty");
          if (q) q.textContent = fmt(res.price_fca * Number(q.dataset.qty), 2) + " €";
          var miss = res.missing.length ? "Pflichtangaben fehlen: " + res.missing.join(", ") + ". " : "";
          var emp = res.empty.length ? "Nicht ausgefüllt (als 0 gerechnet): " + res.empty.join(", ") + "." : "";
          document.getElementById("r-missing").textContent = miss + emp;
        });
    }
    var schedule = function () { clearTimeout(timer); timer = setTimeout(run, 200); };
    form.addEventListener("input", schedule);
    var mt = document.getElementById("mat-table");
    if (mt) { mt.addEventListener("input", schedule); mt.addEventListener("change", schedule); }
    if (useSum) useSum.addEventListener("change", run);
    document.querySelectorAll("[data-set-price]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var src = btn.dataset.setPrice;
        document.querySelectorAll("#mat-table tbody tr").forEach(function (tr) {
          var v = tr.dataset[src] !== "" ? tr.dataset[src] : tr.dataset.then;
          if (v !== "" && v !== undefined) tr.querySelector(".m-price").value = Number(v).toFixed(2).replace(".", ",");
        });
        run();
      });
    });
    document.getElementById("save-form").addEventListener("submit", function () { run(); });
    run();
  }

  /* ---------- Ersatzteile / Handelsware ---------- */
  function initTrade() {
    var tbody = document.querySelector("#trade-table tbody");
    if (!tbody) return;
    var lines = JSON.parse(document.getElementById("trade-lines").textContent || "[]");
    var focused = null, timer = null;
    var F = ["pos", "description", "qty", "unit", "supplier", "ek", "lead_time"];
    function cell(name, value, cls, size) {
      if (name === "description") return '<td class="t-desc"><textarea data-f="description" rows="2">' + esc(value || "") + "</textarea></td>";
      return '<td class="' + (cls || "") + '"><input data-f="' + name + '" value="' + esc(value === null || value === undefined ? "" : value) + '" size="' + (size || 6) + '"' + (cls ? ' class="num-in"' : "") + "></td>";
    }
    function fmtIn(v) { return v === null || v === undefined || v === "" ? "" : String(v).replace(".", ","); }
    function render() {
      tbody.innerHTML = lines.map(function (l, i) {
        return '<tr data-i="' + i + '">' + cell("pos", l.pos, "", 3) + cell("description", l.description) + cell("qty", fmtIn(l.qty), "num", 4) +
          cell("unit", l.unit || "Stk", "", 3) + cell("supplier", l.supplier, "", 10) + cell("ek", fmtIn(l.ek), "num", 6) + cell("lead_time", l.lead_time, "", 7) +
          '<td class="num t-vku">–</td><td class="num t-vkt">–</td><td class="no-print"><button class="btn small danger" type="button" data-del="' + i + '">×</button></td></tr>';
      }).join("");
    }
    function read() {
      tbody.querySelectorAll("tr").forEach(function (tr) {
        var l = lines[Number(tr.dataset.i)];
        tr.querySelectorAll("[data-f]").forEach(function (el) {
          var f = el.dataset.f;
          l[f] = (f === "qty" || f === "ek") ? num(el.value) : el.value;
        });
      });
    }
    function rates() {
      var r = {};
      document.querySelectorAll("#rates input").forEach(function (el) {
        var v = num(el.value);
        r[el.name] = (v !== null && el.dataset.rate) ? v / 100 : v;
      });
      return r;
    }
    function run() {
      read();
      var payload = { lines: lines, rates: rates() };
      document.getElementById("trade_json").value = JSON.stringify(payload);
      fetch("/api/handelsware/berechnen", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          res.lines.forEach(function (l) {
            var tr = tbody.querySelector('tr[data-i="' + l.i + '"]');
            if (!tr) return;
            tr.querySelector(".t-vku").textContent = l.vk_unit === null ? "offen" : fmt(l.vk_unit, 2);
            tr.querySelector(".t-vkt").textContent = l.vk_total === null ? "–" : fmt(l.vk_total, 2);
          });
          var ex = res.extras.packaging_cost + res.extras.shipping_cost + res.extras.certificate_cost;
          document.getElementById("t-factor").textContent = fmt(res.factor, 4);
          document.getElementById("t-ek").textContent = fmt(res.sum_ek, 2) + " €";
          document.getElementById("t-vk").textContent = fmt(res.sum_vk, 2) + " €";
          document.getElementById("t-extras").textContent = fmt(ex, 2) + " €";
          document.getElementById("t-total").textContent = fmt(res.total, 2) + " €";
          document.getElementById("t-margin").textContent = res.margin === null ? "–" : fmt(res.margin * 100, 1) + " %";
          document.getElementById("t-open").textContent = res.open.length ? "Ohne EK (offen, nicht in der Summe): Pos. " + res.open.join(", ") : "";
        });
    }
    var schedule = function () { clearTimeout(timer); timer = setTimeout(run, 250); };
    tbody.addEventListener("input", schedule);
    tbody.addEventListener("focusin", function (e) { var tr = e.target.closest("tr"); if (tr) focused = Number(tr.dataset.i); });
    tbody.addEventListener("click", function (e) {
      var d = e.target.dataset.del;
      if (d === undefined) return;
      read(); lines.splice(Number(d), 1); render(); run();
    });
    document.getElementById("rates").addEventListener("input", schedule);
    document.getElementById("add-line").addEventListener("click", function () {
      read();
      lines.push({ pos: String(lines.length + 1), description: "", qty: null, unit: "Stk", supplier: "", ek: null, lead_time: "" });
      render(); run();
    });
    document.getElementById("trade-form").addEventListener("submit", function () { read(); document.getElementById("trade_json").value = JSON.stringify({ lines: lines, rates: rates() }); });
    // Suche nach früheren Preisen
    var results = document.getElementById("s-results");
    function search() {
      var q = document.getElementById("s-q").value, a = document.getElementById("s-dmin").value, b = document.getElementById("s-dmax").value;
      fetch("/api/handelsware/suche?q=" + encodeURIComponent(q) + "&d_min=" + encodeURIComponent(a) + "&d_max=" + encodeURIComponent(b))
        .then(function (r) { return r.json(); })
        .then(function (res) {
          var link = function (x) { return '<a href="/angebote/' + x.offer_id + '" target="_blank">' + esc(x.offer) + "</a>"; };
          var take = function (price, note) { return price === null ? "" : '<button class="btn small" type="button" data-take="' + price + '" data-note="' + esc(note || "") + '">übernehmen</button>'; };
          var h = "<h3>Handelsware (EK/VK)</h3>";
          h += res.trade.length ? '<table class="table compact"><tr><th>Angebot</th><th>Benennung</th><th class="num">Menge</th><th class="num">EK/Stk</th><th class="num">VK/Stk</th><th>Quelle</th><th></th></tr>' +
            res.trade.map(function (t) { return "<tr><td>" + link(t) + "</td><td>" + esc(t.description) + '</td><td class="num">' + fmt(t.qty, 0) + '</td><td class="num">' + fmt(t.ek, 2) + '</td><td class="num">' + fmt(t.vk, 2) + "</td><td class=small>" + esc(t.note || "") + (t.estimate ? ' <span class="badge warn">Schätzwert</span>' : "") + "</td><td>" + take(t.ek, t.note) + "</td></tr>"; }).join("") + "</table>"
            : '<p class="muted">Keine Treffer.</p>';
          h += "<h3>Bauteile aus Materialauszügen (Kalkulationsansatz)</h3>";
          h += res.material.length ? '<table class="table compact"><tr><th>Angebot</th><th>Benennung</th><th>Abmaße</th><th>Werkstoff</th><th class="num">Preis</th><th>Einh.</th><th></th></tr>' +
            res.material.map(function (m) { return "<tr><td>" + link(m) + "</td><td>" + esc(m.description) + "</td><td>" + esc(m.dimensions || "") + "</td><td>" + esc(m.material || "") + '</td><td class="num">' + fmt(m.price, 2) + "</td><td>" + esc(m.unit || "") + "</td><td>" + take(m.price, "Ansatz " + m.offer) + "</td></tr>"; }).join("") + "</table>"
            : '<p class="muted">Keine Treffer.</p>';
          results.innerHTML = h;
        });
    }
    document.getElementById("s-go").addEventListener("click", search);
    document.getElementById("s-q").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); search(); } });
    results.addEventListener("click", function (e) {
      var p = e.target.dataset.take;
      if (p === undefined) return;
      read();
      var i = focused !== null && lines[focused] ? focused : lines.length - 1;
      lines[i].ek = Number(p);
      if (!lines[i].supplier && e.target.dataset.note) lines[i].supplier = e.target.dataset.note;
      render(); run();
    });
    render(); run();
  }

  window.HETA = { pollJobs: pollJobs, initViewer: initViewer, initSources: initSources, initInquiry: initInquiry, initTrade: initTrade, fmt: fmt, esc: esc };
})();
