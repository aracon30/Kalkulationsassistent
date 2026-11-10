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

  /* ---------- Kalkulationssimulation ---------- */
  function initSimulation() {
    var form = document.getElementById("sim-form");
    if (!form) return;
    var tbody = document.querySelector("#sim-table tbody");
    var timer = null;
    function collect() {
      var values = {};
      form.querySelectorAll("input").forEach(function (i) {
        var raw = i.value.trim().replace(/\./g, "").replace(",", ".");
        if (i.value.trim().match(/^\d+\.\d{1,3}$/)) raw = i.value.trim();  // Dezimalpunkt erlauben
        var v = raw === "" ? null : Number(raw);
        if (v !== null && isNaN(v)) v = null;
        if (v !== null && i.dataset.rate) v = v / 100;
        values[i.name] = v;
      });
      return values;
    }
    function run() {
      var values = collect();
      document.getElementById("inputs_json").value = JSON.stringify(values);
      fetch("/api/simulation/berechnen", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: values }) })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          tbody.innerHTML = res.lines.map(function (l) {
            return '<tr class="' + (l.subtotal ? "sum-row" : "") + '"><td>' + esc(l.label) + '<div class="small muted">' + esc(l.formula) + "</div></td>" +
              '<td class="num">' + (l.kind === "surcharge" ? fmt(l.base, 2) : "") + "</td>" +
              '<td class="num">' + (l.kind === "surcharge" && l.rate !== null ? fmt(l.rate * 100, 2) + " %" : "") + "</td>" +
              '<td class="num">' + fmt(l.amount, 2) + "</td></tr>";
          }).join("");
          document.getElementById("r-total").textContent = fmt(res.total_cost, 2) + " €";
          document.getElementById("r-profit").textContent = fmt(res.profit, 2) + " €";
          document.getElementById("r-price").textContent = fmt(res.sales_price, 2) + " €";
          document.getElementById("r-margin").textContent = res.margin === null ? "–" : fmt(res.margin * 100, 1) + " %";
          document.getElementById("r-kg").textContent = res.price_per_kg === null ? "–" : fmt(res.price_per_kg, 2) + " €/kg";
          document.getElementById("r-missing").textContent = res.missing_inputs.length ? "Fehlende Eingaben (werden nicht als 0 angenommen): " + res.missing_inputs.join(", ") : "";
        });
    }
    form.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(run, 200); });
    run();
  }

  window.HETA = { pollJobs: pollJobs, initViewer: initViewer, initSources: initSources, initSimulation: initSimulation };
})();
