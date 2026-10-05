(function () {
  const cfg = JSON.parse(document.getElementById("canesat-config").textContent || "{}");
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const plotId = params.get("plot_id");
  const tokenParam = params.get("token"); // optional shared read token (unused yet)

  $("btn-close").onclick = () => {
    if (window.liff && liff.isInClient && liff.isInClient()) liff.closeWindow();
    else history.back();
  };

  function showErr(msg) {
    $("err").textContent = msg;
    $("err").classList.remove("hidden");
  }

  function statusLabel(s) {
    if (s === "alert") return { t: "ควรไปดู", c: "status-alert" };
    if (s === "watch") return { t: "เฝ้าระวัง", c: "status-watch" };
    if (s === "normal") return { t: "ปกติ", c: "status-ok" };
    return { t: s || "—", c: "" };
  }

  function thaiDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso + "T00:00:00");
    const m = ["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."];
    return d.getDate() + " " + m[d.getMonth()];
  }

  async function idToken() {
    if (!cfg.liffId) return null;
    await liff.init({ liffId: cfg.liffId });
    if (!liff.isLoggedIn()) {
      liff.login({ redirectUri: location.href });
      return null;
    }
    return liff.getIDToken();
  }

  async function load() {
    if (!plotId) {
      showErr("ไม่พบรหัสแปลง — เปิดจากปุ่มในแชท หรือใส่ ?plot_id=");
      return;
    }
    const headers = {};
    try {
      const tok = await idToken();
      if (tok) headers.Authorization = "Bearer " + tok;
    } catch (e) {
      /* public plot_id URL still works for owners who share a link without LIFF */
    }
    const r = await fetch("/api/plots/" + encodeURIComponent(plotId) + "/chart", { headers });
    if (r.status === 401) {
      showErr("กรุณาเปิดจาก LINE เพื่อยืนยันตัวตน");
      return;
    }
    if (r.status === 403 || r.status === 404) {
      showErr("ไม่พบแปลงนี้ หรือคุณไม่มีสิทธิ์ดู");
      return;
    }
    if (!r.ok) {
      showErr("โหลดข้อมูลไม่สำเร็จ (" + r.status + ")");
      return;
    }
    const data = await r.json();
    render(data);
  }

  function render(data) {
    $("main").classList.remove("hidden");
    const p = data.plot || {};
    $("title").textContent = p.name || ("แปลง #" + plotId);
    const area = p.area_rai != null ? " · ≈ " + p.area_rai.toFixed(0) + " ไร่" : "";
    $("subtitle").textContent = "ความเขียวของอ้อย (จากภาพดาวเทียม)" + area;

    const series = data.series || [];
    if (!series.length) {
      showErr("ยังไม่มีภาพปลอดเมฆสำหรับแปลงนี้ — ระบบกำลังดึงภาพย้อนหลัง");
      return;
    }
    const last = series[series.length - 1];
    $("s-latest").textContent = last.ndvi.toFixed(2);
    const st = statusLabel(last.status);
    $("s-status").textContent = st.t;
    $("s-status").className = st.c;
    $("s-date").textContent = thaiDate(last.date);

    const labels = series.map((x) => x.date);
    const mine = series.map((x) => x.ndvi);
    const nb = series.map((x) => x.neighbour);
    // prior year: match by month-day from series points one year earlier if present in raw
    const byDate = Object.fromEntries(series.map((x) => [x.date, x]));
    const prior = series.map((x) => {
      const d = new Date(x.date + "T00:00:00");
      d.setFullYear(d.getFullYear() - 1);
      const key = d.toISOString().slice(0, 10);
      // look up in full series (same array only has current years already selected by API)
      const hit = data.prior_year && data.prior_year.find((p) => p.date.slice(5) === x.date.slice(5));
      return hit ? hit.ndvi : null;
    });

    new Chart($("chart"), {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "แปลงของคุณ", data: mine, borderColor: "#c62828", backgroundColor: "transparent", tension: 0.25, pointRadius: 2, borderWidth: 2 },
          { label: "เพื่อนบ้าน", data: nb, borderColor: "#2e7d32", backgroundColor: "transparent", tension: 0.25, pointRadius: 0, borderWidth: 2 },
          { label: "ปีที่แล้ว", data: data.prior_year_aligned || prior, borderColor: "#9e9e9e", backgroundColor: "transparent", tension: 0.25, pointRadius: 0, borderWidth: 2, borderDash: [4, 4] },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          y: { min: 0.2, max: 0.9, title: { display: true, text: "ความเขียว" } },
          x: { ticks: { maxTicksLimit: 8, callback: function(v) { return thaiDate(this.getLabelForValue(v)); } } },
        },
      },
    });
  }

  load();
})();
