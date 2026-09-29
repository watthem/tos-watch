// Tests for POST /request, written from the requirement before the handler
// existed: /request stores optional personal data (an email), so the sad
// path matters as much as the happy path.
//
// (a) happy: request -> row created; repeat request increments the count
//     and does not duplicate. A request with an email becomes a Buttondown
//     tag (see subscribe.test.js); no email is stored here.
// (b) sad: missing/oversized service is rejected and stores no row; no
//     route returns stored requests or emails.
import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { _resetRateLimitForTests } from "../src/index.js";

async function request(body) {
  return SELF.fetch("https://tos.watch/request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM requests").run();
  _resetRateLimitForTests();
});

describe("POST /request happy path", () => {
  it("creates a row for a new service request", async () => {
    const res = await request({ service: "Hinge" });
    expect(res.status).toBe(200);

    const row = await env.DB.prepare(
      "SELECT service, unverified_count FROM requests WHERE normalized_key = ?"
    )
      .bind("service:hinge")
      .first();
    expect(row.service).toBe("Hinge");
    expect(row.unverified_count).toBe(1);
  });

  it("increments the count on a repeat request instead of duplicating", async () => {
    await request({ service: "Hinge" });
    await request({ service: "  hinge  " }); // same service, different case/whitespace

    const rows = await env.DB.prepare(
      "SELECT unverified_count FROM requests WHERE normalized_key = ?"
    )
      .bind("service:hinge")
      .all();
    expect(rows.results.length).toBe(1);
    expect(rows.results[0].unverified_count).toBe(2);
  });

  it("dedupes a URL request against the same normalized URL", async () => {
    await request({ url: "https://example.com/legal/" });
    await request({ url: "https://www.example.com/legal" });

    const rows = await env.DB.prepare("SELECT unverified_count FROM requests").all();
    expect(rows.results.length).toBe(1);
    expect(rows.results[0].unverified_count).toBe(2);
  });
});

describe("POST /request sad path", () => {
  it("rejects a request with neither service nor url and stores nothing", async () => {
    const res = await request({ email: "someone@example.com" });
    expect(res.status).toBe(400);

    const rows = await env.DB.prepare("SELECT * FROM requests").all();
    expect(rows.results.length).toBe(0);
  });

  it("rejects an oversized service name and stores nothing", async () => {
    const res = await request({ service: "x".repeat(500) });
    expect(res.status).toBe(400);

    const rows = await env.DB.prepare("SELECT * FROM requests").all();
    expect(rows.results.length).toBe(0);
  });

  it("rejects an invalid url and stores nothing", async () => {
    const res = await request({ url: "not a url" });
    expect(res.status).toBe(400);

    const rows = await env.DB.prepare("SELECT * FROM requests").all();
    expect(rows.results.length).toBe(0);
  });

  it("GET /request does not exist as a listing route", async () => {
    await request({ service: "Hinge", email: "listed@example.com" });
    const res = await SELF.fetch("https://tos.watch/request");
    expect(res.status).toBe(404);
    const text = await res.text();
    expect(text).not.toContain("listed@example.com");
    expect(text).not.toContain("Hinge");
  });

  it("never returns a stored email or service list from the /request response", async () => {
    const res = await request({ service: "Hinge", email: "secret2@example.com" });
    const body = await res.clone().text();
    expect(body).not.toContain("secret2@example.com");
    expect(body).not.toContain("Hinge");
  });
});
