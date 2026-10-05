(function () {
  const cfg = JSON.parse(document.getElementById("canesat-config").textContent || "{}");
  const token = new URLSearchParams(location.search).get("token");
  const $ = (id) => document.getElementById(id);
  if (!token) { $("msg").textContent = "ลิงก์ไม่ครบ — ขอลิงก์ใหม่จากหัวหน้ากลุ่ม"; return; }
  $("ok").onchange = () => { $("btn").disabled = !$("ok").checked; };
  $("btn").onclick = async () => {
    $("err").classList.add("hidden");
    try {
      await liff.init({ liffId: cfg.liffId });
      if (!liff.isLoggedIn()) { liff.login({ redirectUri: location.href }); return; }
      const r = await fetch("/api/consents/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + liff.getIDToken() },
        body: JSON.stringify({ token }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) { $("err").textContent = body.detail || "ยืนยันไม่สำเร็จ"; $("err").classList.remove("hidden"); return; }
      $("msg").textContent = "✅ บันทึกความยินยอมแล้ว ขอบคุณครับ";
      $("btn").disabled = true;
      $("ok").disabled = true;
    } catch (e) {
      $("err").textContent = "เกิดข้อผิดพลาด — ลองใหม่";
      $("err").classList.remove("hidden");
    }
  };
  $("msg").textContent = "อ่านแล้วติ๊กยินยอม แล้วกดยืนยัน (ต้องเปิดจาก LINE ของเจ้าของแปลง)";
})();
