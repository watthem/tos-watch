// Tests written from the owner's requirement (2026-09-28, chat: "remove the
// PII as early as possible from the services"), before the change:
//
// Buttondown is the only place an email address lives. /subscribe, /vote and
// /request never write an email to D1 or to the Worker logs, and a vote or a
// request from an existing subscriber is a Buttondown tag. A vote from someone
// who unsubscribed must not quietly resubscribe them.
import { env, SELF } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker, { _resetRateLimitForTests } from "../src/index.js";

const KEYED = () => ({ ...env, BUTTONDOWN_API_KEY: "test-key" });

function post(path, body, envOverride = KEYED()) {
  const req = new Request(`https://api.tos.watch${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.9" },
    body: JSON.stringify(body),
  });
  return worker.fetch(req, envOverride);
}

// A fake Buttondown: `known` maps email -> {type, tags}; records every call.
function fakeButtondown(known = {}) {
  const calls = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const method = init.method || "GET";
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url, method, body });
    const email = decodeURIComponent(url.split("/subscribers/")[1] || "");
    if (method === "POST") {
      if (known[body.email_address]) {
        return new Response(JSON.stringify({ code: "email_already_exists" }), { status: 400 });
      }
      return new Response("{}", { status: 201 });
    }
    if (method === "GET") {
      const s = known[email];
      return s ? new Response(JSON.stringify(s), { status: 200 }) : new Response("{}", { status: 404 });
    }
    if (method === "PATCH") return new Response("{}", { status: 200 });
    return new Response("{}", { status: 500 });
  });
  return { calls, spy };
}

async function dbDump() {
  const tables = await env.DB.prepare(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%' AND name != 'd1_migrations'"
  ).all();
  let text = tables.results.map((t) => t.name).join(",");
  for (const { name } of tables.results) {
    const rows = await env.DB.prepare(`SELECT * FROM "${name}"`).all();
    text += JSON.stringify(rows.results);
  }
  return text;
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM requests").run();
  _resetRateLimitForTests();
});
afterEach(() => vi.restoreAllMocks());

describe("no email address is kept outside Buttondown", () => {
  it("subscribe, vote and request leave no email in D1 or the logs", async () => {
    fakeButtondown();
    const log = vi.spyOn(console, "log");

    expect((await post("/subscribe", { email: "reader@example.com", source: "landing-page" })).status).toBe(200);
    expect((await post("/vote", { feature: "on-demand-check", email: "voter@example.com" })).status).toBe(200);
    expect((await post("/request", { service: "Hinge", email: "asker@example.com" })).status).toBe(200);
    // Same, with no Buttondown key (local dev): still nothing stored.
    await post("/subscribe", { email: "nokey@example.com" }, env);

    const dump = await dbDump();
    for (const e of ["reader@", "voter@", "asker@", "nokey@"]) expect(dump).not.toContain(e);
    expect(dump).not.toMatch(/subscribers|votes/);
    expect(dump).toContain("Hinge"); // the anonymous request count is still kept

    const logged = log.mock.calls.flat().join(" ");
    expect(logged).not.toContain("@example.com");
  });

  it("the old token routes are gone", async () => {
    expect((await SELF.fetch("https://api.tos.watch/confirm?token=x")).status).toBe(404);
    expect((await SELF.fetch("https://api.tos.watch/unsubscribe?token=x")).status).toBe(404);
  });
});

describe("Buttondown carries sources, topics, votes and requests as tags", () => {
  it("a new subscriber is created with the source tag, their visitor IP and valid topics only", async () => {
    const { calls } = fakeButtondown();
    const res = await post("/subscribe", {
      email: "Picker@Example.com ",
      source: "landing-page",
      tracks: ["dating", "'; DROP TABLE x; --"],
    });
    expect(await res.json()).toEqual({ ok: true, next: "confirm" });
    const create = calls.find((c) => c.method === "POST");
    expect(create.body.email_address).toBe("picker@example.com");
    expect(create.body.ip_address).toBe("203.0.113.9");
    expect(create.body.tags.sort()).toEqual(["landing-page", "track:dating"]);
  });

  it("a vote from an existing subscriber adds the tag and keeps their other tags", async () => {
    const { calls } = fakeButtondown({ "fan@example.com": { type: "regular", tags: ["landing-page"] } });
    await post("/vote", { feature: "on-demand-check", email: "fan@example.com" });
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch.body).toEqual({ tags: ["landing-page", "vote:on-demand-check"] });
  });

  it("a vote from someone who unsubscribed does not resubscribe or retag them", async () => {
    const { calls } = fakeButtondown({ "gone@example.com": { type: "unsubscribed", tags: [] } });
    const res = await post("/vote", { feature: "on-demand-check", email: "gone@example.com" });
    expect(res.status).toBe(200);
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("a request with an email tags the subscriber requested:<key>", async () => {
    const { calls } = fakeButtondown();
    await post("/request", { service: "Hinge", email: "wants-hinge@example.com" });
    const create = calls.find((c) => c.method === "POST");
    expect(create.body.tags).toEqual(["requested:service-hinge"]);
  });

  it("vote responses don't reveal whether the email was already known", async () => {
    fakeButtondown({ "known@example.com": { type: "regular", tags: [] } });
    const a = await post("/vote", { feature: "f", email: "known@example.com" });
    const b = await post("/vote", { feature: "f", email: "new@example.com" });
    expect(a.status).toBe(b.status);
    expect(await a.json()).toEqual(await b.json());
  });

  it("still subscribes when the Buttondown plan can't create tags", async () => {
    const calls = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init = {}) => {
      const body = JSON.parse(init.body);
      calls.push(body);
      if (body.tags && body.tags.length) {
        return new Response(JSON.stringify({ code: "feature_disabled" }), { status: 403 });
      }
      return new Response("{}", { status: 201 });
    });
    const res = await post("/subscribe", { email: "free-plan@example.com", source: "landing-page" });
    expect(await res.json()).toEqual({ ok: true, next: "confirm" });
    expect(calls.length).toBe(2);
    expect(calls[1].tags).toBeUndefined();
  });

  it("rejects an invalid email or missing feature without calling Buttondown", async () => {
    const { calls } = fakeButtondown();
    expect((await post("/subscribe", { email: "not-an-email" })).status).toBe(400);
    expect((await post("/vote", { feature: "", email: "a@example.com" })).status).toBe(400);
    expect(calls.length).toBe(0);
  });
});

// Written from the requirement (timebox 202609291040, LL-2026-09-27-02): when
// Buttondown fails, nothing is saved, so the response must not say it worked.
describe("a failed Buttondown signup is not reported as success", () => {
  it("returns not-ok for a Buttondown 500 and for a network error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 500 }));
    const a = await post("/subscribe", { email: "reader@example.com" });
    expect(a.status).toBeGreaterThanOrEqual(500);
    expect(await a.json()).toEqual({ ok: false, error: "unavailable" });

    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("network"));
    const b = await post("/subscribe", { email: "reader@example.com" });
    expect(b.status).toBeGreaterThanOrEqual(500);
    expect((await b.json()).ok).toBe(false);
  });

  it("reports a firewall block as blocked", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "subscriber_blocked" }), { status: 400 })
    );
    const res = await post("/subscribe", { email: "reader@example.com" });
    expect(res.status).toBe(422);
    expect(await res.json()).toEqual({ ok: false, error: "blocked" });
  });
});
