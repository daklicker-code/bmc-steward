/* vBMC-Weboberfläche: eine Single-Page-App für alle Hersteller. Liest live aus Redfish (Cookie-Session),
   Layout/Menüs/Begriffe kommen aus window.VB (Skin des jeweiligen Herstellers). */
(function () {
  "use strict";
  var C = window.VB, root = document.getElementById("app");
  var timer = null, cur = null;

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
  function fmtTime(iso) { var d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString(C.locale || "de-DE"); }
  function dur(s) { var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return (d ? d + " d " : "") + h + " h " + m + " min"; }
  function sevCls(s) { return s === "Critical" ? "bad" : (s === "Warning" ? "warn" : "ok"); }
  function badge(txt, cls) { return '<span class="badge ' + (cls || "ok") + '">' + esc(txt) + "</span>"; }
  function kv(rows) { return '<table class="kv">' + rows.map(function (r) {
    return "<tr><th>" + esc(r[0]) + "</th><td>" + (r[2] ? r[1] : esc(r[1])) + "</td></tr>"; }).join("") + "</table>"; }
  function table(head, rows, raw) { return '<table class="grid"><thead><tr>' + head.map(function (h) {
    return "<th>" + esc(h) + "</th>"; }).join("") + "</tr></thead><tbody>" + (rows.length ? rows.map(function (r) {
    return "<tr>" + r.map(function (c) { return "<td>" + (raw ? c : esc(c)) + "</td>"; }).join("") + "</tr>";
  }).join("") : '<tr><td colspan="' + head.length + '" class="muted">–</td></tr>') + "</tbody></table>"; }
  function card(title, body, extra) { return '<section class="card"><h3>' + esc(title) + (extra || "") +
    "</h3><div>" + body + "</div></section>"; }
  function powerBadge(ps, post) {
    var map = {On: ["ok", C.t.on], Off: ["off", C.t.off], PoweringOn: ["warn", C.t.poweringOn], PoweringOff: ["warn", C.t.poweringOff]};
    var m = map[ps] || ["off", ps];
    var extra = (ps === "On" && post && post !== "FinishedPost") ? " – POST" : "";
    return badge(m[1] + extra, m[0]);
  }

  /* ---------- Rahmen ---------- */
  function shell() {
    var nav = C.nav.map(function (n) {
      if (n.section) return '<div class="navsec">' + esc(n.section) + "</div>";
      return '<a class="navitem" data-k="' + n.k + '" href="#/' + n.k + '">' + esc(n.label) + "</a>";
    }).join("");
    root.innerHTML =
      '<header class="top"><div class="brand"><span class="logo"></span><span class="bt">' + esc(C.product) +
      '</span><span class="sub">' + esc(C.model) + "</span></div>" +
      '<div class="topr"><span id="hdr-power"></span><span class="who">' + esc(C.hostLabel) + '</span>' +
      '<button class="btn flat" id="logout">' + esc(C.t.logout) + "</button></div></header>" +
      '<div class="body lay-' + C.layout + '"><nav class="nav">' + nav + '</nav><main id="main"></main></div>' +
      '<footer class="foot">' + esc(C.t.simNote) + "</footer>" + '<div id="modal"></div>';
    document.getElementById("logout").onclick = function () {
      fetch("/redfish/v1/SessionService/Sessions/x", {method: "DELETE", credentials: "same-origin"})
        .then(function () { location.replace("/"); }); };
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

  /* ---------- Seiten ---------- */
  var P = {};
  P.overview = function (el) {
    return Promise.all([api(C.sys), api(C.mgr), api(C.chassis), api(C.log + "/Entries")]).then(function (r) {
      var s = r[0], m = r[1], ch = r[2], ev = (r[3].Members || []).slice(0, 6);
      var postSt = ((s.Oem || {}).Hpe || {}).PostState;
      var oem = (m.Oem || {});
      el.innerHTML =
        '<div class="cols"><div>' +
        card(C.t.systemInfo, kv([[C.t.model, s.Model], [C.t.serial, s.SerialNumber], ["UUID", s.UUID],
          [C.t.bios, s.BiosVersion], [C.t.cpu, (s.ProcessorSummary || {}).Count + " x " + (s.ProcessorSummary || {}).Model],
          [C.t.memory, (s.MemorySummary || {}).TotalSystemMemoryGiB + " GiB"],
          [C.t.power, powerBadge(s.PowerState, postSt), 1],
          [C.t.health, badge((s.Status || {}).Health || "OK", sevCls((s.Status || {}).Health)), 1],
          ["UID", s.IndicatorLED === "Off" ? esc(C.t.off) : esc(s.IndicatorLED) +
            ' <button class="btn flat" id="uid">' + esc(C.t.toggleUid) + "</button>", 1]])) +
        card(C.t.bmcInfo, kv([[C.t.bmcName, C.hostLabel], [C.t.firmware, m.FirmwareVersion + " (" + m.Model + ")"],
          [C.t.ip, C.addr], [C.t.mac, C.bmcMac], [C.t.hostname, s.HostName || "–"]])) +
        "</div><div>" +
        card(C.t.recentEvents, table([C.t.severity, C.t.time, C.t.desc],
          ev.map(function (e) { return [badge(e.Severity, sevCls(e.Severity)), esc(fmtTime(e.Created)), esc(e.Message)]; }), true) +
          '<p><a href="#/events">' + esc(C.t.allEvents) + "</a></p>") +
        card(C.t.quickLaunch, '<button class="btn pri" id="ql-console">' + esc(C.t.launchConsole) + "</button> " +
          '<a class="btn" href="#/power">' + esc(C.t.powerCtl) + "</a>") +
        "</div></div>";
      var u = el.querySelector("#uid");
      if (u) u.onclick = function () { patch(C.sys, {IndicatorLED: s.IndicatorLED === "Off" ? "Lit" : "Off"}).then(function () { render(); }); };
      el.querySelector("#ql-console").onclick = launchConsole;
      document.getElementById("hdr-power").innerHTML = powerBadge(s.PowerState, postSt);
    });
  };
  P.sysinfo = function (el) {
    return Promise.all([members(C.sys + "/Processors"), members(C.sys + "/Memory"),
      members(C.sys + "/EthernetInterfaces"), api(C.sys + "/Storage/1")]).then(function (r) {
      return Promise.all((r[3].Drives || []).map(function (d) { return api(d["@odata.id"]); })).then(function (drives) {
        el.innerHTML =
          card(C.t.processors, table([C.t.socket, C.t.model, C.t.cores, "MHz", C.t.status],
            r[0].map(function (p) { return [p.Socket, p.Model, p.TotalCores + "/" + p.TotalThreads, p.OperatingSpeedMHz, p.Status.Health]; })), "") +
          card(C.t.memory, table([C.t.slot, C.t.size, C.t.type, "MHz", C.t.vendor, C.t.status],
            r[1].map(function (d) { return [d.DeviceLocator, (d.CapacityMiB / 1024) + " GiB", d.MemoryDeviceType, d.OperatingSpeedMhz, d.Manufacturer, d.Status.Health]; }))) +
          card(C.t.storage, table([C.t.slot, C.t.model, C.t.capacity, C.t.type, C.t.serial, C.t.status],
            drives.map(function (d) { return [d.Name, d.Model, Math.round(d.CapacityBytes / 1e9) + " GB", d.Protocol + " " + d.MediaType, d.SerialNumber, d.Status.Health]; }))) +
          card(C.t.nics, table([C.t.name, C.t.mac, C.t.speed, C.t.link],
            r[2].map(function (n) { return [n.Name, n.MACAddress, (n.SpeedMbps / 1000) + " Gbps", n.LinkStatus]; })));
      });
    });
  };
  P.power = function (el) {
    return Promise.all([api(C.sys), api(C.chassis + "/Power")]).then(function (r) {
      var s = r[0], pw = r[1], pc = pw.PowerControl[0], on = s.PowerState !== "Off";
      var postSt = ((s.Oem || {}).Hpe || {}).PostState;
      el.innerHTML =
        card(C.t.serverPower, kv([[C.t.state, powerBadge(s.PowerState, postSt), 1],
          [C.t.bootStage, (s.BootProgress || {}).LastState || "–"]]) + '<div class="acts">' +
          C.powerActions.map(function (a) { return '<button class="btn' + (a[2] ? " danger" : "") + '" data-r="' + a[1] + '">' + esc(a[0]) + "</button>"; }).join(" ") + "</div>") +
        card(C.t.powerUse, kv([[C.t.present, pc.PowerConsumedWatts + " W"], [C.t.average, pc.PowerMetrics.AverageConsumedWatts + " W"],
          [C.t.peak, pc.PowerMetrics.MaxConsumedWatts + " W"], [C.t.capacity, pc.PowerCapacityWatts + " W"]])) +
        card(C.t.psu, table([C.t.name, C.t.model, C.t.output, C.t.input, C.t.status],
          pw.PowerSupplies.map(function (p) { return [p.Name, p.Model, p.LastPowerOutputWatts + " W", p.LineInputVoltage + " V", p.Status.Health]; })));
      Array.prototype.forEach.call(el.querySelectorAll("[data-r]"), function (b) {
        b.onclick = function () {
          var rt = b.getAttribute("data-r");
          confirmBox(C.t.confirmPower.replace("%s", b.textContent), function () {
            post(C.sys + "/Actions/ComputerSystem.Reset", {ResetType: rt}).then(function () { toast(C.t.done); render(); })
              .catch(function (e) { toast(e.message, true); });
          });
        };
      });
      document.getElementById("hdr-power").innerHTML = powerBadge(s.PowerState, postSt);
    });
  };
  P.thermal = function (el) {
    return api(C.chassis + "/Thermal").then(function (t) {
      el.innerHTML =
        card(C.t.temps, table([C.t.sensor, C.t.reading, C.t.warnAt, C.t.critAt, C.t.status],
          t.Temperatures.map(function (x) { return [x.Name, x.ReadingCelsius + " °C", "", x.UpperThresholdCritical + " °C", x.Status.Health]; }))) +
        card(C.t.fans, table([C.t.sensor, C.t.speed, C.t.status],
          t.Fans.map(function (f) { return [f.Name, f.Reading + " %", f.Status.State + " / " + f.Status.Health]; })));
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
    el.innerHTML = card(C.t.remoteConsole, "<p>" + esc(C.t.consoleHelp) + '</p><div class="thumb" id="thumb"><pre id="thumbtxt"></pre></div>' +
      '<p><button class="btn pri" id="lc">' + esc(C.t.launchConsole) + "</button></p>");
    el.querySelector("#lc").onclick = launchConsole;
    el.querySelector("#thumb").onclick = launchConsole;
    return api("/vbsim/console/screen").then(function (s) {
      el.querySelector("#thumbtxt").textContent = s.lines.slice(0, 12).join("\n");
    }).catch(function (e) {
      if (e.message === "401") throw e;
      el.querySelector("#thumb").innerHTML = '<p class="err" style="padding:10px">' + esc(C.t.needLicenseConsole) + "</p>";
    });
  };
  P.network = function (el) {
    return api(C.mgr + "/EthernetInterfaces/" + C.nic).then(function (n) {
      var a = (n.IPv4Addresses || [{}])[0];
      el.innerHTML = card(C.t.networkSettings, kv([[C.t.hostname, n.HostName || "–"], [C.t.mac, C.bmcMac],
        [C.t.ip, a.Address], [C.t.mask, a.SubnetMask], [C.t.gateway, a.Gateway || "–"],
        ["DHCP", (n.DHCPv4 || {}).DHCPEnabled ? C.t.enabled : C.t.disabled]]) + '<p class="muted">' + esc(C.t.netHint) + "</p>");
    }).catch(function () { el.innerHTML = card(C.t.networkSettings, "<p class='muted'>–</p>"); });
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

  var popup = null;
  function launchConsole() {
    popup = window.open(C.consolePath, "vbmc_console", "width=980,height=700");
  }

  /* ---------- Steuerung ---------- */
  function route() { return (location.hash || "#/" + C.nav.filter(function (n) { return n.k; })[0].k).slice(2); }
  function render() {
    var k = route(), page = P[k] || P.overview, el = document.getElementById("main");
    Array.prototype.forEach.call(document.querySelectorAll(".navitem"), function (a) {
      a.classList.toggle("on", a.getAttribute("data-k") === k); });
    if (timer) clearTimeout(timer);
    var h = document.createElement("h2"); h.textContent = (C.nav.filter(function (n) { return n.k === k; })[0] || {}).label || "";
    var holder = document.createElement("div");
    page(holder).then(function () {
      el.innerHTML = ""; el.appendChild(h); el.appendChild(holder);
      cur = k;
      if (!page.noRefresh) timer = setTimeout(function () { if (!document.getElementById("modal").innerHTML) render(); }, 4000);
    }).catch(function (e) { if (e.message !== "401") { el.innerHTML = '<p class="err">' + esc(e.message) + "</p>"; } });
  }
  window.addEventListener("hashchange", render);
  shell();
  render();
})();
