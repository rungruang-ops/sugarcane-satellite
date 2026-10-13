# LINE webhook — บอท "เบิ่งไฮ่" (BerngHai)

LINE OA **@755zfojh** (เบิ่งไฮ่) · Messaging API channel `2011859231` · LINE Login channel
`2011859249` (no LIFF app yet) · plan: Free (300 push/month).

## ทำอะไรได้บ้าง / What it does

| Event | Behaviour |
|---|---|
| `follow` | ตอบข้อความต้อนรับ (reply) อธิบายว่าเบิ่งไฮ่ทำอะไร + "การลงทะเบียนแปลงจะเปิดเร็ว ๆ นี้"; บันทึก `users` (LINE userId, display name จาก profile API, `followed_at`, `is_active=true`) |
| `unfollow` | `users.is_active=false`, `unfollowed_at` (ไม่มี reply token) |
| text | keyword → คำตอบสั้น + quick reply: `เมนู`/`help`/`วิธีใช้`, `แปลงของฉัน`, `เทียบเพื่อนบ้าน`, `ฝน`, `เพิ่มแปลง`, `หัวหน้ากลุ่ม`; อื่น ๆ → fallback |
| postback `action=feedback&alert_id=N&answer=…` | บันทึก `alerts.feedback` (`borer`/`white_leaf`/`weed`/`water_stress`/`no_problem`), `feedback_at`, `feedback_user_id` แล้วตอบขอบคุณ |
| postback `action=rain_report&answer=none\|little\|enough` | ตอบขอบคุณ (ยังไม่เก็บ — ตาราง weather มาใน milestone ฝน) |

* **Reply only.** The webhook never sends push / multicast / broadcast (`canesat.line.client`
  has no such method). Alerts are push messages and belong to the future `notify` job, which must
  enforce the 300/month quota.
* If PostGIS is unreachable the bot still replies; DB writes are skipped and retried after 30 s.
* `canesat.line.messages.build_greenness_alert_flex()` builds the 🔴 alert Flex message from
  mockup 01 (not sent anywhere yet). All reply messages and the Flex alert pass LINE's
  `/v2/bot/message/validate/reply`.
* Migration `0002_line_users.sql` adds `users.followed_at / unfollowed_at / is_active` and
  `alerts.feedback_user_id` (`canesat db migrate`).

## Run

```bash
pip install -e ".[dev]"
canesat db migrate                       # optional, DATABASE_URL
# secrets: a JSON file containing LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN (any nesting).
# They are loaded into the server process env only — never put them in the repo or on argv.
python scripts/run_webhook.py --secrets-json /path/to/secrets.json --port 8711
curl localhost:8711/healthz              # {"status":"ok","db":"ok|unavailable|disabled"}
```

Expose it with any HTTPS tunnel, then point LINE at it:

```bash
python scripts/line_webhook_endpoint.py --secrets-json S.json set https://<public>/callback
python scripts/line_webhook_endpoint.py --secrets-json S.json test
python scripts/line_webhook_endpoint.py --secrets-json S.json get   # "active" = Use webhook toggle
```

> ⚠️ The tunnel used during development is **temporary** (quick tunnel / guest relay). Production
> needs a fixed HTTPS domain (design §9: one VM + FastAPI container).

## Rich Menu (not created yet)

`canesat.line.richmenu.rich_menu_spec()` — 2500×1686, 3×2 tiles as in mockup 01:
➕ เพิ่มแปลง · 🗺️ แปลงของฉัน · 📊 เทียบเพื่อนบ้าน · 🌧️ ฝน/ความชื้นดิน · 👥 หัวหน้ากลุ่ม · ❓ วิธีใช้/ติดต่อ.
Until LIFF exists every tile sends the matching keyword. Manual step:

```bash
python scripts/create_rich_menu.py                       # print JSON only
python scripts/create_rich_menu.py --apply --image menu.png --secrets-json S.json
```

## Open items
* Feedback postbacks are not yet restricted to the plot owner / group leader.
* Field photos (image messages) are acknowledged but not stored.
* Notify job (push, quota counter, quiet hours 06:00–19:00), LIFF pages, consent flows.
