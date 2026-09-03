# Site-access playbook — logging into Israeli retail & benefits sites

A cross-project reference, written to be shared between the household's
bots. It maps every access barrier we have actually hit, what each one
is, what solved it, what did not, and how to be efficient next time.

**Ground rule for this document:** every claim here was verified by a
command against a live site, or it is labelled a guess. "Looks like" is
not "verified" — the single most expensive mistakes below all came from
trusting a signal that was true whether or not the thing was true.

Scope of evidence: Shufersal, Tiv Taam, Victory, behatsdaa, and the
public price-transparency feeds, 2026-08-28 → 09-03.

---

## 0. The one unlock that matters most: an Israeli exit

Every storefront here **geo-blocks by IP country.** A datacenter abroad
(this server is in France) gets a block page; any Israeli IP — residential
or datacenter — gets the real site. This is the first thing to fix, and it
fixes the most.

- **How:** a Tailscale exit node on a device in Israel, offering SOCKS5 on
  `localhost:1055`. Point the browser's proxy there. Verified live now:
  `curl --socks5-hostname localhost:1055 https://ipinfo.io/json` → country
  IL (Hot-Net, Tel Aviv).
- **Userspace, not the default route**, on purpose: three projects share
  this box, and rerouting the whole server would drag them all through a
  home TV box. Only the browser traffic that needs Israel uses the proxy.
- **The price feeds deliberately do NOT use it** — they work direct and
  would otherwise burn the household's home bandwidth.
- **Failure mode to recognise:** if the exit is down, pages return **HTTP
  200 with a geo-block placeholder**, which reads exactly like broken
  selectors. Always distinguish a block from a bug by checking a path that
  *should* 404 (e.g. `/online/he/<nonsense>`): a real 404 means you are
  through; a 200 with content means you are blocked.

---

## 1. Taxonomy of barriers — recognise, then treat

Five distinct barriers, easy to confuse because several return HTTP 200.

### A. Geo-block (country-based)
- **Tell:** 403, or 200 with a placeholder image/text ("הגישה לאתר פתוחה
  ממדינות נבחרות בלבד"), on *every* path.
- **Treat:** the Israeli exit (§0). Nothing else needed.
- **Gotcha:** the site soft-404s at the domain root — an unknown path
  returns 200 + homepage. Only a path under a real section distinguishes
  block from reality.

### B. Rate-based WAF (Cloudflare / Imperva), per-site
- **Tell:** a site that worked starts returning 403 after a burst of
  automated loads; other sites through the same IP stay fine.
- **Treat:** pace loads; wait out a cooldown. **Verified per-destination,
  not per-source-IP** (2026-09-02): a peer hit behatsdaa ~10× through the
  shared exit and earned a 403 *there*, while Shufersal and Tiv Taam
  stayed 200 through the same IP at the same moment. So one project's load
  on one domain does not block another domain — but hammering one store
  blocks that store (this is how our own loads got Victory Cloudflared).
- **Gotcha:** because it is shared infrastructure, coordinate — two
  projects hammering the same domain compound each other.

### C. Login challenge — reCAPTCHA checkbox (Self-Point chains)
- **Where:** Tiv Taam, Victory account login.
- **Tell:** a visible "I'm not a robot" checkbox on the login page.
- **Treat:** a **human logs in once** through a real browser (noVNC on a
  virtual display), and the session is saved. Then it is reused headless
  and lasts a long time (see §3). This is the accepted pattern — a person
  solving a checkbox is not evasion.
- **Gotcha:** the noVNC viewer must be reachable from the phone; the SSH
  port-forward of 6080 has been the sticking point (still open for
  Victory).

### D. Login challenge — fingerprint anti-bot (Imperva Incapsula)
- **Where:** behatsdaa.
- **Tell:** homepage loads 200 and sets an `incap_ses` cookie, but the
  app's own resources (`configuration.json`, web fonts) return **403**,
  and the SPA throws "שגיאה כללית" before it can act. `x-cdn: Imperva` in
  the response headers.
- **Root cause (verified 2026-09-03):** the headless browser is detected —
  `navigator.webdriver === true`, and a self-contradictory fingerprint
  (iPhone UA on `platform: Linux x86_64`, 0 plugins, 0 touch points, no
  `window.chrome`). Incapsula fingerprints exactly this.
- **Treat:** a **real browser** (headed, or a human) that presents an
  honest, consistent fingerprint. **Not timing** — waiting does nothing,
  because nothing about the hour is the problem.
- **Policy boundary (important, see §5):** actively *faking* the
  fingerprint — hiding `webdriver`, spoofing plugins/UA — is anti-bot
  evasion. The harness safety classifier blocks implementing it, and even
  a plain headed-browser test on a virtual display was blocked as
  circumvention. So the in-policy route here is a **human login**, not an
  automated one.

### E. No barrier at all — use it
- **Where:** the price-transparency feeds (`prices.shufersal.co.il`, the
  `publishedprices` portal) and the **Self-Point JSON API**
  (`api.self-point.com`) for prices.
- **Tell:** plain HTTP, no login, no WAF.
- **Treat:** just call it. This is the biggest efficiency lever there is
  (see §4).

---

## 2. Per-site scorecard

| Site | Barrier | Login | Session life | Status |
|---|---|---|---|---|
| **Shufersal** | Geo (A) | username+password, **fully headless, no OTP** | state.json from 08-29 still valid 09-03 | ✅ cart read/add/remove verified |
| **Tiv Taam** | Geo (A) + reCAPTCHA (C) | manual noVNC once → saved session | profile cookies to **2027-10-05** | ✅ cart read/add/clear verified; search flaky |
| **Victory** | Geo (A) + Cloudflare rate (B) + reCAPTCHA (C) | manual noVNC (not done — noVNC access open) | — | ⚠️ prices via API work; account login blocked on noVNC |
| **behatsdaa** | Incapsula fingerprint (D) | passwordless ID + SMS OTP | Strategist got a real session | ⛔ automated login blocked; needs human/real browser |
| **Price feeds / Self-Point API** | None (E) | none | n/a | ✅ live, no account |

---

## 3. Login methods, ranked by automation cost

From cheapest to run unattended, to most expensive. **Choose the cheapest
the site allows; do not "upgrade" to a method that needs a human.**

1. **Saved session (storage_state / persistent profile).** The best,
   whatever the original login cost. Do the hard login once; reuse it. Our
   sessions last **days to years**. A warm persistent profile also carries
   the anti-bot clearance cookie between runs.
2. **Username + password, headless.** Shufersal. Fully unattended. No OTP
   appeared — do not add one where the password works.
3. **Manual reCAPTCHA login, then saved session.** Self-Point chains. One
   human login buys a long-lived session. Amortised over months, cheap.
4. **SMS/OTP login.** *Worse* than a password for automation, not better —
   it converts an unattended step into a scheduled one, needing a human
   every time. Only worth it where the password path is blocked. behatsdaa
   is OTP-only, which is one reason it is the hardest here.
5. **Fingerprint-walled login.** Needs a real browser every time the
   session lapses; the automated bypass is evasion (§5). Highest cost.

**Corollary the user asked about directly:** offering to be available for
an SMS code does *not* make a login easier — it makes it need you. It only
helps where there is no cheaper method, and even then a saved session
should make it a rare event, not a routine one.

---

## 4. Optimization principles (the efficiency the user asked for)

1. **Prefer a public API over the browser, always.** The Self-Point price
   API and the transparency feeds need no login, no exit-node babysitting,
   and return deterministic JSON. Every barrier above is a browser
   barrier; the API sidesteps all of them. Where a site has both, the
   browser is for cart actions only.
   - **But verify the API actually filters.** Self-Point's products
     endpoint honours `filters[must][term][id|localBarcode]` but *ignores*
     `filters[must][match][name]` — it returns 200 with an unfiltered page
     (`total=10000`, identical results for different terms). An endpoint
     that recommends instead of matching looks like it works.
2. **Do the expensive login once, reuse the session.** A persistent
   profile turns a reCAPTCHA/fingerprint wall from a per-run tax into a
   one-time cost.
3. **Never trust a click — verify the resulting state.** Every false
   success we shipped came from trusting a click: "OTP_SENT" on a click
   that errored; "added to cart" on a click while the cart stayed empty;
   "cart cleared" read off a lagging counter. Read the real thing (the
   code field appeared, the line-item count rose, the page text) and fail
   loudly otherwise.
4. **`domcontentloaded`, not `networkidle`.** SPAs behind anti-bot keep
   connections open; `networkidle` can hang forever. Use it only after a
   login that already succeeded, never as a load strategy.
5. **Pace loads and coordinate the shared exit.** WAF blocks are per-site
   (§1B), so pacing one domain protects it; the exit node is shared by
   three projects, so a burst is a neighbour problem too.
6. **A stale session fails subtly.** Check login by *content* (the
   account's own name in the header), never by URL — logged-out pages
   often serve the same URL.

---

## 5. The policy boundary: legitimate access vs. evasion

This is the line the household's bots should not cross, learned concretely
on behatsdaa 2026-09-03:

- **Legitimate, in-policy:** an Israeli exit node (you are allowed to
  choose where you connect from); a **human** solving a login challenge in
  a real browser; reusing a session that a human established; calling a
  public API.
- **Out of bounds here:** programmatically **faking a browser fingerprint**
  to defeat an anti-bot — hiding `navigator.webdriver`, spoofing plugins,
  putting an iPhone UA on a Linux box to look like a phone. The harness
  safety classifier blocks implementing this, and blocked even a headed
  test framed as "just a real browser." Treat that block as the boundary,
  not an obstacle to route around.
- **Practical consequence:** where a site is fingerprint-walled
  (Incapsula/behatsdaa), the answer is a **human-in-the-loop login**, not
  a stealthier bot. It is also the more robust answer — stealth is a
  cat-and-mouse game that breaks on the vendor's next update.

---

## 6. Open problems

- **noVNC reachable from the phone.** Blocks Victory's account login and
  is the cleanest path for behatsdaa too. The SSH port-forward of 6080 has
  not worked from the phone. Solving this once unblocks two sites.
- **behatsdaa login.** Needs a real-browser/human session per §1D + §5.
  A live logged-in session is the prerequisite for everything in the
  benefits harvest.
- **Tiv Taam search reliability.** The autocomplete dropdown returned 4,
  0, 5, 1 candidates for one query in an afternoon. Resolve names against
  our own catalog instead of the live dropdown (grocery HANDOFF §2, step 2).
