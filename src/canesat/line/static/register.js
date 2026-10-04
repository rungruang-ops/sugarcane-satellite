/* เบิ่งไฮ่ — LIFF plot registration (MapLibre GL + LIFF SDK). No build step. */
(function () {
  "use strict";
  var CFG = JSON.parse(document.getElementById("canesat-config").textContent || "{}");
  var M2_PER_RAI = 1600;
  var $ = function (id) { return document.getElementById(id); };
  var pts = [];            // [[lng, lat], ...] in tap order
  var caneType = "unknown";
  var idToken = null;
  var liffReady = false;
  var saving = false;

  // ------------------------------------------------------------------ geometry
  function rad(d) { return d * Math.PI / 180; }
  // spherical polygon area (same formula as @mapbox/geojson-area), m²
  function ringAreaM2(c) {
    var R = 6378137, n = c.length, a = 0;
    if (n < 3) return 0;
    for (var i = 0; i < n; i++) {
      var p1 = c[i], p2 = c[(i + 1) % n];
      a += (rad(p2[0]) - rad(p1[0])) * (2 + Math.sin(rad(p1[1])) + Math.sin(rad(p2[1])));
    }
    return Math.abs(a * R * R / 2);
  }
  function segX(a, b, c, d) {
    function o(p, q, r) { var v = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1]); return v > 0 ? 1 : v < 0 ? -1 : 0; }
    return o(a, b, c) !== o(a, b, d) && o(c, d, a) !== o(c, d, b);
  }
  function selfIntersects(c) {
    var n = c.length;
    if (n < 4) return false;
    for (var i = 0; i < n; i++) {
      var a = c[i], b = c[(i + 1) % n];
      for (var j = i + 1; j < n; j++) {
        if (Math.abs(i - j) <= 1 || (i === 0 && j === n - 1)) continue;
        if (segX(a, b, c[j], c[(j + 1) % n])) return true;
      }
    }
    return false;
  }
  function status() {
    if (pts.length < 3) return { ok: false, msg: "แตะอย่างน้อย 3 จุด", cls: "" };
    if (selfIntersects(pts)) return { ok: false, msg: "⚠️ เส้นขอบตัดกัน", cls: "bad" };
    var rai = ringAreaM2(pts) / M2_PER_RAI;
    if (rai < CFG.minRai) return { ok: false, msg: "⚠️ เล็กเกินไป", cls: "bad", rai: rai };
    if (rai > CFG.maxRai) return { ok: false, msg: "⚠️ ใหญ่เกินไป", cls: "bad", rai: rai };
    return { ok: true, msg: "✓ ขอบเขตถูกต้อง", cls: "ok", rai: rai };
  }
  function fmtRai(r) { return r >= 10 ? Math.round(r).toLocaleString("th-TH") : r.toFixed(1); }

  // ------------------------------------------------------------------ map
  var map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        esri: {
          type: "raster",
          tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
          tileSize: 256, maxzoom: 19,
          attribution: "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community"
        }
      },
      layers: [{ id: "esri", type: "raster", source: "esri" }]
    },
    center: CFG.center || [102.55, 16.45],
    zoom: CFG.zoom || 11,
    maxZoom: 19,
    attributionControl: { compact: true },
    dragRotate: false, pitchWithRotate: false, touchPitch: false
  });
  map.touchZoomRotate.disableRotation();

  function fc(features) { return { type: "FeatureCollection", features: features }; }
  function draftData() {
    var f = [];
    if (pts.length >= 3) f.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [pts.concat([pts[0]])] } });
    else if (pts.length === 2) f.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: pts } });
    return fc(f);
  }
  function vertexData() { return fc(pts.map(function (p) { return { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } }; })); }

  map.on("load", function () {
    map.addSource("mine", { type: "geojson", data: fc([]) });
    map.addLayer({ id: "mine-line", type: "line", source: "mine", paint: { "line-color": "#fff", "line-width": 1.5, "line-dasharray": [3, 2] } });
    map.addSource("draft", { type: "geojson", data: draftData() });
    map.addLayer({ id: "draft-fill", type: "fill", source: "draft", filter: ["==", "$type", "Polygon"], paint: { "fill-color": "#06c755", "fill-opacity": 0.25 } });
    map.addLayer({ id: "draft-line", type: "line", source: "draft", paint: { "line-color": "#06c755", "line-width": 3 } });
    map.addSource("vtx", { type: "geojson", data: vertexData() });
    map.addLayer({ id: "vtx", type: "circle", source: "vtx", paint: { "circle-radius": 7, "circle-color": "#fff", "circle-stroke-color": "#06c755", "circle-stroke-width": 3 } });
    redraw();
  });
  map.on("click", function (e) { pts.push([+e.lngLat.lng.toFixed(7), +e.lngLat.lat.toFixed(7)]); redraw(); });

  function redraw() {
    if (map.getSource("draft")) { map.getSource("draft").setData(draftData()); map.getSource("vtx").setData(vertexData()); }
    var s = status();
    $("area").textContent = s.rai !== undefined ? "≈ " + fmtRai(s.rai) + " ไร่" : "— ไร่";
    var chip = $("chip"); chip.textContent = s.msg; chip.className = "chip " + s.cls;
    refreshSave();
  }
  function undo() { pts.pop(); redraw(); }
  $("btn-undo").onclick = undo; $("btn-undo2").onclick = undo;
  $("btn-clear").onclick = function () { pts = []; redraw(); };
  $("btn-locate").onclick = function () {
    if (!navigator.geolocation) return toast("เครื่องนี้หาตำแหน่งไม่ได้");
    navigator.geolocation.getCurrentPosition(function (p) {
      map.flyTo({ center: [p.coords.longitude, p.coords.latitude], zoom: 16 });
    }, function () { toast("หาตำแหน่งไม่ได้ — อนุญาตการเข้าถึงตำแหน่งก่อน"); }, { enableHighAccuracy: true, timeout: 10000 });
  };

  // ------------------------------------------------------------------ form
  Array.prototype.forEach.call(document.querySelectorAll("#cane-type button"), function (b) {
    b.onclick = function () {
      caneType = b.dataset.v;
      Array.prototype.forEach.call(document.querySelectorAll("#cane-type button"), function (x) { x.classList.toggle("on", x === b); });
      $("date-label").textContent = caneType === "plant" ? "ปลูกเมื่อ (ประมาณ)" : caneType === "ratoon" ? "ตัดครั้งล่าสุด (ประมาณ)" : "ปลูก / ตัดครั้งล่าสุด (ประมาณ)";
    };
  });
  var today = new Date();
  $("ref-date").max = today.getFullYear() + "-" + String(today.getMonth() + 1).padStart(2, "0");
  $("on-behalf").onchange = function () {
    var on = this.checked;
    $("member").classList.toggle("hidden", !on);
    $("assist-row").classList.toggle("hidden", !on);
    $("lead").classList.toggle("hidden", !on);
    $("consent-note").textContent = on ? "อ่านให้เจ้าของแปลงฟัง แล้วติ๊กตามที่เจ้าของแปลงเลือก" : "เลือกได้ทีละข้อ ข้อ \"จำเป็น\" ต้องติ๊กก่อนบันทึก";
    refreshSave();
  };
  $("member-name").oninput = function () { $("lead-name").textContent = this.value || "—"; refreshSave(); };
  ["name", "c-service", "c-assist"].forEach(function (id) { $(id).addEventListener("input", refreshSave); $(id).addEventListener("change", refreshSave); });

  function formProblems() {
    if (!status().ok) return "วาดขอบเขตแปลงให้ถูกต้องก่อน";
    if (!$("name").value.trim()) return "ตั้งชื่อแปลงก่อน";
    if ($("on-behalf").checked && !$("member-name").value.trim()) return "ใส่ชื่อเจ้าของแปลงก่อน";
    if (!$("c-service").checked) return "ต้องติ๊กความยินยอมข้อ 'จำเป็น'";
    if ($("on-behalf").checked && !$("c-assist").checked) return "หัวหน้ากลุ่มต้องยืนยันว่าอ่านความยินยอมให้เจ้าของแปลงฟังแล้ว";
    return null;
  }
  function refreshSave() {
    var p = formProblems();
    $("btn-save").disabled = !!p || saving || !liffReady;
    $("btn-save").title = p || "";
  }

  // ------------------------------------------------------------------ API
  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: Object.assign({ "Content-Type": "application/json" }, idToken ? { Authorization: "Bearer " + idToken } : {}),
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401 && window.liff && liff.isLoggedIn && liff.isLoggedIn()) { liff.logout(); liff.login({ redirectUri: location.href }); }
        if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "บันทึกไม่สำเร็จ (" + r.status + ")");
        return j;
      });
    });
  }
  function caneLabel(t) { return t === "plant" ? "อ้อยปลูก" : t === "ratoon" ? "อ้อยตอ" : "ไม่ระบุ"; }
  function loadMine() {
    if (!liffReady) { $("mine-list").textContent = "เปิดจาก LINE เพื่อดูแปลงของคุณ"; return; }
    api("GET", "/api/plots").then(function (j) {
      var list = $("mine-list"); list.textContent = ""; list.className = "";
      if (!j.plots.length) { list.className = "note"; list.textContent = "ยังไม่มีแปลง — วาดแปลงแรกด้านบนได้เลย"; }
      j.plots.forEach(function (p) {
        var row = document.createElement("div"); row.className = "plot";
        var left = document.createElement("div");
        var nm = document.createElement("div"); nm.textContent = p.name + (p.owner_name ? " — ของ" + p.owner_name : "");
        var sub = document.createElement("div"); sub.className = "sub";
        sub.textContent = caneLabel(p.cane_type) + " · " + (p.n_clear_obs ? "มีภาพดาวเทียม " + p.n_clear_obs + " ภาพ" : "กำลังดึงภาพดาวเทียม…");
        left.appendChild(nm); left.appendChild(sub);
        var right = document.createElement("div"); right.textContent = p.area_rai != null ? "≈ " + fmtRai(p.area_rai) + " ไร่" : "";
        row.appendChild(left); row.appendChild(right);
        row.onclick = function () {
          var c = p.geometry.coordinates[0][0]; var xs = c.map(function (q) { return q[0]; }), ys = c.map(function (q) { return q[1]; });
          map.fitBounds([[Math.min.apply(null, xs), Math.min.apply(null, ys)], [Math.max.apply(null, xs), Math.max.apply(null, ys)]], { padding: 40, maxZoom: 17 });
          window.scrollTo({ top: 0, behavior: "smooth" });
        };
        list.appendChild(row);
      });
      var feats = j.plots.map(function (p) { return { type: "Feature", properties: {}, geometry: p.geometry }; });
      var setMine = function () { map.getSource("mine").setData(fc(feats)); };
      if (map.getSource("mine")) setMine(); else map.once("load", setMine);
    }).catch(function (e) { $("mine-list").textContent = e.message; });
  }

  $("btn-save").onclick = function () {
    var p = formProblems(); if (p) return showErr(p);
    var s = status(), name = $("name").value.trim();
    var body = {
      name: name,
      cane_type: caneType,
      ref_date: $("ref-date").value || null,
      geometry: { type: "Polygon", coordinates: [pts.concat([pts[0]])] },
      on_behalf: $("on-behalf").checked,
      member_name: $("on-behalf").checked ? $("member-name").value.trim() : null,
      member_phone: $("on-behalf").checked ? ($("member-phone").value.trim() || null) : null,
      consents: { service: $("c-service").checked, leader_view: $("c-leader").checked, research: $("c-research").checked },
      assisted_consent_confirmed: $("on-behalf").checked && $("c-assist").checked
    };
    saving = true; refreshSave(); showErr(null); $("btn-save").textContent = "กำลังบันทึก…";
    api("POST", "/api/plots", body).then(function (res) {
      var msg = "✅ ลงทะเบียนแปลง \"" + res.name + "\" (≈ " + fmtRai(res.area_rai) + " ไร่) แล้ว";
      var ctx = liff.getContext && liff.getContext();
      var canSend = liff.isInClient() && ctx && ["utou", "group", "room", "square_chat"].indexOf(ctx.type) >= 0;
      var done = function () {
        if (liff.isInClient()) { liff.closeWindow(); return; }
        toast(msg); pts = []; redraw(); $("name").value = ""; loadMine();
      };
      if (canSend) liff.sendMessages([{ type: "text", text: msg }]).then(done, done);
      else done();
    }).catch(function (e) { showErr(e.message); })
      .then(function () { saving = false; $("btn-save").textContent = "บันทึกแปลง"; refreshSave(); });
  };
  $("btn-close").onclick = function () { if (window.liff && liff.isInClient && liff.isInClient()) liff.closeWindow(); else history.back(); };

  function showErr(m) { $("err").textContent = m || ""; $("err").classList.toggle("hidden", !m); }
  var toastTimer;
  function toast(m) { var t = $("toast"); t.textContent = m; t.classList.remove("hidden"); clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.add("hidden"); }, 3500); }
  function banner(m) { $("banner").textContent = m; $("banner").classList.remove("hidden"); }

  // ------------------------------------------------------------------ LIFF
  function boot() {
    if (/[?&]view=my/.test(location.search) || /view%3Dmy/.test(location.search)) setTimeout(function () { $("my-plots").scrollIntoView({ behavior: "smooth" }); }, 600);
    if (!CFG.liffId) {
      banner("ทดลองวาดแปลงได้ แต่ยังบันทึกไม่ได้ — ยังไม่ได้ตั้งค่า LIFF");
      $("btn-save").textContent = "ยังบันทึกไม่ได้"; loadMine(); return;
    }
    liff.init({ liffId: CFG.liffId }).then(function () {
      if (!liff.isLoggedIn()) { liff.login({ redirectUri: location.href }); return; }
      idToken = liff.getIDToken();
      if (!idToken) { banner("ไม่ได้รับสิทธิ์ openid — ตั้งค่า LIFF scope ให้มี openid"); return; }
      liffReady = true; refreshSave(); loadMine();
    }).catch(function (e) { banner("เปิด LIFF ไม่สำเร็จ: " + (e && e.message ? e.message : e)); });
  }
  boot();
})();
