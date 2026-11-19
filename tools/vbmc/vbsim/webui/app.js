/* vBMC-Weboberfläche: eine Single-Page-App, vier Seitenrahmen (Skins) nach dem Bedienkonzept der Hersteller.
   Liest live aus Redfish (Cookie-Session). Menüs/Begriffe kommen aus window.VB.
   Alle Symbole sind eigene, einfache SVG-Formen – keine Hersteller-Grafiken. */
(function () {
  "use strict";
  var C = window.VB, root = document.getElementById("app"), S = C.shell;
  var timer = null;

  /* ---------- Hilfen ---------- */
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); }
  function api(path, opt) {
    opt = opt || {}; opt.credentials = "same-origin";
    opt.headers = Object.assign({"Accept": "application/json"}, opt.headers || {});
    return fetch(path, opt).then(function (r) {
      if (r.status === 401) { location.replace("/?expired=1"); throw new Error("401"); }
      return r.text().then(function (t) { var j = {}; try { j = t ? JSON.parse(t) : {}; } catch (e) {}
        if (!r.ok) { var m = (j.error && j.error.message) || ("HTTP " + r.status); throw new Error(m); }
        return j; });
    });
  }
  function post(path, body) { return api(path, {method: "POST", body: JSON.stringify(body || {}),
    headers: {"Content-Type": "application/json"}}); }
  function patch(path, body) { return api(path, {method: "PATCH", body: JSON.stringify(body || {}),
    headers: {"Content-Type": "application/json"}}); }
  function members(col) { return api(col).then(function (d) {
    return Promise.all((d.Members || []).map(function (m) { return api(m["@odata.id"]); })); }); }
  function soft(p) { return p.catch(function (e) { if (e.message === "401") throw e; return {}; }); }
  function fmtTime(iso) { var d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString(C.locale || "de-DE"); }
  function sevCls(s) { return s === "Critical" ? "bad" : (s === "Warning" ? "warn" : "ok"); }
  function worst(list) { var w = "OK"; (list || []).forEach(function (x) { var h = ((x || {}).Status || {}).Health || "OK";
    if (h === "Critical" || (h === "Warning" && w === "OK")) w = h; }); return w; }
  function badge(txt, cls) { return '<span class="badge ' + (cls || "ok") + '">' + esc(txt) + "</span>"; }
  function kv(rows) { return '<table class="kv">' + rows.map(function (r) {
    return "<tr><th>" + esc(r[0]) + "</th><td>" + (r[2] ? r[1] : esc(r[1])) + "</td></tr>"; }).join("") + "</table>"; }
  function table(head, rows, raw) { return '<table class="grid"><thead><tr>' + head.map(function (h) {
    return "<th>" + esc(h) + "</th>"; }).join("") + "</tr></thead><tbody>" + (rows.length ? rows.map(function (r) {
    return "<tr>" + r.map(function (c) { return "<td>" + (raw ? c : esc(c)) + "</td>"; }).join("") + "</tr>";
  }).join("") : '<tr><td colspan="' + head.length + '" class="muted">–</td></tr>') + "</tbody></table>"; }
  function card(title, body, extra) { return '<section class="card"><h3><span>' + esc(title) + "</span>" + (extra || "") +
    "</h3><div>" + body + "</div></section>"; }
  function hword(h) { return h === "Critical" ? C.t.critical : (h === "Warning" ? C.t.degraded : C.t.healthy); }
  function powerBadge(ps, post) {
    var map = {On: ["ok", C.t.on], Off: ["off", C.t.off], PoweringOn: ["warn", C.t.poweringOn], PoweringOff: ["warn", C.t.poweringOff]};
    var m = map[ps] || ["off", ps];
    var extra = (ps === "On" && post && post !== "FinishedPost") ? " – POST" : "";
    return badge(m[1] + extra, m[0]);
  }
  function postOf(s) { return ((s.Oem || {}).Hpe || {}).PostState; }

  /* ---------- eigene Symbole ---------- */
  var IP = {
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    server: '<rect x="4" y="4" width="16" height="7" rx="1"/><rect x="4" y="13" width="16" height="7" rx="1"/><path d="M8 7.5h.01M8 16.5h.01"/>',
    chip: '<rect x="7" y="7" width="10" height="10"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
    screen: '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M9 20h6M12 16v4"/>',
    power: '<path d="M12 3v8"/><path d="M6.5 7a7.5 7.5 0 1 0 11 0"/>',
    net: '<circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v5M5 17v-3h14v3"/>',
    gear: '<circle cx="12" cy="12" r="3.5"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="1"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    sliders: '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/>',
    home: '<path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z"/>',
    bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20h4"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    gauge: '<path d="M4 17a8 8 0 1 1 16 0"/><path d="M12 17l4-5"/>',
    disk: '<ellipse cx="12" cy="6" rx="7" ry="2.5"/><path d="M5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6"/>',
    heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
    bulb: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    out: '<path d="M14 4h5v16h-5M10 8l-4 4 4 4M6 12h10"/>'
  };
  function ico(n) { return '<svg class="ico" viewBox="0 0 24 24">' + (IP[n] || IP.info) + "</svg>"; }
  function st(h) {                              /* Zustandssymbol: Kreis mit Haken / Ausrufezeichen / Kreuz */
    var col = h === "Critical" ? "#c4262e" : (h === "Warning" ? "#d48806" : "#2b8a3e");
    var mark = h === "Critical" ? '<path d="M8.5 8.5l7 7M15.5 8.5l-7 7"/>' : (h === "Warning" ? '<path d="M12 7v6M12 16.5v.5"/>'
      : '<path d="M7.5 12.5l3 3 6-6.5"/>');
    return '<svg class="st" viewBox="0 0 24 24" width="16" height="16" style="vertical-align:-3px"><circle cx="12" cy="12" r="10.5" fill="' + col +
      '"/><g fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">' + mark + "</g></svg>";
  }

  /* ---------- Menü ---------- */
  var ITEMS = [];
  C.menu.forEach(function (g) { g.items.forEach(function (i) { ITEMS.push({k: i.k, label: i.label, g: g}); }); });
  function item(k) { return ITEMS.filter(function (i) { return i.k === k; })[0] || ITEMS[0]; }
  function href(k) { return "#/" + k; }

  /* ---------- Rahmen je Hersteller ---------- */
  function shell() {
    var simTag = '<span class="sim" title="' + esc(C.t.simNote) + '">' + esc(C.t.simShort) + "</span>";
    var foot = '<footer class="foot">' + esc(C.t.simNote) + " · " + esc(C.profile) + "-Profil</footer><div id=\"modal\"></div>";
    var logout = '<button class="btn flat" id="logout">' + esc(C.t.logout) + "</button>";
    var h;
    if (S === "dell") {
      h = '<header class="top"><div class="brand"><span class="mk">vB</span><span class="p">' + esc(C.product) +
        '</span><span class="ed" id="hdr-lic"></span></div><div class="topr">' + simTag +
        '<span>' + ico("search") + "</span><span>" + ico("help") + "</span><span>" + ico("user") + " " + esc(C.user) +
        '</span><span id="hdr-power"></span><button id="logout" title="' + esc(C.t.logout) + '">' + ico("out") + "</button></div></header>" +
        '<nav class="menu">' + C.menu.map(function (g, gi) {
          if (g.items.length === 1) return '<a data-g="' + gi + '" href="' + href(g.items[0].k) + '">' + esc(g.label) + "</a>";
          return '<div class="dd" data-g="' + gi + '"><a href="' + href(g.items[0].k) + '">' + esc(g.label) + ' &#9662;</a><div class="ddm">' +
            g.items.map(function (i) { return '<a href="' + href(i.k) + '">' + esc(i.label) + "</a>"; }).join("") + "</div></div>";
        }).join("") + "</nav>" +
        '<div class="phead"><h1 id="ptitle"></h1><div class="tabs" id="tabs"></div></div><main id="main"></main>' + foot;
    } else if (S === "hpe") {
      h = '<div class="lay"><aside class="nav"><div class="brandblk"><div class="mk"></div><div class="p">' + esc(C.product) +
        '</div><div class="m">' + esc(C.hostLabel) + '</div><div class="m">' + esc(C.model) + "</div>" + simTag + "</div>" +
        C.menu.map(function (g, gi) { return '<a class="navg" data-g="' + gi + '" href="' + href(g.items[0].k) + '">' + ico(g.icon) +
          esc(g.label) + "</a>" + (C.navSub ? g.items.map(function (i) {
            return '<a class="navsub" data-k="' + i.k + '" data-sub="' + gi + '" href="' + href(i.k) + '">' + esc(i.label) + "</a>"; }).join("") : ""); }).join("") +
        '<div class="navthumb"><div class="thumb" id="navthumb"><pre></pre></div><div class="lnk"><a href="#" id="nav-h5">HTML5</a>' +
        '<a href="#/console">' + esc(C.t.remoteConsole) + "</a></div></div></aside>" +
        '<div class="main"><header class="top"><h1 id="ptitle"></h1><div class="icons">' +
        '<span id="hdr-power" title="' + esc(C.t.serverPower) + '"></span><span id="hdr-health" title="' + esc(C.t.sysHealthShort) + '"></span>' +
        '<span id="hdr-uid" title="UID"></span><span>' + ico("user") + " " + esc(C.user) + "</span><span>" + ico("help") + "</span>" +
        logout + '</div></header><div class="tabs" id="tabs"></div><main id="main"></main></div></div>' + foot;
    } else if (S === "lenovo") {
      h = '<header class="top"><div class="brand"><span class="mk">vB</span><span class="p">' + esc(C.product) +
        '</span><span class="sys">' + esc(C.hostLabel) + " · " + esc(C.model) + '</span></div><div class="topr">' + simTag +
        '<span id="hdr-health"></span><span id="hdr-power"></span><span>' + ico("user") + " " + esc(C.user) + "</span><span>" +
        ico("help") + "</span>" + logout + "</div></header>" +
        '<div class="lay"><aside class="nav">' + C.menu.map(function (g, gi) {
          var sub = g.items.length > 1 ? g.items.map(function (i) {
            return '<a class="navsub" data-k="' + i.k + '" data-sub="' + gi + '" href="' + href(i.k) + '">' + esc(i.label) + "</a>"; }).join("") : "";
          return '<a class="navg" data-g="' + gi + '" href="' + href(g.items[0].k) + '">' + ico(g.icon) + esc(g.label) +
            (sub ? ' <span style="margin-left:auto">&#9662;</span>' : "") + "</a>" + sub;
        }).join("") + '</aside><div class="main"><div class="phead"><h1 id="ptitle"></h1></div><main id="main"></main></div></div>' + foot;
    } else {
      h = '<header class="banner"><div class="brand"><span class="mk">vB</span><span class="p">' + esc(C.product) + "</span>" + simTag +
        '</div><div class="links">' + ico("user") + " " + esc(C.user) + ' &nbsp;|&nbsp; <span id="hdr-power"></span> &nbsp;|&nbsp; ' +
        '<a href="#" id="refresh">' + esc(C.t.refresh) + "</a> | " + logout + "</div></header>" +
        '<nav class="menu">' + C.menu.map(function (g, gi) { return '<a data-g="' + gi + '" href="' + href(g.items[0].k) + '">' +
          esc(g.label) + "</a>"; }).join("") + "</nav>" +
        '<div class="lay"><aside class="side" id="side"></aside><div class="main"><h1 id="ptitle"></h1><p class="desc" id="pdesc"></p>' +
        '<main id="main"></main></div></div>' + foot;
    }
    root.innerHTML = h;
    document.getElementById("logout").onclick = function () {
      fetch("/redfish/v1/SessionService/Sessions/x", {method: "DELETE", credentials: "same-origin"})
        .then(function () { location.replace("/"); }); };
    var rf = document.getElementById("refresh");
    if (rf) rf.onclick = function (e) { e.preventDefault(); render(); };
    var h5 = document.getElementById("nav-h5");
    if (h5) h5.onclick = function (e) { e.preventDefault(); launchConsole(); };
    var nt = document.getElementById("navthumb");
    if (nt) nt.onclick = launchConsole;
    document.addEventListener("click", function (e) {
      var t = e.target.closest("[data-dd]");
      Array.prototype.forEach.call(document.querySelectorAll(".dd.open"), function (d) {
        if (!t || d !== t.parentNode) d.classList.remove("open"); });
      if (t) { e.preventDefault(); t.parentNode.classList.toggle("open"); }
    });
  }

  function chrome(k) {                              /* Markierungen, Reiter, Titel passend zur Seite */
    var it = item(k), gi = C.menu.indexOf(it.g);
    Array.prototype.forEach.call(document.querySelectorAll("[data-g]"), function (a) {
      a.classList.toggle("on", +a.getAttribute("data-g") === gi); });
    Array.prototype.forEach.call(document.querySelectorAll("[data-sub]"), function (a) {
      a.style.display = +a.getAttribute("data-sub") === gi ? "" : "none";
      a.classList.toggle("on", a.getAttribute("data-k") === k); });
    var tabs = document.getElementById("tabs");
    if (tabs) {
      tabs.innerHTML = it.g.items.length > 1 || S === "hpe" ? it.g.items.map(function (i) {
        return '<a class="' + (i.k === k ? "on" : "") + '" href="' + href(i.k) + '">' + esc(i.label) + "</a>"; }).join("") : "";
      tabs.style.display = tabs.innerHTML ? "" : "none";
    }
    var side = document.getElementById("side");
    if (side) side.innerHTML = it.g.items.map(function (i) {
      return '<a class="' + (i.k === k ? "on" : "") + '" href="' + href(i.k) + '">' + esc(i.label) + "</a>"; }).join("");
    var pt = document.getElementById("ptitle");
    if (S === "hpe" && C.dash === "dell") pt.innerHTML = esc(it.g.label) + " &rsaquo; <b>" + esc(it.label) + "</b>";
    else if (S === "hpe") pt.innerHTML = esc(it.g.label) + " - <b>" + esc(it.label) + "</b>";
    else if (S === "dell") pt.textContent = it.g.label;
    else pt.textContent = it.label;
    var pd = document.getElementById("pdesc");
    if (pd) pd.textContent = C.desc[k] || "";
  }

  function setHTML(id, html) { var e = document.getElementById(id); if (e) e.innerHTML = html; }
  function header(s) {                              /* Statusanzeigen in der Kopfleiste */
    var h = (s.Status || {}).Health || "OK";
    if (S === "hpe") {
      setHTML("hdr-power", '<span style="color:' + (s.PowerState === "On" ? "#2b8a3e" : "#7b828c") + '">' + ico("power") + "</span>");
      setHTML("hdr-health", st(h));
      setHTML("hdr-uid", '<span style="color:' + (s.IndicatorLED === "Off" ? "#9aa0a6" : "#2a6fdb") + '">' + ico("bulb") + "</span>");
    } else if (S === "lenovo") {
      setHTML("hdr-health", st(h));
      setHTML("hdr-power", ico("power") + " " + esc(s.PowerState === "On" ? C.t.on : (s.PowerState === "Off" ? C.t.off : s.PowerState)));
    } else {
      setHTML("hdr-power", powerBadge(s.PowerState, postOf(s)));
    }
  }

  function confirmBox(msg, ok) {
    var m = document.getElementById("modal");
    m.innerHTML = '<div class="shade"><div class="dlg"><p>' + esc(msg) + '</p><div class="dlgb">' +
      '<button class="btn" id="m-no">' + esc(C.t.cancel) + '</button><button class="btn pri" id="m-yes">' +
      esc(C.t.ok) + "</button></div></div></div>";
    document.getElementById("m-no").onclick = function () { m.innerHTML = ""; };
    document.getElementById("m-yes").onclick = function () { m.innerHTML = ""; ok(); };
  }
  function toast(msg, bad) {
    var t = document.createElement("div"); t.className = "toast" + (bad ? " bad" : ""); t.textContent = msg;
    document.body.appendChild(t); setTimeout(function () { t.remove(); }, 4000);
  }
  function bindPower(el) {
    Array.prototype.forEach.call(el.querySelectorAll("[data-r]"), function (b) {
      b.onclick = function () {
        var rt = b.getAttribute("data-r");
        confirmBox(C.t.confirmPower.replace("%s", b.textContent), function () {
          post(C.sys + "/Actions/ComputerSystem.Reset", {ResetType: rt}).then(function () { toast(C.t.done); render(); })
            .catch(function (e) { toast(e.message, true); });
        });
      };
    });
  }
  function bindUid(el, s) {
    Array.prototype.forEach.call(el.querySelectorAll(".uid"), function (u) {
      u.onclick = function () { patch(C.sys, {IndicatorLED: s.IndicatorLED === "Off" ? "Lit" : "Off"}).then(function () { render(); }); };
    });
  }
  function powerMenu(label) {
    return '<div class="dd"><button class="btn" data-dd="1">' + esc(label) + ' &#9662;</button><div class="ddm">' +
      C.powerActions.map(function (a) { return '<button data-r="' + a[1] + '">' + esc(a[0]) + "</button>"; }).join("") + "</div></div>";
  }
  function thumbHtml(id) { return '<div class="thumb" id="' + id + '"><pre></pre></div>'; }
  function fillThumb(el) {
    if (!el) return Promise.resolve();
    return api("/vbsim/console/screen").then(function (s) {
      el.querySelector("pre").textContent = s.lines.slice(0, 26).join("\n");
    }).catch(function (e) {
      if (e.message === "401") throw e;
      el.innerHTML = '<div class="lic">' + esc(C.t.needLicenseConsole) + "</div>";
    });
  }
  function healthRows(s, th, pw) {
    return [[C.t.batteries, "OK"], [C.t.cooling, worst(th.Fans)], [C.t.cpus, ((s.ProcessorSummary || {}).Status || {}).Health || "OK"],
      [C.t.memory, ((s.MemorySummary || {}).Status || {}).Health || "OK"], [C.t.psus, worst(pw.PowerSupplies)],
      [C.t.storageH, "OK"], [C.t.temperatures, worst(th.Temperatures)], [C.t.voltages, worst(pw.Voltages)]];
  }

  /* ---------- Dashboards je Hersteller ---------- */
  var DASH = {};
  function dashData() {
    return Promise.all([api(C.sys), api(C.mgr), soft(api(C.chassis + "/Thermal")), soft(api(C.chassis + "/Power")),
      soft(api(C.log + "/Entries")), soft(api(C.license))]);
  }
  DASH.dell = function (el) {
    return dashData().then(function (r) {
      var s = r[0], m = r[1], th = r[2], pw = r[3], ev = (r[4].Members || []).slice(0, 5), lic = r[5];
      var rows = healthRows(s, th, pw), all = rows.reduce(function (w, x) { return x[1] === "Critical" || (x[1] === "Warning" && w === "OK") ? x[1] : w; }, (s.Status || {}).Health || "OK");
      el.innerHTML =
        '<div class="dashbar">' + powerMenu(C.powerActions[2][0]) +
        '<button class="btn uid">' + ico("bulb") + " " + esc(C.t.identify) + (s.IndicatorLED !== "Off" ? " (" + esc(C.t.uidOn) + ")" : "") + "</button>" +
        '<button class="btn" id="ql-console">' + esc(C.t.launchConsole) + "</button></div>" +
        '<div class="dgrid"><div>' +
        card(C.t.sysHealth, '<div class="bighealth">' + st(all).replace('width="16" height="16"', 'width="38" height="38"') + esc(hword(all)) + "</div>" +
          '<div class="hl">' + rows.map(function (x) { return "<div>" + esc(x[0]) + "</div><div>" + st(x[1]) + "</div>"; }).join("") + "</div>") +
        "</div><div>" +
        card(C.t.systemInfo, kv([[C.t.state, powerBadge(s.PowerState, postOf(s)), 1], [C.t.model, s.Model],
          [C.t.hostname, s.HostName || "–"], [C.t.serial, s.SerialNumber], [C.t.systemRom, s.BiosVersion],
          [C.t.bmcFw, m.FirmwareVersion], [C.t.ip, C.addr], [C.t.mac, C.bmcMac], [C.t.license, lic.Name || "–"]])) +
        "</div><div>" +
        card(C.t.consoleTitle, thumbHtml("dthumb") + '<p><a href="#" id="ql2">' + esc(C.t.launchConsole) + "</a></p>") +
        "</div></div>" +
        card(C.t.recentLogs, table([C.t.severity, C.t.desc, C.t.time],
          ev.map(function (e) { return [st(e.Severity === "OK" ? "OK" : e.Severity), esc(e.Message), esc(fmtTime(e.Created))]; }), true) +
          '<p><a href="#/events">' + esc(C.t.allEvents) + "</a></p>");
      bindPower(el); bindUid(el, s);
      el.querySelector("#ql-console").onclick = launchConsole;
      el.querySelector("#ql2").onclick = function (e) { e.preventDefault(); launchConsole(); };
      el.querySelector("#dthumb").onclick = launchConsole;
      setHTML("hdr-lic", esc((lic.Name || "").replace(/^(Böll|Häpe|Venolo|Hypernano)\s*\d*\s+/, "")));
      header(s);
      return fillThumb(el.querySelector("#dthumb"));
    });
  };
  DASH.hpe = function (el) {
    return dashData().then(function (r) {
      var s = r[0], m = r[1], ev = (r[4].Members || []).slice(0, 6), lic = r[5];
      var uid = (s.IndicatorLED === "Off" ? C.t.uidOff : C.t.uidOn) + ' <button class="btn flat uid">' + esc(C.t.toggleUid) + "</button>";
      el.innerHTML = '<div class="cols"><div>' +
        card(C.t.info, kv([[C.t.hostname, s.HostName || "–"], [C.t.productName, s.Model], ["UUID", s.UUID],
          [C.t.serial, s.SerialNumber], [C.t.productId, s.SKU || "–"], [C.t.systemRom, s.BiosVersion],
          [C.t.remoteConsole, '<a href="#" id="ql-console">HTML5</a>', 1], [C.t.licType, lic.Name || "–"],
          [C.t.bmcFw, m.FirmwareVersion], [C.t.ip, C.addr], [C.t.bmcHost, C.hostLabel]])) +
        "</div><div>" +
        card(C.t.statusH, kv([[C.t.sysHealthShort, st((s.Status || {}).Health) + " " + esc(hword((s.Status || {}).Health)), 1],
          [C.t.bmcHealth, st((m.Status || {}).Health) + " " + esc(hword((m.Status || {}).Health)), 1],
          [C.t.serverPower, powerBadge(s.PowerState, postOf(s)), 1], [C.t.uidLed, uid, 1],
          [C.t.bootStage, (s.BootProgress || {}).LastState || "–"]])) +
        card(C.t.recentEvents, table([C.t.severity, C.t.time, C.t.desc],
          ev.map(function (e) { return [badge(e.Severity, sevCls(e.Severity)), esc(fmtTime(e.Created)), esc(e.Message)]; }), true)) +
        "</div></div>";
      el.querySelector("#ql-console").onclick = function (e) { e.preventDefault(); launchConsole(); };
      bindUid(el, s); header(s);
    });
  };
  DASH.lenovo = function (el) {
    return dashData().then(function (r) {
      var s = r[0], m = r[1], th = r[2], pw = r[3], ev = (r[4].Members || []), lic = r[5];
      var act = ev.filter(function (e) { return e.Severity !== "OK"; }).slice(0, 5);
      var tiles = [[C.t.cpus, ((s.ProcessorSummary || {}).Status || {}).Health || "OK"], [C.t.memory, ((s.MemorySummary || {}).Status || {}).Health || "OK"],
        [C.t.storageH, "OK"], [C.t.pciAdapters, "OK"], [C.t.psus, worst(pw.PowerSupplies)], [C.t.fanH, worst(th.Fans)],
        [C.t.systemBoard, worst(th.Temperatures)], ["BMC", (m.Status || {}).Health || "OK"]];
      var pc = (pw.PowerControl || [{}])[0] || {}, pct = pc.PowerCapacityWatts ? Math.round(100 * (pc.PowerConsumedWatts || 0) / pc.PowerCapacityWatts) : 0;
      var opts = C.powerActions.map(function (a) { return '<option value="' + a[1] + '">' + esc(a[0]) + "</option>"; }).join("");
      el.innerHTML =
        card(C.t.healthSummary + " / " + C.t.activeEvents, '<div class="tiles">' + tiles.map(function (x) {
          return '<div class="tile">' + st(x[1]).replace('class="st"', 'class="st" ').replace('width="16" height="16"', "") + esc(x[0]) + "</div>"; }).join("") + "</div>" +
          (act.length ? table([C.t.severity, C.t.desc, C.t.time], act.map(function (e) {
            return [badge(e.Severity, sevCls(e.Severity)), esc(e.Message), esc(fmtTime(e.Created))]; }), true)
            : '<p class="muted">' + esc(C.t.noActive) + "</p>")) +
        '<div class="hgrid"><div>' +
        card(C.t.sysInfoSettings, kv([[C.t.machineName, s.HostName || C.hostLabel], [C.t.machineType, s.Model],
          [C.t.serial, s.SerialNumber], [C.t.state, powerBadge(s.PowerState, postOf(s)), 1],
          [C.t.systemState, (s.BootProgress || {}).LastState || "–"], [C.t.license, lic.Name || "–"],
          [C.t.bmcFw, m.FirmwareVersion], [C.t.ip, C.addr]])) +
        card(C.t.powerUtil, '<div class="bar"><i style="width:' + pct + '%"></i></div><p>' + esc(pc.PowerConsumedWatts) + " W / " +
          esc(pc.PowerCapacityWatts) + " W (" + pct + " %)</p>") +
        "</div><div>" +
        card(C.t.quickActions, '<div class="form"><label>' + esc(C.t.powerAction) + '</label><select id="qa-p">' + opts + "</select> " +
          '<button class="btn pri" id="qa-go">' + esc(C.t.apply) + "</button>" +
          '<label>' + esc(C.t.uidLed) + '</label><button class="btn uid">' + ico("bulb") + " " +
          esc(s.IndicatorLED === "Off" ? C.t.uidOff : C.t.uidOn) + "</button></div>") +
        card(C.t.consolePreview, thumbHtml("lthumb") + '<p><button class="btn pri" id="ql-console">' + esc(C.t.launch) + "</button></p>") +
        "</div></div>";
      el.querySelector("#qa-go").onclick = function () {
        var sel = el.querySelector("#qa-p"), rt = sel.value, lbl = sel.options[sel.selectedIndex].text;
        confirmBox(C.t.confirmPower.replace("%s", lbl), function () {
          post(C.sys + "/Actions/ComputerSystem.Reset", {ResetType: rt}).then(function () { toast(C.t.done); render(); })
            .catch(function (e) { toast(e.message, true); }); }); };
      el.querySelector("#ql-console").onclick = launchConsole;
      el.querySelector("#lthumb").onclick = launchConsole;
      bindUid(el, s); header(s);
      return fillThumb(el.querySelector("#lthumb"));
    });
  };
  DASH.smc = function (el) {
    return dashData().then(function (r) {
      var s = r[0], m = r[1];
      el.innerHTML = '<div class="cols"><div>' +
        card(C.t.summary, kv([[C.t.fwRev, m.FirmwareVersion], [C.t.biosVer, s.BiosVersion], [C.t.model, s.Model],
          [C.t.serial, s.SerialNumber], [C.t.ip, C.addr], [C.t.bmcMac, C.bmcMac], ["UUID", s.UUID]])) +
        card(C.t.hostIdent, kv([[C.t.server, C.addr], [C.t.userH, C.user]])) +
        "</div><div>" +
        card(C.t.consolePreview, thumbHtml("sthumb") + '<p><button class="btn" id="th-ref">' + esc(C.t.refreshPreview) + "</button></p>") +
        card(C.t.ipmiPower, kv([[C.t.state, powerBadge(s.PowerState, postOf(s)), 1]]) +
          '<p class="acts"><button class="btn" data-r="On">' + esc(C.powerActions[0][0]) + '</button><button class="btn" data-r="GracefulShutdown">' +
          esc(C.powerActions[2][0]) + "</button></p>") +
        "</div></div>";
      bindPower(el); header(s);
      el.querySelector("#sthumb").onclick = launchConsole;
      el.querySelector("#th-ref").onclick = function () { fillThumb(el.querySelector("#sthumb")); };
      return fillThumb(el.querySelector("#sthumb"));
    });
  };

  /* ---------- weitere Seiten ---------- */
  var P = {};
  P.overview = function (el) { return DASH[C.dash || S](el); };
  P.sysinfo = function (el) {
    return Promise.all([members(C.sys + "/Processors"), members(C.sys + "/Memory"),
      members(C.sys + "/EthernetInterfaces")]).then(function (r) {
      el.innerHTML =
        card(C.t.processors, table([C.t.socket, C.t.model, C.t.cores, "MHz", C.t.status],
          r[0].map(function (p) { return [p.Socket, p.Model, p.TotalCores + "/" + p.TotalThreads, p.OperatingSpeedMHz, p.Status.Health]; }))) +
        card(C.t.memory, table([C.t.slot, C.t.size, C.t.type, "MHz", C.t.vendor, C.t.status],
          r[1].map(function (d) { return [d.DeviceLocator, (d.CapacityMiB / 1024) + " GiB", d.MemoryDeviceType, d.OperatingSpeedMhz, d.Manufacturer, d.Status.Health]; }))) +
        card(C.t.nics, table([C.t.name, C.t.mac, C.t.speed, C.t.link],
          r[2].map(function (n) { return [n.Name, n.MACAddress, (n.SpeedMbps / 1000) + " Gbps", n.LinkStatus]; })));
    });
  };
  P.storage = function (el) {
    return api(C.sys + "/Storage/1").then(function (sto) {
      return Promise.all((sto.Drives || []).map(function (d) { return api(d["@odata.id"]); })).then(function (drives) {
        el.innerHTML = card(C.t.physDisks, table([C.t.slot, C.t.model, C.t.capacity, C.t.type, C.t.serial, C.t.status],
          drives.map(function (d) { return [d.Name, d.Model, Math.round(d.CapacityBytes / 1e9) + " GB", d.Protocol + " " + d.MediaType,
            d.SerialNumber, st(d.Status.Health) + " " + esc(d.Status.Health)].map(function (c, i) { return i === 5 ? c : esc(c); }); }), true));
      });
    });
  };
  P.power = function (el) {
    return Promise.all([api(C.sys), api(C.chassis + "/Power")]).then(function (r) {
      var s = r[0], pw = r[1], pc = pw.PowerControl[0];
      el.innerHTML =
        card(C.t.serverPower, kv([[C.t.state, powerBadge(s.PowerState, postOf(s)), 1],
          [C.t.bootStage, (s.BootProgress || {}).LastState || "–"]]) + '<div class="acts">' +
          C.powerActions.map(function (a) { return '<button class="btn' + (a[2] ? " danger" : "") + '" data-r="' + a[1] + '">' + esc(a[0]) + "</button>"; }).join(" ") + "</div>") +
        card(C.t.powerUse, kv([[C.t.present, pc.PowerConsumedWatts + " W"], [C.t.average, pc.PowerMetrics.AverageConsumedWatts + " W"],
          [C.t.peak, pc.PowerMetrics.MaxConsumedWatts + " W"], [C.t.capacity, pc.PowerCapacityWatts + " W"]])) +
        card(C.t.psu, table([C.t.name, C.t.model, C.t.output, C.t.input, C.t.status],
          pw.PowerSupplies.map(function (p) { return [p.Name, p.Model, p.LastPowerOutputWatts + " W", p.LineInputVoltage + " V", p.Status.Health]; })));
      bindPower(el); header(s);
    });
  };
  P.thermal = function (el) {
    return api(C.chassis + "/Thermal").then(function (t) {
      el.innerHTML =
        card(C.t.temps, table([C.t.sensor, C.t.status, C.t.reading, C.t.critAt],
          t.Temperatures.map(function (x) { return [esc(x.Name), st(x.Status.Health) + " " + esc(x.Status.Health),
            esc(x.ReadingCelsius + " °C"), esc(x.UpperThresholdCritical + " °C")]; }), true)) +
        card(C.t.fans, table([C.t.sensor, C.t.status, C.t.speed],
          t.Fans.map(function (f) { return [esc(f.Name), st(f.Status.Health) + " " + esc(f.Status.State + " / " + f.Status.Health),
            esc(f.Reading + " %")]; }), true));
    });
  };
  P.events = function (el) {
    return api(C.log + "/Entries").then(function (d) {
      var rows = (d.Members || []).map(function (e) { return [e.Id, badge(e.Severity, sevCls(e.Severity)), esc((e.Oem || {}).Source),
        esc(fmtTime(e.Created)), esc(e.Message)]; });
      el.innerHTML = card(C.t.eventLog, table(["#", C.t.severity, C.t.source, C.t.time, C.t.desc], rows, true),
        ' <button class="btn danger right" id="clr">' + esc(C.t.clearLog) + "</button>");
      el.querySelector("#clr").onclick = function () {
        confirmBox(C.t.confirmClear, function () { post(C.log + "/Actions/LogService.ClearLog", {}).then(render); }); };
    });
  };
  P.console = function (el) {
    el.innerHTML = card(C.t.remoteConsole, "<p>" + esc(C.t.consoleHelp) + "</p>" + thumbHtml("cthumb") +
      '<p><button class="btn pri" id="lc">' + esc(C.t.launchConsole) + "</button></p>");
    el.querySelector("#lc").onclick = launchConsole;
    el.querySelector("#cthumb").onclick = launchConsole;
    return fillThumb(el.querySelector("#cthumb"));
  };
  P.network = function (el) {
    return api(C.mgr + "/EthernetInterfaces/" + C.nic).then(function (n) {
      var a = (n.IPv4Addresses || [{}])[0];
      el.innerHTML = card(C.t.networkSettings, kv([[C.t.hostname, n.HostName || "–"], [C.t.mac, C.bmcMac],
        [C.t.ip, a.Address], [C.t.mask, a.SubnetMask], [C.t.gateway, a.Gateway || "–"],
        ["DHCP", (n.DHCPv4 || {}).DHCPEnabled ? C.t.enabled : C.t.disabled]]) + '<p class="muted">' + esc(C.t.netHint) + "</p>");
    }).catch(function (e) { if (e.message === "401") throw e; el.innerHTML = card(C.t.networkSettings, "<p class='muted'>–</p>"); });
  };
  P.users = function (el) {
    return members("/redfish/v1/AccountService/Accounts").then(function (u) {
      el.innerHTML = card(C.t.users, table([C.t.id, C.t.username, C.t.role, C.t.status],
        u.map(function (a) { return [a.Id, a.UserName, a.RoleId, a.Enabled === false ? C.t.disabled : C.t.enabled]; })));
    });
  };
  P.firmware = function (el) {
    return members("/redfish/v1/UpdateService/FirmwareInventory").then(function (f) {
      el.innerHTML = card(C.t.firmwareInv, table([C.t.name, C.t.version, C.t.updateable],
        f.map(function (x) { return [x.Name, x.Version, x.Updateable ? C.t.yes : C.t.no]; })));
    });
  };
  P.bios = function (el) {
    return api(C.sys + "/Bios").then(function (b) {
      el.innerHTML = card(C.t.biosAttrs, table([C.t.attribute, C.t.value], Object.keys(b.Attributes || {}).map(function (k) {
        return [k, b.Attributes[k]]; })) + '<p class="muted">' + esc(C.t.biosHint) + "</p>");
    });
  };

  P.vmedia = function (el) {
    return Promise.all([members(C.mgr + "/VirtualMedia"), api(C.sys), api(C.license)]).then(function (r) {
      var devs = r[0], bo = r[1].Boot || {}, lic = r[2];
      var rows = devs.map(function (d) { return [esc(d.Id), esc(d.MediaTypes.join(", ")), esc(d.Image || "–"),
        badge(d.Inserted ? C.t.inserted : C.t.notInserted, d.Inserted ? "ok" : "off"),
        d.Inserted ? '<button class="btn danger" data-eject="' + esc(d.Id) + '">' + esc(C.t.eject) + "</button>" : ""]; });
      var opts = devs.map(function (d) { return '<option value="' + esc(d.Id) + '">' + esc(d.Id) + " (" + esc(d.MediaTypes.join("/")) + ")</option>"; }).join("");
      var targets = (bo["BootSourceOverrideTarget@Redfish.AllowableValues"] || ["None"]).map(function (t) {
        return '<option' + (t === bo.BootSourceOverrideTarget ? " selected" : "") + ">" + esc(t) + "</option>"; }).join("");
      var enab = ["Disabled", "Once", "Continuous"].map(function (t) {
        return '<option' + (t === bo.BootSourceOverrideEnabled ? " selected" : "") + ">" + t + "</option>"; }).join("");
      el.innerHTML = (lic.Removable ? "" : '<p class="err">' + esc(C.t.needLicenseVm) + "</p>") +
        card(C.t.vmedia, table([C.t.device, C.t.mediaTypes, C.t.image, C.t.status, ""], rows, true)) +
        card(C.t.insertMedia, '<div class="form"><label>' + esc(C.t.device) + '</label><select id="vm-dev">' + opts + "</select>" +
          '<label>' + esc(C.t.imageUrl) + '</label><input id="vm-url" type="text" placeholder="http://server/image.iso" size="48">' +
          '<label><input id="vm-wp" type="checkbox" checked> ' + esc(C.t.writeProtect) + "</label>" +
          '<p><button class="btn pri" id="vm-ins">' + esc(C.t.insert) + "</button></p></div>") +
        card(C.t.bootNext, '<div class="form"><label>' + esc(C.t.bootTarget) + '</label><select id="bo-t">' + targets + "</select>" +
          '<label>' + esc(C.t.bootEnabled) + '</label><select id="bo-e">' + enab + "</select>" +
          '<p><button class="btn pri" id="bo-apply">' + esc(C.t.apply) + "</button></p></div>");
      Array.prototype.forEach.call(el.querySelectorAll("[data-eject]"), function (b) {
        b.onclick = function () { post(C.mgr + "/VirtualMedia/" + b.getAttribute("data-eject") + "/Actions/VirtualMedia.EjectMedia", {})
          .then(function () { toast(C.t.done); render(); }).catch(function (e) { toast(e.message, true); }); }; });
      el.querySelector("#vm-ins").onclick = function () {
        post(C.mgr + "/VirtualMedia/" + el.querySelector("#vm-dev").value + "/Actions/VirtualMedia.InsertMedia",
          {Image: el.querySelector("#vm-url").value, Inserted: true, WriteProtected: el.querySelector("#vm-wp").checked})
          .then(function () { toast(C.t.done); render(); }).catch(function (e) { toast(e.message, true); }); };
      el.querySelector("#bo-apply").onclick = function () {
        patch(C.sys, {Boot: {BootSourceOverrideTarget: el.querySelector("#bo-t").value,
          BootSourceOverrideEnabled: el.querySelector("#bo-e").value}})
          .then(function () { toast(C.t.done); render(); }).catch(function (e) { toast(e.message, true); }); };
    });
  };
  P.vmedia.noRefresh = true;
  P.license = function (el) {
    return api(C.license).then(function (l) {
      el.innerHTML = card(C.t.license, kv([[C.t.licenseName, l.Name], [C.t.licenseType, l.LicenseType],
        [C.t.licenseKey, l.LicenseString || "–"], [C.t.installDate, l.InstallDate ? fmtTime(l.InstallDate) : "–"],
        [C.t.status, badge((l.Status || {}).Health || "OK", "ok"), 1]]) +
        (l.Removable ? '<p><button class="btn danger" id="lic-rm">' + esc(C.t.removeKey) + "</button></p>" : "")) +
        card(C.t.installKey, '<div class="form"><label>' + esc(C.t.licenseKey) + '</label>' +
          '<input id="lic-key" type="text" placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX" size="40">' +
          '<p><button class="btn pri" id="lic-ins">' + esc(C.t.install) + "</button></p></div>" +
          '<p class="muted">' + esc(C.t.licenseHint) + "</p>");
      el.querySelector("#lic-ins").onclick = function () {
        post("/redfish/v1/LicenseService/Actions/LicenseService.Install", {LicenseString: el.querySelector("#lic-key").value})
          .then(function () { toast(C.t.done); render(); }).catch(function (e) { toast(e.message, true); }); };
      var rm = el.querySelector("#lic-rm");
      if (rm) rm.onclick = function () { confirmBox(C.t.confirmRemoveKey, function () {
        api("/redfish/v1/LicenseService/Licenses/1", {method: "DELETE"}).then(function () { toast(C.t.done); render(); })
          .catch(function (e) { toast(e.message, true); }); }); };
    });
  };
  P.license.noRefresh = true;

  P.certificate = function (el) {
    return api(C.cert).then(function (c) {
      var yn = function (b) { return b ? C.t.yes : C.t.no; };
      el.innerHTML = card(C.t.certificate, kv([[C.t.subject, (c.Subject || {}).CommonName], [C.t.issuer, (c.Issuer || {}).CommonName],
        [C.t.validFrom, fmtTime(c.ValidNotBefore)], [C.t.validTo, fmtTime(c.ValidNotAfter)], [C.t.serial, c.SerialNumber],
        [C.t.fingerprint, c.Fingerprint], [C.t.selfSigned, yn((c.Oem || {}).SelfSigned)]])) +
        card(C.t.genCsr, '<div class="form"><label>' + esc(C.t.commonName) + '</label><input id="csr-cn" type="text" size="40">' +
          '<label>' + esc(C.t.organization) + '</label><input id="csr-o" type="text" size="40">' +
          '<label>' + esc(C.t.country) + '</label><input id="csr-c" type="text" size="3" maxlength="2">' +
          '<p><button class="btn pri" id="csr-go">' + esc(C.t.genCsr) + "</button></p>" +
          '<textarea id="csr-out" rows="8" cols="70" readonly placeholder="-----BEGIN CERTIFICATE REQUEST-----"></textarea></div>') +
        card(C.t.importCert, '<div class="form"><label>' + esc(C.t.pem) + '</label>' +
          '<textarea id="cert-pem" rows="8" cols="70" placeholder="-----BEGIN CERTIFICATE-----"></textarea>' +
          '<p><button class="btn pri" id="cert-go">' + esc(C.t.install) + "</button></p></div>" +
          '<p class="muted">' + esc(C.t.certHint) + "</p>");
      el.querySelector("#csr-go").onclick = function () {
        post("/redfish/v1/CertificateService/Actions/CertificateService.GenerateCSR",
          {CommonName: el.querySelector("#csr-cn").value, Organization: el.querySelector("#csr-o").value,
           Country: el.querySelector("#csr-c").value}).then(function (r) {
          el.querySelector("#csr-out").value = r.CSRString || ""; toast(C.t.done); })
          .catch(function (e) { toast(e.message, true); }); };
      el.querySelector("#cert-go").onclick = function () {
        post("/redfish/v1/CertificateService/Actions/CertificateService.ReplaceCertificate",
          {CertificateString: el.querySelector("#cert-pem").value, CertificateType: "PEM"})
          .then(function () { toast(C.t.done); render(); }).catch(function (e) { toast(e.message, true); }); };
    });
  };
  P.certificate.noRefresh = true;

  function launchConsole() { window.open(C.consolePath, "vbmc_console", "width=1024,height=720"); }

  /* ---------- Steuerung ---------- */
  function route() { var k = (location.hash || "").slice(2); return P[k] && ITEMS.some(function (i) { return i.k === k; }) ? k : ITEMS[0].k; }
  function render() {
    var k = route(), page = P[k], el = document.getElementById("main");
    chrome(k);
    if (timer) clearTimeout(timer);
    var holder = document.createElement("div");
    page(holder).then(function () {
      el.innerHTML = ""; el.appendChild(holder);
      if (S === "hpe") fillThumb(document.getElementById("navthumb")).catch(function () {});
      if (k !== "overview" && k !== "power") soft(api(C.sys)).then(function (s) { if (s.PowerState) header(s); });
      if (!page.noRefresh) timer = setTimeout(function () {
        if (!document.getElementById("modal").innerHTML && !document.querySelector(".dd.open")) render(); }, 4000);
    }).catch(function (e) { if (e.message !== "401") { el.innerHTML = '<p class="err">' + esc(e.message) + "</p>"; } });
  }
  window.addEventListener("hashchange", render);
  shell();
  render();
})();
