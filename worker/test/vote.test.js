// Tests for POST /vote, written from the requirement before the handler
// existed (owner, chat, 2026-09-26): a verified +1 on a feature request.
//
// (a) happy: a confirmed email votes once and the count is 1; a repeat vote
//     is still 1; a pending email votes, the count is 0, then after confirm
//     it's 1.
// (b) sad: the vote response is identical for a known (already-subscribed)
//     and unknown email; no route lists votes or emails.
import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { _resetRateLimitForTests } from "../src/index.js";

const FEATURE = "on-demand-check";

async function vote(feature, email) {
  return SELF.fetch("https://tos.watch/vote", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ feature, email }),
  });
}

async function confirmedCount(feature) {
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM votes WHERE feature = ? AND status = 'confirmed'"
  )
    .bind(feature)
    .first();
  return row.n;
}

async function insertConfirmedSubscriber(email) {
  await env.DB.prepare(
    "INSERT INTO subscribers (email, status, token, source, created_at, confirmed_at) VALUES (?, 'confirmed', 'tok-' || ?, 'test', ?, ?)"
  )
    .bind(email, email, new Date().toISOString(), new Date().toISOString())
    .run();
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM subscribers").run();
  await env.DB.prepare("DELETE FROM requests").run();
  await env.DB.prepare("DELETE FROM votes").run();
  _resetRateLimitForTests();
});

describe("POST /vote happy path", () => {
  it("counts a confirmed email's vote immediately, once", async () => {
    await insertConfirmedSubscriber("fan@example.com");

    const res1 = await vote(FEATURE, "fan@example.com");
    expect(res1.status).toBe(200);
    expect(await confirmedCount(FEATURE)).toBe(1);

    const res2 = await vote(FEATURE, "fan@example.com"); // repeat
    expect(res2.status).toBe(200);
    expect(await confirmedCount(FEATURE)).toBe(1);
  });

  it("only counts a pending (new) email's vote after they confirm", async () => {
    const res = await vote(FEATURE, "new-voter@example.com");
    expect(res.status).toBe(200);
    expect(await confirmedCount(FEATURE)).toBe(0);

    const sub = await env.DB.prepare(
      "SELECT token, source FROM subscribers WHERE email = ?"
    )
      .bind("new-voter@example.com")
      .first();
    expect(sub.source).toBe(`vote:${FEATURE}`);

    await SELF.fetch(`https://tos.watch/confirm?token=${sub.token}`);
    expect(await confirmedCount(FEATURE)).toBe(1);
  });
});

describe("POST /vote sad path", () => {
  it("does not fetch anything (no live network calls from voting)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await vote(FEATURE, "no-network@example.com");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("returns the same response for a known (confirmed) and an unknown email", async () => {
    await insertConfirmedSubscriber("known@example.com");

    const resKnown = await vote("feature-a", "known@example.com");
    const resUnknown = await vote("feature-a", "brand-new@example.com");

    expect(resKnown.status).toBe(resUnknown.status);
    const [bodyKnown, bodyUnknown] = await Promise.all([resKnown.json(), resUnknown.json()]);
    expect(bodyKnown).toEqual(bodyUnknown);
  });

  it("rejects a missing feature or email without creating a vote", async () => {
    const res = await vote("", "someone@example.com");
    expect(res.status).toBe(400);
    const rows = await env.DB.prepare("SELECT * FROM votes").all();
    expect(rows.results.length).toBe(0);
  });

  it("GET /vote is not a listing route", async () => {
    await vote(FEATURE, "listed-voter@example.com");
    const res = await SELF.fetch("https://tos.watch/vote");
    expect(res.status).toBe(404);
    const text = await res.text();
    expect(text).not.toContain("listed-voter@example.com");
  });
});
