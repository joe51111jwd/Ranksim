// Ranksim client report. Read only. Builds every node with textContent; never parses HTML from the data.
(function () {
  "use strict";

  var root = document.getElementById("report");
  var path = location.pathname.replace(/\/+$/, "");
  var shared = /^\/r\/[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{22}$/.test(path);
  var preview = path.match(/^\/report\/([a-z0-9][a-z0-9-]{0,59})$/);
  var dataUrl = shared ? path + "/data" : preview ? "/api/v1/projects/" + preview[1] + "/client-report" : "";
  var csvBase = shared ? path : preview ? "/api/v1/projects/" + preview[1] + "/export" : "";
  var logoUrl = shared ? path + "/logo" : "/api/v1/branding/logo";

  function h(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "on") Object.keys(value).forEach(function (ev) { node.addEventListener(ev, value[ev]); });
      else node.setAttribute(key, value === true ? "" : value);
    });
    (kids || []).forEach(function (kid) {
      if (kid === null || kid === undefined || kid === false) return;
      node.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    });
    return node;
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var parts = String(iso).slice(0, 10).split("-");
    if (parts.length !== 3) return String(iso);
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var m = Number(parts[1]) - 1;
    return (months[m] || parts[1]) + " " + Number(parts[2]) + ", " + parts[0];
  }

  function message(text) {
    root.textContent = "";
    root.appendChild(h("section", { class: "tile message" }, [h("h1", { text: "Report unavailable" }), h("p", { text: text })]));
  }

  function tile(cls, kids) { return h("section", { class: "tile " + (cls || "") }, kids); }

  function head(title, sub) {
    return h("div", { class: "tile-head" }, [h("h2", { text: title }), sub ? h("p", { class: "sub", text: sub }) : null]);
  }

  function stat(label, value, note, dark) {
    return h("div", { class: "stat" + (dark ? " dark" : "") }, [
      h("span", { class: "stat-label", text: label }),
      h("span", { class: "stat-value", text: value === null || value === undefined ? "None" : String(value) }),
      note ? h("span", { class: "stat-note", text: note }) : null,
    ]);
  }

  function pathOf(url) {
    if (!url) return "";
    try { var u = new URL(url); return (u.pathname || "/") + (u.search || ""); } catch (e) { return url; }
  }

  function render(d) {
    root.textContent = "";
    var src = d.source || "Google";
    var googleNote = d.google_date ? src + " data " + d.google_date : d.search_data ? src + " data, date not recorded" : "No " + src + " data yet";
    document.title = "SEO report for " + (d.site || d.name || "your site");

    if (d.preview) {
      root.appendChild(h("div", { class: "preview no-print" }, [
        h("strong", { text: "Preview." }), " This is what your client sees. Use Print to save a PDF.",
      ]));
    }

    // header
    var brand = h("div", { class: "brand" }, [
      d.agency && d.agency.logo ? h("img", { class: "logo", src: logoUrl, alt: d.agency.name ? d.agency.name + " logo" : "Agency logo" }) : null,
      h("span", { class: "agency", text: (d.agency && d.agency.name) || "SEO report" }),
    ]);
    var actions = h("div", { class: "actions no-print" }, [
      h("button", { type: "button", class: "btn primary", on: { click: function () { window.print(); } } }, ["Print or save PDF"]),
      csvBase ? h("a", { class: "btn", href: csvBase + "/rankings.csv", download: "" }, ["Rankings CSV"]) : null,
      csvBase ? h("a", { class: "btn", href: csvBase + "/issues.csv", download: "" }, ["Issues CSV"]) : null,
    ]);
    root.appendChild(h("header", { class: "top" }, [brand, actions]));

    var dates = [h("span", { text: "Report date " + fmtDate(d.report_date) }), h("span", { text: googleNote })];
    if (d.data && d.data.label) dates.push(h("span", { text: d.data.label }));
    root.appendChild(tile("hero dark-tile", [
      h("p", { class: "eyebrow", text: "Search report" }),
      h("h1", { text: d.site || d.name || "Your site" }),
      h("p", { class: "dates" }, dates),
      d.data && d.data.demo ? h("p", { class: "demo", text: d.data.note }) : null,
    ]));

    // overview numbers
    var k = d.kpis || {};
    root.appendChild(h("section", { class: "stats", "aria-label": "Overview" }, [
      stat("In the top 10", (k.top10 || 0) + " of " + (k.checked || 0), googleNote, true),
      stat("In the top 3", k.top3 || 0, googleNote),
      stat("Average position", k.average === null || k.average === undefined ? "None" : k.average, "Searches in the top 10. " + googleNote),
      stat("Searches tracked", k.searches || 0, (k.checked || 0) + " checked"),
      stat("Issues found", k.issues || 0, (k.high || 0) + " high priority"),
    ]));

    // rankings
    var rows = (d.rankings || []).map(function (r) {
      var pos = r.position ? "#" + r.position : r.checked ? "Not in top 10" : "Not checked";
      var leader = (r.top || []).filter(function (t) { return t.position === 1; })[0];
      return h("tr", {}, [
        h("td", { class: "q" }, [
          h("span", { text: r.q }),
          r.url ? h("span", { class: "muted small", text: pathOf(r.url) }) : null,
          r.date && r.date !== d.google_date ? h("span", { class: "muted small", text: "Pulled " + r.date }) : null,
        ]),
        h("td", { class: "num" + (r.position && r.position <= 3 ? " good" : ""), text: pos }),
        d.predictions ? h("td", { class: "num" }, r.predicted ? [h("span", { text: "#" + r.predicted.position + " of " + r.predicted.of }), h("span", { class: "tag", text: "PREDICTED" })] : ["None"]) : null,
        h("td", { class: "muted", text: leader ? leader.domain : "" }),
      ]);
    });
    root.appendChild(tile("", [
      head("Where you rank", src + " positions. " + googleNote + "." + (d.predictions ? " PREDICTED is Ranksim's estimate, not a Google rank." : "")),
      rows.length ? h("div", { class: "table-wrap" }, [h("table", {}, [
        h("thead", {}, [h("tr", {}, [
          h("th", { text: "Search" }),
          h("th", { class: "num", text: src + " position" }),
          d.predictions ? h("th", { class: "num", text: "PREDICTED" }) : null,
          h("th", { text: src + " #1" }),
        ])]),
        h("tbody", {}, rows),
      ])]) : h("p", { class: "muted", text: "No searches yet." }),
    ]));

    // issues
    var issues = d.issues || [];
    root.appendChild(tile("", [
      head("Issues and what we are doing", issues.length ? issues.length + " found on the site." : "No issues found."),
      h("ul", { class: "issues" }, issues.map(function (i) {
        return h("li", { class: "issue" }, [
          h("div", { class: "issue-top" }, [
            h("span", { class: "sev " + i.severity, text: i.severity }),
            h("span", { class: "status " + i.status, text: i.status_label }),
          ]),
          h("h3", { text: i.title }),
          i.comment ? h("p", { text: i.comment }) : null,
          i.fix ? h("p", { class: "fix" }, [h("strong", { text: "Fix. " }), i.fix]) : null,
          i.plan ? h("p", { class: "plan" }, [h("strong", { text: "What we are doing. " }), i.plan]) : null,
          i.page || i.search ? h("p", { class: "muted small", text: [i.page ? pathOf(i.page) : "", i.search ? "Search: " + i.search : ""].filter(Boolean).join("  ") }) : null,
        ]);
      })),
    ]));

    // competitors
    var share = d.share || [];
    root.appendChild(tile("", [
      head("Competitors", "Who holds the top 10 across your searches. " + googleNote + "."),
      d.competitors && d.competitors.length ? h("p", { class: "chips" }, d.competitors.map(function (c) { return h("span", { class: "chip", text: c }); })) : null,
      share.length ? h("div", { class: "table-wrap" }, [h("table", {}, [
        h("thead", {}, [h("tr", {}, [h("th", { text: "Site" }), h("th", { class: "num", text: "Top 10 spots" }), h("th", { class: "num", text: "Best" }), h("th", { class: "num", text: "Average" })])]),
        h("tbody", {}, share.map(function (s) {
          return h("tr", { class: s.you ? "you" : "" }, [
            h("td", {}, [s.domain, s.you ? h("span", { class: "tag blue", text: "You" }) : null, s.competitor ? h("span", { class: "tag", text: "Competitor" }) : null]),
            h("td", { class: "num", text: String(s.slots) }),
            h("td", { class: "num", text: "#" + s.best }),
            h("td", { class: "num", text: String(s.average) }),
          ]);
        })),
      ])]) : h("p", { class: "muted", text: "No " + src + " data yet." }),
    ]));

    // change log
    var changes = d.changes || [];
    root.appendChild(tile("", [
      head("Recent work", changes.length ? "" : "Nothing logged yet."),
      h("ol", { class: "log" }, changes.map(function (c) {
        return h("li", {}, [h("span", { class: "muted small", text: fmtDate(c.date) }), h("span", { class: "kind", text: c.label }), h("span", { text: c.summary })]);
      })),
    ]));

    root.appendChild(h("footer", { class: "foot" }, (d.notes || []).map(function (n) { return h("p", { text: n }); }).concat([
      h("p", { text: [(d.agency && d.agency.name) || "", "Report date " + fmtDate(d.report_date), googleNote].filter(Boolean).join("  |  ") }),
    ])));
  }

  if (!dataUrl) { message("This address is not a report."); return; }
  fetch(dataUrl, { credentials: "same-origin", headers: { accept: "application/json" } })
    .then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) { return { ok: res.ok, status: res.status, body: body }; });
    })
    .then(function (r) {
      if (!r.ok) { message(r.status === 429 ? "Too many requests. Wait a minute and reload." : (r.body && r.body.error) || "This report is not available."); return; }
      render(r.body);
    })
    .catch(function () { message("The report could not load. Check your connection and reload."); });
})();
