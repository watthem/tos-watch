/**
 * tos.watch signup + search Worker.
 *
 * Endpoints:
 *   POST /subscribe  {email, source?, tracks?[]}  -> create the subscriber in Buttondown
 *   POST /request    {service?, url?, email?}  -> count interest in an untracked/unknown
 *                    service; dedupe by normalized service/url; an optional email
 *                    tags that subscriber requested:<key> in Buttondown
 *   POST /vote       {feature, email}  -> counts the vote in D1 (anonymous) and tags that
 *                    subscriber vote:<feature> in Buttondown; feature is one of VALID_FEATURES
 *   GET  /check?url=...  -> MOCK on-demand check: never fetches the given url, only
 *                    looks it up against the bundled tracked/archived service list
 *
 * Buttondown is the only place an email address lives (owner, 2026-09-28:
 * "remove the PII as early as possible from the services"). It sends the
 * double opt-in confirmation and owns unsubscribes. The signup source, topics,
 * votes and requests are tags on the Buttondown subscriber, so a vote counts
 * once that subscriber has confirmed (type "regular"), and unsubscribing or
 * deleting them in Buttondown removes everything. D1 keeps only anonymous
 * counts: per-service requests and per-feature votes. Nothing here logs an
 * email address.
 *
 * Without the BUTTONDOWN_API_KEY secret (`wrangler secret put
 * BUTTONDOWN_API_KEY`, see README) the Buttondown calls no-op and no email is
 * kept anywhere, which is what local dev and most tests see.
 */

import SERVICES from "./services.json";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const BUTTONDOWN_API_BASE = "https://api.buttondown.com/v1";
const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX = 5;
const MAX_SERVICE_LEN = 200;
const MAX_URL_LEN = 2000;

// Track ids, kept in sync by hand with pipeline/watchlist.json's tracks[]
// (5 tracks added 2026-09-26; see README for the source of truth). A form
// that sends tracks[] tags the subscriber track:<id>; an unknown id is
// dropped rather than passed on raw.
const VALID_TRACKS = ["ai-assistants", "dating", "typing", "dev-tools", "consumer"];

// Feature ids /vote accepts, each a demand signal the owner reads from D1
// feature_counts: the mock on-demand check, a paid supporter tier, and
// vendor watchlists for teams (owner, 2026-09-28: "we should collect signals").
const VALID_FEATURES = ["on-demand-check", "founding-supporter", "vendor-watch"];

// Buttondown subscriber types we must not touch: tagging them could
// resubscribe someone who left.
const BUTTONDOWN_SUPPRESSED = ["unsubscribed", "blocked", "complained", "undeliverable", "removed"];

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

function buttondownHeaders(env) {
  return { Authorization: `Token ${env.BUTTONDOWN_API_KEY}`, "Content-Type": "application/json" };
}

/**
 * Create the subscriber in Buttondown (default double opt-in: type
 * "unactivated" until they confirm Buttondown's own email). Never throws and
 * never logs the address. Returns what happened:
 *  "sent"    created; Buttondown emails its confirmation,
 *  "exists"  Buttondown already has this address,
 *  "blocked" Buttondown's firewall rejected it,
 *  "failed"  anything else went wrong,
 *  "skipped" no API key (local dev and tests).
 * https://docs.buttondown.com/api-subscribers-create (a tag that doesn't
 * exist yet needs a plan with tags; see the feature_disabled retry below)
 */
export async function buttondownCreate(env, email, tags, ip) {
  if (!env.BUTTONDOWN_API_KEY) {
    console.log("[tos.watch] BUTTONDOWN_API_KEY not set; subscriber not sent to Buttondown");
    return "skipped";
  }
  try {
    const body = { email_address: email };
    if (tags && tags.length) body.tags = tags;
    if (ip && ip !== "unknown") body.ip_address = ip;
    const res = await fetch(`${BUTTONDOWN_API_BASE}/subscribers`, {
      method: "POST",
      headers: buttondownHeaders(env),
      body: JSON.stringify(body),
    });
    if (res.ok) return "sent";
    // Buttondown's error bodies can echo the address, so log only the code.
    let code = "";
    try { code = JSON.parse(await res.text()).code || ""; } catch {}
    console.log(`[tos.watch] Buttondown subscribe ${res.status}: ${code || "no code"}`);
    if (code === "email_already_exists") return "exists";
    // Plans without tags reject a tag that doesn't exist yet. Losing the tag
    // is better than losing the signup, so retry once without tags.
    if (code === "feature_disabled" && body.tags) return buttondownCreate(env, email, [], ip);
    if (code === "subscriber_blocked") return "blocked";
    return "failed";
  } catch (err) {
    console.log(`[tos.watch] Buttondown subscribe error: ${err.name}`);
    return "failed";
  }
}

/**
 * Add tags to a subscriber Buttondown already has, keeping their others.
 * Skips anyone unsubscribed or otherwise suppressed, so a vote never
 * quietly resubscribes someone who left.
 * https://docs.buttondown.com/api-subscribers-update
 */
export async function buttondownAddTags(env, email, tags) {
  if (!env.BUTTONDOWN_API_KEY) return;
  try {
    const url = `${BUTTONDOWN_API_BASE}/subscribers/${encodeURIComponent(email)}`;
    const res = await fetch(url, { method: "GET", headers: buttondownHeaders(env) });
    if (!res.ok) {
      console.log(`[tos.watch] Buttondown lookup ${res.status}`);
      return;
    }
    const sub = await res.json();
    if (BUTTONDOWN_SUPPRESSED.includes(sub.type)) return;
    const current = sub.tags || [];
    const merged = [...new Set([...current, ...tags])];
    if (merged.length === current.length) return;
    const patch = await fetch(url, {
      method: "PATCH",
      headers: buttondownHeaders(env),
      body: JSON.stringify({ tags: merged }),
    });
    if (!patch.ok) console.log(`[tos.watch] Buttondown tag update ${patch.status}`);
  } catch (err) {
    console.log(`[tos.watch] Buttondown tag error: ${err.name}`);
  }
}

/** Create the subscriber with these tags, or add the tags if Buttondown
 * already has them. */
async function buttondownUpsertTags(env, email, tags, ip) {
  const outcome = await buttondownCreate(env, email, tags, ip);
  if (outcome === "exists") await buttondownAddTags(env, email, tags);
  return outcome;
}

/** tracks[] from a /subscribe body -> track:<id> tags. Unknown ids are
 * dropped; an omitted list adds no track tags (everyone gets every alert). */
function trackTags(input) {
  if (!Array.isArray(input)) return [];
  return [...new Set(input.filter((t) => typeof t === "string" && VALID_TRACKS.includes(t)))].map(
    (t) => `track:${t}`
  );
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
  const tags = [source, ...trackTags(body.tracks)];

  // A repeat signup still asks Buttondown, so an address its firewall blocked
  // earlier gets through on a retry; the reply never says it was known.
  return subscribeResult(await buttondownUpsertTags(env, email, tags, ip));
}

function subscribeResult(outcome) {
  if (outcome === "blocked") return json({ ok: false, error: "blocked" }, 422);
  if (outcome === "failed") return json({ ok: true, next: "saved" });
  return json({ ok: true, next: "confirm" });
}

/**
 * POST /request {service?, url?, email?} -- log interest in a service that
 * isn't tracked (or isn't known to us at all). Dedupes by a normalized key
 * so repeat requests increment an anonymous count instead of piling up rows.
 * An optional email tags that subscriber requested:<key> in Buttondown (and
 * creates them, double opt-in, if new); it is never stored here.
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
    await buttondownUpsertTags(env, email, [`requested:${normalizedKey.replace(":", "-")}`], ip);
  }

  return json({ ok: true });
}

/**
 * POST /vote {feature, email} -- a +1 on a feature request (e.g. the mock
 * on-demand check), as a vote:<feature> tag in Buttondown. It counts once
 * that subscriber has confirmed (type "regular"). Always returns the same
 * generic response whether or not the email was already known, so the
 * response itself never reveals subscriber status.
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

  if (!VALID_FEATURES.includes(feature) || !email || email.length > 320 || !EMAIL_RE.test(email)) {
    return json({ ok: false, error: "invalid_vote" }, 400);
  }

  await env.DB.prepare(
    "INSERT INTO feature_counts (feature, unverified_count, updated_at) VALUES (?, 1, ?) " +
      "ON CONFLICT(feature) DO UPDATE SET unverified_count = unverified_count + 1, updated_at = excluded.updated_at"
  )
    .bind(feature, new Date().toISOString())
    .run();

  await buttondownUpsertTags(env, email, [`vote:${feature}`], ip);

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
