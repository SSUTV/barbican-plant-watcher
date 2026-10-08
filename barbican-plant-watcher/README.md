# Barbican plant adoption watcher (2 Dec)

Checks the Barbican Conservatory Plant Adoption page every ~45 seconds, around the clock, and alerts Stefan and Hannah by **email + phone push** when tickets for 2 December go live.

| What happens | Alert |
|---|---|
| Bookable time slots appear (the page's booking-button feed stops being empty) | **Urgent** push + email every 2 min for 20 min. Then the bot switches itself off. |
| "Not yet on sale" disappears, an on-sale date or "book now" appears, or a new plant-adoption page turns up | One **urgent** push + email. The bot keeps watching for real slots. |
| Any other change to the event text | One push + email showing what changed. |
| Bot can't read the page for ~6 min | Push + email. Another push when it recovers. |
| Every morning around 8am | Quiet "still watching" push. |

The bot stops by itself after 2 December.

---

## Setup (about 10 minutes)

### 1. Phone push (both of you)
1. Install **ntfy** ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. Tap **+** and subscribe to the topic **`barbican-plants-3d2b14045101`** (server: ntfy.sh).
3. In the app, allow notifications. On iPhone, also allow it to break through Focus / Do Not Disturb (Settings → Notifications → ntfy → Time Sensitive).

The topic name is the only secret here. Anyone who knows it can read or send to it, so don't post it publicly.

### 2. Gmail app password (for sending the email)
1. Stefan's Google account must have **2-Step Verification** on.
2. Go to <https://myaccount.google.com/apppasswords> and create one named "Plant watcher". Copy the 16-character password.
   - If that page says app passwords aren't available, the Workspace admin has to allow them (Admin console → Security → Authentication → 2-Step Verification).

### 3. GitHub repo
1. Create a new **public** repo, e.g. `barbican-plant-watcher`.
   - It has to be public: private repos get 2,000 free Actions minutes a month, and this bot uses about 1,440 a day. The code holds no secrets. Your emails, password and push topic go into encrypted GitHub Secrets, which nobody else can see.
2. Upload this folder's contents, keeping the `.github/workflows/watch.yml` path. (The web uploader works: drag the folder in with "Add file → Upload files".)
3. **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|---|---|
| `SMTP_USER` | `stefan@meterz.co.uk` |
| `SMTP_APP_PASSWORD` | the 16-character app password |
| `EMAIL_TO` | `stefan@meterz.co.uk,hannah@meterz.co.uk` |
| `NTFY_TOPIC` | `barbican-plants-3d2b14045101` |

4. **Actions** tab → enable workflows if asked.

### 4. Test it, then start it
1. Actions → **Plant adoption watcher** → **Run workflow** → mode **test**. Within a minute you should **both** get a test push and a test email. Check spam and mark it "not spam".
2. Run it again with mode **watch** to start straight away. From then on, the schedule keeps it running.
3. Open the running job's log. You should see `poll N: not on sale yet` every ~45s. If you see `FAILED` lines instead, Barbican may be blocking GitHub's servers, so let me know.

---

## Good to know
- **Speed:** worst case is ~50s from Barbican publishing to your phone buzzing. Email adds a few seconds. Push is the fast channel.
- **Handover:** each run lasts ~5h45m and the next one is already queued, so there's no gap. You'll see some runs marked "cancelled" in the Actions list. That's normal: GitHub drops duplicate queued runs.
- **Pause / stop:** Actions → Plant adoption watcher → ⋯ → Disable workflow.
- **Restart after an alert:** the bot disables itself once it has nagged. To watch again, delete the cache (Actions → Caches → delete `watcher-state-*`) and re-enable the workflow.
- **Booking:** the bot only alerts. One of you still books, which needs a Barbican online account, so set one up now and stay logged in on your phones.
