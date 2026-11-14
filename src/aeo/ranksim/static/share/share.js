// Ranksim client report. Read only. Builds every node with textContent; never parses HTML from the data.
// Written for a client who does not do SEO: short plain words, and every number says what it is and how fresh it is.
(function () {
  "use strict";

  var root = document.getElementById("report");
  var path = location.pathname.replace(/\/+$/, "");
  var shared = /^\/r\/[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{22}$/.test(path);
  var preview = path.match(/^\/report\/([a-z0-9][a-z0-9-]{0,59})$/);
  var dataUrl = shared ? path + "/data" : preview ? "/api/v1/projects/" + preview[1] + "/client-report" : "";
  var csvBase = shared ? path : preview ? "/api/v1/projects/" + preview[1] + "/export" : "";
  var logoUrl = shared ? path + "/logo" : "/api/v1/branding/logo";
  var PRIORITY = { high: "High priority", medium: "Medium priority", low: "Low priority" };

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
      h("span", { class: "stat-value", text: value === null || value === undefined ? "None yet" : String(value) }),
      note ? h("span", { class: "stat-note", text: note }) : null,
    ]);
  }

  function pathOf(url) {
    if (!url) return "";
    try { var u = new URL(url); return (u.pathname || "/") + (u.search || ""); } catch (e) { return url; }
  }

  function place(position) { return position ? "#" + position : "Not on page one"; }

  function moveChip(move) {
    var before = move.from || 11;
    var after = move.to || 11;
    var up = after < before;
    var steps = Math.abs(before - after);
    var text = move.from && move.to ? (up ? "Up " : "Down ") + steps : up ? "New on page one" : "Off page one";
    return h("span", { class: "move " + (up ? "up" : "down"), text: text });
  }

  function weekly(d, src) {
    var w = d.weekly;
    if (!w) return null;
    var moves = w.moves || [];
    var done = w.done || [];
    var next = w.next || [];
    var checks = w.checks ? "Google was checked " + (w.checks === 1 ? "once" : w.checks + " times") + " this week." : "";
    return tile("week", [
      head("This week", "Since " + fmtDate(w.since) + "."),
      h("div", { class: "week-grid" }, [
        h("div", { class: "week-col" }, [
          h("h3", { text: "Position changes" }),
          moves.length ? h("ul", { class: "week-list" }, moves.map(function (m) {
            return h("li", {}, [
              h("span", { class: "week-q", text: m.q }),
              h("span", { class: "week-meta", text: place(m.from) + " on " + fmtDate(m.from_date) + ", now " + place(m.to) + " (" + src + ", " + fmtDate(m.to_date) + ")" }),
              moveChip(m),
            ]);
          })) : h("p", { class: "muted", text: w.moves_note }),
          w.counts && w.counts.compared ? h("p", { class: "muted small", text: "Of " + w.counts.compared + " searches checked a week apart: " + w.counts.gained + " new on page one, " + w.counts.lost + " dropped off, " + w.counts.up + " moved up, " + w.counts.down + " moved down." }) : null,
          (w.passed || []).length ? h("h3", { class: "week-sub", text: "Competitors who moved ahead" }) : null,
          (w.passed || []).length ? h("ul", { class: "week-list" }, w.passed.map(function (p) {
            return h("li", {}, [
              h("span", { class: "week-q", text: p.domain }),
              h("span", { class: "week-meta", text: "Moved ahead of you for \"" + p.q + "\". They are #" + p.their_to + ", you are " + place(p.our_to) + "." }),
            ]);
          })) : null,
        ]),
        h("div", { class: "week-col" }, [
          h("h3", { text: "Done" }),
          done.length ? h("ul", { class: "week-list" }, done.map(function (c) {
            return h("li", {}, [h("span", { class: "week-q", text: c.summary }), h("span", { class: "week-meta", text: c.label + ", " + fmtDate(c.date) })]);
          })) : h("p", { class: "muted", text: "Nothing new to report this week." }),
          checks ? h("p", { class: "muted small", text: checks }) : null,
        ]),
        h("div", { class: "week-col" }, [
          h("h3", { text: "Next" }),
          next.length ? h("ul", { class: "week-list" }, next.map(function (i) {
            return h("li", {}, [
              h("span", { class: "week-q", text: i.title }),
              h("span", { class: "week-meta", text: i.plan ? i.status_label + ". " + i.plan : i.status_label + ". " + PRIORITY[i.severity] + "." }),
            ]);
          })) : h("p", { class: "muted", text: "No open fixes right now." }),
        ]),
      ]),
    ]);
  }

  // Owner preview only (/report/<slug>, behind the login). A share link never shows or fetches this.
  function weeklyCopyButton(slug) {
    var button = h("button", { type: "button", class: "btn" }, ["Copy weekly update"]);
    button.addEventListener("click", function () {
      button.disabled = true;
      fetch("/api/v1/projects/" + slug + "/weekly-update", { credentials: "same-origin", headers: { accept: "application/json" } })
        .then(function (res) { if (!res.ok) throw new Error("not ok"); return res.json(); })
        .then(function (data) {
          var full = "Subject: " + data.subject + "\n\n" + data.text;
          if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
            return navigator.clipboard.write([new ClipboardItem({
              "text/html": new Blob([data.html], { type: "text/html" }),
              "text/plain": new Blob([data.text], { type: "text/plain" }),
            })]).catch(function () { window.prompt("Copy the weekly update", full); });
          }
          return navigator.clipboard.writeText(full).catch(function () { window.prompt("Copy the weekly update", full); });
        })
        .then(function () { button.textContent = "Copied"; })
        .catch(function () { button.textContent = "Could not copy"; })
        .then(function () { button.disabled = false; setTimeout(function () { button.textContent = "Copy weekly update"; }, 2500); });
    });
    return button;
  }

  function render(d) {
    root.textContent = "";
    var src = d.source || "Google";
    var demo = !!(d.data && d.data.demo);
    // A demo never says Google: its sites and positions are invented.
    var where = demo ? "the demo results" : "Google";
    var posLabel = demo ? "Demo position" : "Your position";
    var checked = demo ? "Invented demo data" : d.google_date ? "Google checked " + d.google_date : d.search_data ? "Google check date not recorded" : "Not checked on Google yet";
    document.title = "Search report for " + (d.site || d.name || "your site");

    if (d.preview) {
      root.appendChild(h("div", { class: "preview no-print" }, [
        h("strong", { text: "Preview." }), " This is what your client sees. Use Print to save a PDF.",
      ]));
    }

    var brand = h("div", { class: "brand" }, [
      d.agency && d.agency.logo ? h("img", { class: "logo", src: logoUrl, alt: d.agency.name ? d.agency.name + " logo" : "Agency logo" }) : null,
      h("span", { class: "agency", text: (d.agency && d.agency.name) || "Search report" }),
    ]);
    var actions = h("div", { class: "actions no-print" }, [
      d.preview && preview ? weeklyCopyButton(preview[1]) : null,
      h("button", { type: "button", class: "btn primary", on: { click: function () { window.print(); } } }, ["Print or save PDF"]),
      csvBase ? h("a", { class: "btn", href: csvBase + "/rankings.csv", download: "" }, ["Positions (CSV)"]) : null,
      csvBase ? h("a", { class: "btn", href: csvBase + "/issues.csv", download: "" }, ["Fixes (CSV)"]) : null,
    ]);
    root.appendChild(h("header", { class: "top" }, [brand, actions]));

    var dates = [h("span", { text: "Report made " + fmtDate(d.report_date) }), h("span", { text: checked })];
    root.appendChild(tile("hero dark-tile", [
      h("p", { class: "eyebrow", text: "Search report" }),
      h("h1", { text: d.site || d.name || "Your site" }),
      h("p", { class: "lead", text: "Where your site shows up in " + where + ", what we found, and what we are doing about it." }),
      h("p", { class: "dates" }, dates),
      d.data && d.data.demo ? h("p", { class: "demo", text: d.data.note }) : null,
    ]));

    root.appendChild(weekly(d, src));

    var k = d.kpis || {};
    root.appendChild(h("section", { class: "stats", "aria-label": "Summary" }, [
      stat(demo ? "On page one (demo)" : "On page one of Google", (k.top10 || 0) + " of " + (k.checked || 0), "Searches where your site is in the first 10 results.", true),
      stat("In the top 3", k.top3 || 0, "Searches where you are one of the first three results."),
      stat("Average position", k.average === null || k.average === undefined ? null : k.average, "Across searches where you are on page one. Lower is better."),
      stat("Searches we track", k.searches || 0, "What people type when you want them to find you."),
      stat("Things to fix", k.issues || 0, (k.high || 0) + " high priority"),
    ]));
    root.appendChild(h("p", { class: "stats-note muted small", text: (demo ? "Invented demo positions, not Google results." : "Positions from Google. " + checked + ".") }));

    var rows = (d.rankings || []).map(function (r) {
      var pos = r.position ? "#" + r.position : r.checked ? "Not on page one" : "Not checked yet";
      var leader = (r.top || []).filter(function (t) { return t.position === 1; })[0];
      return h("tr", {}, [
        h("td", { class: "q", "data-label": "Search" }, [
          h("span", { text: r.q }),
          r.url ? h("span", { class: "muted small", text: "Your page: " + pathOf(r.url) }) : null,
          r.date && r.date !== d.google_date ? h("span", { class: "muted small", text: "Checked " + r.date }) : null,
        ]),
        h("td", { class: "num" + (r.position && r.position <= 3 ? " good" : ""), "data-label": posLabel, text: pos }),
        d.predictions ? h("td", { class: "num", "data-label": "Estimate (PREDICTED)" }, r.predicted ? [
          h("span", { text: "#" + r.predicted.position + " of " + r.predicted.of }),
          h("span", { class: "tag", text: "PREDICTED" }),
          h("span", { class: "muted small est-basis", text: "based on " + r.predicted.pairs + " page pairs" }),
        ] : ["Not shown"]) : null,
        h("td", { class: "muted", "data-label": "First result", text: leader ? leader.domain : "" }),
      ]);
    });
    root.appendChild(tile("", [
      head("Where you show up", (demo ? "Demo position for each search. " : "Your position in Google for each search. ") + "#1 is the first result. " + checked + "." +
        (d.predictions ? " The estimate is Ranksim's model of where your page should land among the pages we stored. It is PREDICTED, not a Google position, and it is shown only for searches where a backtest was clearer, with the number of page pairs it is based on." : "")),
      rows.length ? h("div", { class: "table-wrap" }, [h("table", { class: "stack" }, [
        h("thead", {}, [h("tr", {}, [
          h("th", { text: "Search" }),
          h("th", { class: "num", text: posLabel }),
          d.predictions ? h("th", { class: "num", text: "Estimate (PREDICTED)" }) : null,
          h("th", { text: "First result" }),
        ])]),
        h("tbody", {}, rows),
      ])]) : h("p", { class: "muted", text: "No searches yet." }),
    ]));

    var sc = d.search_console; // gsc session, flagged edit: Search Console data, never mixed with Google positions
    if (sc) {
      var t = sc.totals || {};
      root.appendChild(tile("", [
        head("Google Search Console", sc.label + ", " + sc.range + ". " + sc.note),
        h("div", { class: "stats" }, [
          stat("Clicks", (t.clicks || 0).toLocaleString("en-US"), "From Google Search"),
          stat("Impressions", (t.impressions || 0).toLocaleString("en-US"), "Times the site was shown"),
        ]),
        (sc.positions || []).length ? h("div", { class: "table-wrap" }, [h("table", { class: "stack" }, [
          h("thead", {}, [h("tr", {}, [h("th", { text: "Search" }), h("th", { class: "num", text: "Search Console average position" }), h("th", { class: "num", text: "Impressions" })])]),
          h("tbody", {}, sc.positions.map(function (p) {
            return h("tr", {}, [
              h("td", { class: "q", "data-label": "Search" }, [h("span", { text: p.q }), p.page ? h("span", { class: "muted small", text: "Page: " + p.page }) : null]),
              h("td", { class: "num", "data-label": "Search Console average position", text: "avg #" + p.position }),
              h("td", { class: "num", "data-label": "Impressions", text: (p.impressions || 0).toLocaleString("en-US") }),
            ]);
          })),
        ])]) : null,
      ]));
    }

    var issues = d.issues || [];
    // Clients read the important items in full. Low priority items nobody has planned yet are listed briefly at the end.
    var main = issues.filter(function (i) { return i.severity !== "low" || i.status !== "open" || i.plan; });
    var minor = issues.filter(function (i) { return main.indexOf(i) < 0; });
    root.appendChild(tile("", [
      head("What we found and what we are doing", issues.length
        ? issues.length + " things to fix on the site, most important first." + (minor.length ? " " + minor.length + " smaller fixes are listed at the end." : "")
        : "Nothing to fix right now."),
      h("ul", { class: "issues" }, main.map(function (i) {
        var plain = i.fix && !/editor|test it|ranksim/i.test(i.fix) ? i.fix : "";
        return h("li", { class: "issue" }, [
          h("div", { class: "issue-top" }, [
            h("span", { class: "sev " + i.severity, text: PRIORITY[i.severity] || i.severity }),
            h("span", { class: "status " + i.status, text: i.status_label }),
          ]),
          h("h3", { text: i.title }),
          i.comment ? h("p", { text: i.comment }) : null,
          i.plan ? h("p", { class: "plan" }, [h("strong", { text: "What we are doing: " }), i.plan])
            : plain ? h("p", { class: "fix" }, [h("strong", { text: "Suggested fix: " }), plain]) : null,
          i.page || i.search ? h("p", { class: "muted small", text: [i.page ? "Page: " + pathOf(i.page) : "", i.search ? "Search: " + i.search : ""].filter(Boolean).join("   ") }) : null,
        ]);
      })),
      minor.length ? h("div", { class: "minor" }, [
        h("h3", { text: "Smaller fixes (" + minor.length + ")" }),
        h("p", { class: "muted small", text: "Low priority technical items. We will handle these after the ones above." }),
        h("ul", { class: "minor-list" }, minor.map(function (i) {
          return h("li", {}, [h("span", { text: i.title }), i.page ? h("span", { class: "muted small", text: " " + pathOf(i.page) }) : null]);
        })),
      ]) : null,
    ]));

    var share = d.share || [];
    root.appendChild(tile("", [
      head("Who else shows up", "Sites on page one of " + where + " for your searches. " + checked + "."),
      d.competitors && d.competitors.length ? h("p", { class: "chips" }, [h("span", { class: "muted small", text: "Main competitors:" })].concat(d.competitors.map(function (c) { return h("span", { class: "chip", text: c }); }))) : null,
      share.length ? h("div", { class: "table-wrap" }, [h("table", { class: "stack" }, [
        h("thead", {}, [h("tr", {}, [h("th", { text: "Site" }), h("th", { class: "num", text: "Page one spots" }), h("th", { class: "num", text: "Best position" }), h("th", { class: "num", text: "Average position" })])]),
        h("tbody", {}, share.map(function (s) {
          return h("tr", { class: s.you ? "you" : "" }, [
            h("td", { "data-label": "Site" }, [s.domain, s.you ? h("span", { class: "tag blue", text: "You" }) : null, s.competitor ? h("span", { class: "tag", text: "Competitor" }) : null]),
            h("td", { class: "num", "data-label": "Page one spots", text: String(s.slots) }),
            h("td", { class: "num", "data-label": "Best position", text: "#" + s.best }),
            h("td", { class: "num", "data-label": "Average position", text: String(s.average) }),
          ]);
        })),
      ])]) : h("p", { class: "muted", text: demo ? "No demo results yet." : "Not checked on Google yet." }),
    ]));

    var changes = d.changes || [];
    root.appendChild(tile("", [
      head("Recent work", changes.length ? "The latest first." : "Nothing logged yet."),
      h("ol", { class: "log" }, changes.map(function (c) {
        return h("li", {}, [h("span", { class: "muted small", text: fmtDate(c.date) }), h("span", { class: "kind", text: c.label }), h("span", { text: c.summary })]);
      })),
    ]));

    root.appendChild(h("footer", { class: "foot" }, (d.notes || []).map(function (n) { return h("p", { text: n }); }).concat([
      h("p", { text: [(d.agency && d.agency.name) || "", "Report made " + fmtDate(d.report_date), checked].filter(Boolean).join("   |   ") }),
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
