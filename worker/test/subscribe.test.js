// Tests written from the requirement (tos.watch weekend MVP prompt), before
// any implementation was assumed to be correct:
//
// (a) happy path: subscribe -> row pending -> confirm with token -> confirmed
//     -> unsubscribe with token -> unsubscribed.
// (b) sad path: invalid email rejected, wrong/missing token does not change
//     any row, and no route returns any stored email.
import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { _resetRateLimitForTests } from "../src/index.js";

async function subscribe(email, source) {
  return SELF.fetch("https://tos.watch/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, source }),
  });
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM subscribers").run();
  _resetRateLimitForTests();
});

describe("subscribe -> confirm -> unsubscribe happy path", () => {
  it("moves a subscriber from pending to confirmed to unsubscribed", async () => {
    const email = "reader@example.com";

    const subRes = await subscribe(email, "landing-page");
    expect(subRes.status).toBe(200);

    const pendingRow = await env.DB.prepare(
      "SELECT status, token FROM subscribers WHERE email = ?"
    )
      .bind(email)
      .first();
    expect(pendingRow.status).toBe("pending");
    expect(typeof pendingRow.token).toBe("string");
    expect(pendingRow.token.length).toBeGreaterThan(10);

    const confirmRes = await SELF.fetch(
      `https://tos.watch/confirm?token=${pendingRow.token}`
    );
    expect(confirmRes.status).toBe(200);

    const confirmedRow = await env.DB.prepare(
      "SELECT status, confirmed_at FROM subscribers WHERE email = ?"
    )
      .bind(email)
      .first();
    expect(confirmedRow.status).toBe("confirmed");
    expect(confirmedRow.confirmed_at).toBeTruthy();

    const unsubRes = await SELF.fetch(
      `https://tos.watch/unsubscribe?token=${pendingRow.token}`
    );
    expect(unsubRes.status).toBe(200);

    const finalRow = await env.DB.prepare(
      "SELECT status FROM subscribers WHERE email = ?"
    )
      .bind(email)
      .first();
    expect(finalRow.status).toBe("unsubscribed");
  });

  it("is idempotent on a repeat subscribe of the same email", async () => {
    const email = "again@example.com";
    await subscribe(email);
    const first = await env.DB.prepare("SELECT token FROM subscribers WHERE email = ?")
      .bind(email)
      .first();

    const secondRes = await subscribe(email);
    expect(secondRes.status).toBe(200);

    const rows = await env.DB.prepare("SELECT token FROM subscribers WHERE email = ?")
      .bind(email)
      .all();
    expect(rows.results.length).toBe(1);
    expect(rows.results[0].token).toBe(first.token);
  });
});

describe("sad path: invalid input is rejected without side effects", () => {
  it("rejects an invalid email and stores no row", async () => {
    const res = await subscribe("not-an-email");
    expect(res.status).toBe(400);

    const rows = await env.DB.prepare("SELECT * FROM subscribers").all();
    expect(rows.results.length).toBe(0);
  });

  it("does not change any row for a wrong confirm token", async () => {
    const email = "victim@example.com";
    await subscribe(email);
    const before = await env.DB.prepare(
      "SELECT status FROM subscribers WHERE email = ?"
    )
      .bind(email)
      .first();

    const res = await SELF.fetch("https://tos.watch/confirm?token=not-the-real-token");
    expect(res.status).toBe(400);

    const after = await env.DB.prepare(
      "SELECT status FROM subscribers WHERE email = ?"
    )
      .bind(email)
      .first();
    expect(after.status).toBe(before.status);
  });

  it("does not change any row for a missing unsubscribe token", async () => {
    const email = "victim2@example.com";
    await subscribe(email);

    const res = await SELF.fetch("https://tos.watch/unsubscribe");
    expect(res.status).toBe(400);

    const after = await env.DB.prepare(
      "SELECT status FROM subscribers WHERE email = ?"
    )
      .bind(email)
      .first();
    expect(after.status).toBe("pending");
  });

  it("subscribes honestly with no Buttondown key configured (no-op, no throw)", async () => {
    // The owner has not created a Buttondown account yet. With no
    // BUTTONDOWN_API_KEY secret bound (the default in this test env),
    // /subscribe must still succeed and record the subscriber locally,
    // exactly as if Buttondown didn't exist.
    expect(env.BUTTONDOWN_API_KEY).toBeUndefined();

    const email = "no-key@example.com";
    const res = await subscribe(email);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);

    const row = await env.DB.prepare("SELECT status FROM subscribers WHERE email = ?")
      .bind(email)
      .first();
    expect(row.status).toBe("pending");
  });

  it("never returns a stored email from any route", async () => {
    const email = "secret@example.com";
    const subRes = await subscribe(email);
    const subText = await subRes.clone().text();
    expect(subText).not.toContain(email);

    const row = await env.DB.prepare("SELECT token FROM subscribers WHERE email = ?")
      .bind(email)
      .first();

    const confirmRes = await SELF.fetch(`https://tos.watch/confirm?token=${row.token}`);
    const confirmText = await confirmRes.clone().text();
    expect(confirmText).not.toContain(email);

    const unsubRes = await SELF.fetch(`https://tos.watch/unsubscribe?token=${row.token}`);
    const unsubText = await unsubRes.clone().text();
    expect(unsubText).not.toContain(email);
  });
});
