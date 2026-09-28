// Tests for the tracks[] addition to POST /subscribe, written from the
// requirement: a reader picks which tracks they want (checkboxes; default
// all), and an unknown track name is never stored raw.
import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { _resetRateLimitForTests } from "../src/index.js";

async function subscribe(email, tracks) {
  return SELF.fetch("https://tos.watch/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, tracks }),
  });
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM subscribers").run();
  _resetRateLimitForTests();
});

describe("POST /subscribe tracks[]", () => {
  it("stores a valid subset of known track ids", async () => {
    const res = await subscribe("picker@example.com", ["dating", "dev-tools"]);
    expect(res.status).toBe(200);
    const row = await env.DB.prepare("SELECT tracks FROM subscribers WHERE email = ?")
      .bind("picker@example.com")
      .first();
    expect(row.tracks).toBe("dating,dev-tools");
  });

  it("defaults to all tracks when tracks is omitted", async () => {
    const res = await subscribe("default-all@example.com", undefined);
    expect(res.status).toBe(200);
    const row = await env.DB.prepare("SELECT tracks FROM subscribers WHERE email = ?")
      .bind("default-all@example.com")
      .first();
    expect(row.tracks.split(",").sort()).toEqual(
      ["ai-assistants", "consumer", "dating", "dev-tools", "typing"].sort()
    );
  });

  it("drops unknown track names instead of storing them raw", async () => {
    const res = await subscribe("hacker@example.com", ["dating", "'; DROP TABLE subscribers; --"]);
    expect(res.status).toBe(200);
    const row = await env.DB.prepare("SELECT tracks FROM subscribers WHERE email = ?")
      .bind("hacker@example.com")
      .first();
    expect(row.tracks).toBe("dating");
    expect(row.tracks).not.toContain("DROP TABLE");

    // and the table really is still there
    const stillThere = await env.DB.prepare("SELECT COUNT(*) AS n FROM subscribers").first();
    expect(stillThere.n).toBeGreaterThan(0);
  });
});
