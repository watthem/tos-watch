/**
 * tos.watch signup + search Worker.
 *
 * Endpoints:
 *   POST /subscribe          {email, source?, tracks?[]}  -> store pending, idempotent
 *   GET  /confirm?token=...  -> pending -> confirmed (also promotes any pending votes)
 *   GET  /unsubscribe?token=... -> any -> unsubscribed
 *   POST /request  {service?, url?, email?}  -> log interest in an untracked/unknown
 *                  service; dedupe by normalized service/url; optional email is a
 *                  verified interest signal (see /vote), not a raw count
 *   POST /vote     {feature, email}  -> a verified +1 on a feature request (e.g. the
 *                  mock on-demand check), gated on a confirmed email
 *   GET  /check?url=...  -> MOCK on-demand check: never fetches the given url, only
 *                  looks it up against the bundled tracked/archived service list
 *
 * No endpoint lists or exports subscriber emails, requests, or votes.
 *
 * Email provider: Buttondown (docs.buttondown.com), per the owner's decision
 * 2026-09-26. D1 is our own copy of the list; Buttondown owns the actual
 * double opt-in confirmation email and its own one-click unsubscribe link
 * (docs.buttondown.com/double-opt-in) - that is the ONE side that ever sends
 * real mail. Our own sendConfirmation() below is intentionally never wired
 * to a real sender, so there is no risk of a subscriber getting two
 * confirmation emails. Our own /confirm and /unsubscribe token routes still
 * exist and are tested independently, as our own record of the list; they
 * do not depend on Buttondown being configured.
 *
 * Without the BUTTONDOWN_API_KEY secret (`wrangler secret put
 * BUTTONDOWN_API_KEY`, see README), buttondownSubscribe/buttondownUnsubscribe
 * log and no-op, exactly like sendConfirmation does.
 *
 * /request and /vote share one mechanism (added 2026-09-26, owner chat: "we
 * can actually have an endpoint to mock that and +1 a feature request by
 * attaching a registered verified email"): recordFeatureInterest() below.
 * A confirmed email counts immediately; an unknown or still-pending email
 * creates/reuses a pending subscriber (double opt-in) and the vote only
 * counts once they confirm -- handleConfirm() promotes any pending votes
 * for that email at the same time it confirms the subscriber. Anonymous
 * /request calls (no email) only ever bump the separate unverified_count on
 * the requests row; that number is never conflated with a verified vote.
 */

import SERVICES from "./services.json";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const BUTTONDOWN_API_BASE = "https://api.buttondown.com/v1";
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 5;
const MAX_SERVICE_LEN = 200;
const MAX_FEATURE_LEN = 128;
const MAX_URL_LEN = 2000;

// Track ids, kept in sync by hand with pipeline/watchlist.json's tracks[]
// (5 tracks added 2026-09-26; see README for the source of truth). A reader
// who omits tracks[] on /subscribe gets all of them; an unknown id is
// dropped rather than stored raw.
const VALID_TRACKS = ["ai-assistants", "dating", "typing", "dev-tools", "consumer"];

// Best-effort, per-isolate rate limiting. This resets whenever the Worker
// isolate recycles; acceptable for a low-volume signup form and avoids
// storing visitor IPs anywhere persistent. A real deployment with sustained
// abuse traffic would move this to Durable Objects.
const hits = new Map();

function rateLimited(ip) {
  const now = Date.now();
  const arr = (hits.get(ip) || []).filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
  arr.push(now);
  hits.set(ip, arr);
  return arr.length > RATE_LIMIT_MAX;
}

/** Test-only: the in-memory limiter is module state, so tests that run many
 * requests back-to-back in one isolate must reset it between cases. Not
 * called from production code paths. */
export function _resetRateLimitForTests() {
  hits.clear();
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders() },
  });
}

function text(body, status = 200) {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", ...corsHeaders() },
  });
}

function randomToken() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** name/service string -> url- and key-safe slug, e.g. "  Hinge  " -> "hinge". */
function slugify(name) {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Loose fuzzy-match key: lowercase, alphanumeric only. Mirrors
 * datasets/build_services_catalog.py's match_key() so a vendor name and a
 * URL fragment can be compared. */
function matchKey(s) {
  return s.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** Dedupe key for a requested URL: strip scheme, "www.", and any trailing
 * slash, so "https://example.com/legal/" and "https://www.example.com/legal"
 * collide on the same request row. Returns null for an unparseable/
 * non-http(s) URL. */
function normalizeUrlForDedupe(rawUrl) {
  let u;
  try {
    u = new URL(rawUrl);
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  let host = u.hostname.toLowerCase();
  if (host.startsWith("www.")) host = host.slice(4);
  const path = u.pathname.replace(/\/+$/, "");
  return `${host}${path}`;
}

/** Registrable-domain-ish match key for GET /check: "https://www.grammarly.
 * com/legal" -> "grammarly". Never fetches anything -- pure string parsing
 * against the statically bundled services.json. */
function domainMatchKey(rawUrl) {
  let u;
  try {
    u = new URL(rawUrl.includes("://") ? rawUrl : `https://${rawUrl}`);
  } catch {
    return null;
  }
  let host = u.hostname.toLowerCase();
  if (!host) return null;
  if (host.startsWith("www.")) host = host.slice(4);
  const parts = host.split(".").filter(Boolean);
  if (parts.length === 0) return null;
  const sld = parts.length >= 2 ? parts[parts.length - 2] : parts[0];
  return matchKey(sld);
}

// Built once per isolate from the bundled catalog (datasets/build_services_
// catalog.py); GET /check only ever reads this, never the network.
const SERVICES_BY_KEY = new Map();
for (const s of SERVICES.services || []) {
  const key = matchKey(s.name);
  if (key && !SERVICES_BY_KEY.has(key)) SERVICES_BY_KEY.set(key, s);
}

/** Sending is not in scope this weekend; log only. Exported for tests. */
export async function sendConfirmation(email, link) {
  console.log(`[tos.watch] would send confirmation to ${email}: ${link}`);
}

/**
 * Create/upsert the subscriber in Buttondown (default double opt-in: they
 * get type "unactivated" and Buttondown emails them to confirm). Honest
 * no-op when the secret isn't set yet. Best-effort: never throws, never
 * blocks the visitor-facing response on a downstream failure.
 * https://docs.buttondown.com/api-subscribers-create
 */
/** Returns what happened, for the signup form to show:
 *  "sent"    Buttondown accepted the address and emails its own confirmation
 *            (or already knows it; we don't reveal which),
 *  "blocked" Buttondown's firewall rejected it,
 *  "failed"  anything else went wrong,
 *  "skipped" no API key (local dev and tests). */
export async function buttondownSubscribe(env, email, source, ip) {
  if (!env.BUTTONDOWN_API_KEY) {
    console.log(`[tos.watch] BUTTONDOWN_API_KEY not set; not yet syncing ${email} to Buttondown`);
    return "skipped";
  }
  try {
    const body = { email_address: email, tags: source ? [source] : undefined };
    if (ip && ip !== "unknown") body.ip_address = ip;
    const res = await fetch(`${BUTTONDOWN_API_BASE}/subscribers`, {
      method: "POST",
      headers: {
        Authorization: `Token ${env.BUTTONDOWN_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (res.ok) return "sent";
    // Log Buttondown's error code (e.g. email_already_exists vs. a firewall
    // rejection) without the address itself.
    const detail = (await res.text()).slice(0, 300);
    console.log(`[tos.watch] Buttondown subscribe ${res.status}: ${detail}`);
    let code = "";
    try { code = JSON.parse(detail).code || ""; } catch {}
    if (code === "email_already_exists") return "sent";
    if (code === "subscriber_blocked") return "blocked";
    return "failed";
  } catch (err) {
    console.log(`[tos.watch] Buttondown subscribe error: ${err}`);
    return "failed";
  }
}

/**
 * Mirror an unsubscribe from our own token flow into Buttondown, so someone
 * who unsubscribes via our link stops receiving Buttondown mail too.
 * https://docs.buttondown.com/api-subscribers-update
 */
export async function buttondownUnsubscribe(env, email) {
  if (!env.BUTTONDOWN_API_KEY) {
    console.log(`[tos.watch] BUTTONDOWN_API_KEY not set; not yet syncing unsubscribe for ${email}`);
    return;
  }
  try {
    const res = await fetch(`${BUTTONDOWN_API_BASE}/subscribers/${encodeURIComponent(email)}`, {
      method: "PATCH",
      headers: {
        Authorization: `Token ${env.BUTTONDOWN_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ type: "unsubscribed" }),
    });
    if (!res.ok) {
      console.log(`[tos.watch] Buttondown unsubscribe failed (${res.status}) for ${email}`);
    }
  } catch (err) {
    console.log(`[tos.watch] Buttondown unsubscribe error: ${err}`);
  }
}

/** tracks[] from a /subscribe body -> a validated, deduped array. Unknown
 * ids are dropped, never stored raw; an empty/omitted list defaults to all
 * known tracks (checkbox UI ships with everything checked). */
function normalizeTracks(input) {
  if (!Array.isArray(input)) return VALID_TRACKS.slice();
  const valid = [...new Set(input.filter((t) => typeof t === "string" && VALID_TRACKS.includes(t)))];
  return valid.length ? valid : VALID_TRACKS.slice();
}

/**
 * Shared /request + /vote mechanism (2026-09-26): a verified +1 on a
 * feature/service-request key. `feature` is the votes.feature value counted
 * (e.g. "on-demand-check" or "requested:service-hinge"). `sourceTag`
 * (defaults to `feature`) is only used to tag a *brand-new* subscriber row;
 * an already-known email's existing source/status is never overwritten.
 *
 * A confirmed subscriber's vote is recorded as confirmed immediately. A new
 * or still-pending subscriber's vote is recorded as pending; handleConfirm()
 * promotes it to confirmed when that email confirms. Never reveals to the
 * caller which case applied.
 */
async function recordFeatureInterest(env, feature, email, ip, origin, sourceTag = feature) {
  const existing = await env.DB.prepare("SELECT status FROM subscribers WHERE email = ?")
    .bind(email)
    .first();

  let subscriberStatus = existing ? existing.status : null;

  if (!existing) {
    const token = randomToken();
    const now = new Date().toISOString();
    await env.DB.prepare(
      "INSERT INTO subscribers (email, status, token, source, created_at) VALUES (?, 'pending', ?, ?, ?)"
    )
      .bind(email, token, sourceTag, now)
      .run();
    subscriberStatus = "pending";
    const confirmLink = `${origin}/confirm?token=${token}`;
    await sendConfirmation(email, confirmLink);
    await buttondownSubscribe(env, email, sourceTag, ip);
  }

  const now = new Date().toISOString();
  if (subscriberStatus === "confirmed") {
    await env.DB.prepare(
      "INSERT OR IGNORE INTO votes (feature, email, status, created_at, confirmed_at) VALUES (?, ?, 'confirmed', ?, ?)"
    )
      .bind(feature, email, now, now)
      .run();
  } else {
    await env.DB.prepare(
      "INSERT OR IGNORE INTO votes (feature, email, status, created_at) VALUES (?, ?, 'pending', ?)"
    )
      .bind(feature, email, now)
      .run();
  }
}

async function handleSubscribe(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (rateLimited(ip)) {
    return json({ ok: false, error: "rate_limited" }, 429);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "invalid_body" }, 400);
  }

  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!email || email.length > 320 || !EMAIL_RE.test(email)) {
    return json({ ok: false, error: "invalid_email" }, 400);
  }
  const source = typeof body.source === "string" ? body.source.slice(0, 64) : "site";
  const tracks = normalizeTracks(body.tracks);

  const existing = await env.DB.prepare(
    "SELECT email, status, token FROM subscribers WHERE email = ?"
  )
    .bind(email)
    .first();

  if (existing) {
    // Idempotent: repeat signups don't reset an already-pending or
    // already-confirmed subscriber, and don't leak which case it was. We still
    // ask Buttondown, so a signup its firewall blocked earlier can go through
    // on a retry.
    return subscribeResult(await buttondownSubscribe(env, email, source, ip));
  }

  const token = randomToken();
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO subscribers (email, status, token, source, tracks, created_at) VALUES (?, 'pending', ?, ?, ?, ?)"
  )
    .bind(email, token, source, tracks.join(","), now)
    .run();

  const confirmLink = `${new URL(request.url).origin}/confirm?token=${token}`;
  await sendConfirmation(email, confirmLink);
  return subscribeResult(await buttondownSubscribe(env, email, source, ip));
}

// The D1 row is kept in every case, so a blocked or failed signup can be
// added by hand later.
function subscribeResult(outcome) {
  if (outcome === "blocked") return json({ ok: false, error: "blocked" }, 422);
  if (outcome === "failed") return json({ ok: true, next: "saved" });
  return json({ ok: true, next: "confirm" });
}

async function handleConfirm(request, env) {
  const token = new URL(request.url).searchParams.get("token") || "";
  if (!token) return text("Missing or invalid confirmation link.", 400);

  const row = await env.DB.prepare("SELECT email, status FROM subscribers WHERE token = ?")
    .bind(token)
    .first();
  if (!row) return text("Missing or invalid confirmation link.", 400);

  if (row.status === "pending") {
    const now = new Date().toISOString();
    await env.DB.prepare(
      "UPDATE subscribers SET status = 'confirmed', confirmed_at = ? WHERE token = ?"
    )
      .bind(now, token)
      .run();
    // Promote any votes this email cast while still pending (2026-09-26:
    // a vote only counts once the voter's email is verified).
    await env.DB.prepare(
      "UPDATE votes SET status = 'confirmed', confirmed_at = ? WHERE email = ? AND status = 'pending'"
    )
      .bind(now, row.email)
      .run();
  }
  return text("You're confirmed. Thanks for subscribing to tos.watch.");
}

async function handleUnsubscribe(request, env) {
  const token = new URL(request.url).searchParams.get("token") || "";
  if (!token) return text("Missing or invalid unsubscribe link.", 400);

  const row = await env.DB.prepare("SELECT email FROM subscribers WHERE token = ?")
    .bind(token)
    .first();
  if (!row) return text("Missing or invalid unsubscribe link.", 400);

  await env.DB.prepare("UPDATE subscribers SET status = 'unsubscribed' WHERE token = ?")
    .bind(token)
    .run();
  await buttondownUnsubscribe(env, row.email);
  return text("You're unsubscribed. Sorry to see you go.");
}

/**
 * POST /request {service?, url?, email?} -- log interest in a service that
 * isn't tracked (or isn't known to us at all). Dedupes by a normalized key
 * so repeat requests increment a count instead of piling up rows. An
 * optional email is routed through recordFeatureInterest() as a verified
 * +1 tagged `requested:<slug>`, on the same confirmed-only-counts rule as
 * /vote; it does not add to the anonymous unverified_count.
 */
async function handleRequest(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (rateLimited(ip)) {
    return json({ ok: false, error: "rate_limited" }, 429);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "invalid_body" }, 400);
  }

  const rawService = typeof body.service === "string" ? body.service.trim() : "";
  const rawUrl = typeof body.url === "string" ? body.url.trim() : "";

  if (!rawService && !rawUrl) {
    return json({ ok: false, error: "missing_service_or_url" }, 400);
  }
  if (rawService && rawService.length > MAX_SERVICE_LEN) {
    return json({ ok: false, error: "invalid_service" }, 400);
  }
  if (rawUrl && rawUrl.length > MAX_URL_LEN) {
    return json({ ok: false, error: "invalid_url" }, 400);
  }

  let normalizedKey;
  let storedService = null;
  let storedUrl = null;

  if (rawService) {
    const slug = slugify(rawService);
    if (!slug) return json({ ok: false, error: "invalid_service" }, 400);
    normalizedKey = `service:${slug}`;
    storedService = rawService.slice(0, MAX_SERVICE_LEN);
  } else {
    const normalizedUrl = normalizeUrlForDedupe(rawUrl);
    if (!normalizedUrl) return json({ ok: false, error: "invalid_url" }, 400);
    normalizedKey = `url:${normalizedUrl}`;
    storedUrl = rawUrl.slice(0, MAX_URL_LEN);
  }

  const now = new Date().toISOString();
  const existing = await env.DB.prepare("SELECT id FROM requests WHERE normalized_key = ?")
    .bind(normalizedKey)
    .first();

  if (existing) {
    await env.DB.prepare(
      "UPDATE requests SET unverified_count = unverified_count + 1, updated_at = ? WHERE normalized_key = ?"
    )
      .bind(now, normalizedKey)
      .run();
  } else {
    await env.DB.prepare(
      "INSERT INTO requests (normalized_key, service, url, unverified_count, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)"
    )
      .bind(normalizedKey, storedService, storedUrl, now, now)
      .run();
  }

  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (email && email.length <= 320 && EMAIL_RE.test(email)) {
    const feature = `requested:${normalizedKey.replace(":", "-")}`;
    await recordFeatureInterest(env, feature, email, ip, new URL(request.url).origin);
  }

  return json({ ok: true });
}

/**
 * POST /vote {feature, email} -- a verified +1 on a feature request (e.g.
 * the mock on-demand check). Always returns the same generic response
 * regardless of whether the email was already known/confirmed/pending, so
 * the response itself never reveals subscriber status.
 */
async function handleVote(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (rateLimited(ip)) {
    return json({ ok: false, error: "rate_limited" }, 429);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "invalid_body" }, 400);
  }

  const feature = typeof body.feature === "string" ? body.feature.trim() : "";
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";

  if (!feature || feature.length > MAX_FEATURE_LEN || !email || email.length > 320 || !EMAIL_RE.test(email)) {
    return json({ ok: false, error: "invalid_vote" }, 400);
  }

  await recordFeatureInterest(
    env,
    feature,
    email,
    ip,
    new URL(request.url).origin,
    `vote:${feature}`
  );

  return json({ ok: true });
}

/**
 * GET /check?url=... -- MOCK on-demand check (owner, chat, 2026-09-26): never
 * fetches the given url or any url. It only looks up a normalized domain
 * fragment against the bundled site/services.json catalog (tracked +
 * archived-but-untracked, built by datasets/build_services_catalog.py). An
 * unknown site gets back the on-demand-check feature id so the page can
 * offer the +1 flow (POST /vote) instead of a dead end.
 */
async function handleCheck(request) {
  const target = new URL(request.url).searchParams.get("url") || "";
  if (!target) return json({ ok: false, error: "missing_url" }, 400);

  const key = domainMatchKey(target);
  if (!key) return json({ ok: false, error: "invalid_url" }, 400);

  const match = SERVICES_BY_KEY.get(key);
  if (match) {
    const payload = { status: match.status, slug: match.slug, name: match.name };
    if (match.tracks) payload.tracks = match.tracks;
    if (match.ota_history_url) payload.ota_history_url = match.ota_history_url;
    return json(payload);
  }

  return json({
    status: "unknown",
    feature: "on-demand-check",
    message: "On-demand checks are coming. Tell us your email and we'll let you know when they ship.",
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }
    if (request.method === "POST" && url.pathname === "/subscribe") {
      return handleSubscribe(request, env);
    }
    if (request.method === "GET" && url.pathname === "/confirm") {
      return handleConfirm(request, env);
    }
    if (request.method === "GET" && url.pathname === "/unsubscribe") {
      return handleUnsubscribe(request, env);
    }
    if (request.method === "POST" && url.pathname === "/request") {
      return handleRequest(request, env);
    }
    if (request.method === "POST" && url.pathname === "/vote") {
      return handleVote(request, env);
    }
    if (request.method === "GET" && url.pathname === "/check") {
      return handleCheck(request);
    }
    return text("Not found", 404);
  },
};
