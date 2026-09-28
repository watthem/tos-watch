// Tests for GET /check, written from the requirement before the handler
// existed (owner, chat, 2026-09-26): a MOCK on-demand check. It must never
// make an outbound fetch of any kind -- it only looks a normalized URL up
// in the bundled tracked/archived service list.
import { SELF } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

async function check(url) {
  return SELF.fetch(`https://tos.watch/check?url=${encodeURIComponent(url)}`);
}

describe("GET /check", () => {
  it("never performs an outbound fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await check("https://www.grammarly.com");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("reports a tracked service as tracked, with its slug", async () => {
    const res = await check("https://www.grammarly.com/legal");
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("tracked");
    expect(body.slug).toBe("grammarly");
  });

  it("reports an unknown site as unknown, offering the on-demand-check feature", async () => {
    const res = await check("https://this-service-does-not-exist-abc123.example.com");
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("unknown");
    expect(body.feature).toBe("on-demand-check");
    expect(body.message).toMatch(/coming/i);
  });

  it("rejects a missing or malformed url without crashing", async () => {
    const res = await SELF.fetch("https://tos.watch/check");
    expect(res.status).toBe(400);
  });
});
