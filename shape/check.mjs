#!/usr/bin/env node
// Document-shape check: does each new version of a watched document still
// look like the same document? A site redesign or an extractor change shows
// up as many new Stela chunk hashes (structure churn) with few new words
// (content churn), a word count far outside the document's own history, or
// bot-block text in place of the policy. Those versions need a person to
// look at the site or the pipeline before their alert is trusted.
//
// Each version gets a profile; a FieldTest (Standard Schema, zod) schema
// built from that document's own history validates it.
//
//   TOS_WATCH_DATA=... node shape/check.mjs [--since YYYY-MM-DD] [--profile]
//
// Writes $TOS_WATCH_DATA/pipeline/shape.json (flagged versions, and the
// latest profile per document) and prints a Markdown list of versions on or
// after --since that failed and weren't flagged by an earlier run, for an
// issue. Exits 0 either way.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { validateWithSchema, z } from "@fieldtest/core";
import { run as stela } from "@watthem/stela";

const CODE = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DATA = resolve(process.env.TOS_WATCH_DATA || CODE);
const args = process.argv.slice(2);
const opt = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
const since = opt("--since") || "0000-00-00";
const watchlistPath = opt("--watchlist") || join(CODE, "pipeline", "watchlist.json");
const profiling = args.includes("--profile");

// Thresholds. Restructure: at least this share of chunks is new while at
// most this share of 5-word shingles is (Zoom Privacy Policy 2021-11-03:
// 0.48 vs 0.07; ordinary edits sit near 0.01-0.08 structure).
const RESTRUCTURE_MIN = 0.3;
const RESTRUCTURE_CONTENT_MAX = 0.1;
const WORDS_LOW = 0.5; // x median of the previous versions
const WORDS_HIGH = 2.0;
const HISTORY = 10; // previous versions the bounds come from
const MIN_HISTORY = 3; // fewer than this: no word bounds yet
const BOT_BLOCK = /\b(enable javascript|access denied|are you a robot|verify you are human|captcha|request blocked|403 forbidden|just a moment)\b/i;

const timers = {};
function timed(name, fn) {
  const t0 = performance.now();
  const out = fn();
  const t = (timers[name] ??= { ms: 0, calls: 0 });
  t.ms += performance.now() - t0;
  t.calls += 1;
  return out;
}

// 5-word shingles as 32-bit numbers (FNV-1a per word, combined), not
// strings: a Set of numbers is several times cheaper than one of ~6,000
// joined strings per version. A rare collision only nudges a share.
function shingles(text) {
  const w = text.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter(Boolean);
  const h = new Uint32Array(w.length);
  for (let i = 0; i < w.length; i++) {
    let x = 0x811c9dc5;
    const s = w[i];
    for (let k = 0; k < s.length; k++) x = Math.imul(x ^ s.charCodeAt(k), 0x01000193);
    h[i] = x >>> 0;
  }
  const out = new Set();
  for (let i = 0; i + 5 <= h.length; i++) {
    out.add((Math.imul(h[i], 31 ** 4) + Math.imul(h[i + 1], 31 ** 3) + Math.imul(h[i + 2], 961) + Math.imul(h[i + 3], 31) + h[i + 4]) >>> 0);
  }
  return out;
}

const newShare = (cur, prev) => (cur.size ? [...cur].filter((x) => !prev.has(x)).length / cur.size : 0);

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
}

// The schema a version must satisfy, from its document's own history.
function schemaFor(history) {
  const recent = history.slice(-HISTORY).map((p) => p.words);
  const m = recent.length >= MIN_HISTORY ? median(recent) : null;
  const words = m
    ? z.number().min(Math.floor(m * WORDS_LOW), { message: `words fell below half the recent median (${m})` })
        .max(Math.ceil(m * WORDS_HIGH), { message: `words more than doubled the recent median (${m})` })
    : z.number().positive();
  return z
    .object({
      words,
      chunks: z.number().min(3, { message: "fewer than 3 chunks: probably not the policy text" }),
      botBlock: z.literal(false, { message: "bot-block or error text instead of the policy" }),
      structure: z.number().nullable(),
      content: z.number().nullable(),
    })
    .refine((p) => !(p.structure >= RESTRUCTURE_MIN && p.content !== null && p.content <= RESTRUCTURE_CONTENT_MAX), {
      message: "restructured with few new words: layout or extraction change?",
      path: ["structure"],
    });
}

function git(repo, ...a) {
  return execFileSync("git", ["-C", repo, ...a], { maxBuffer: 1 << 28 });
}

// Every version of one document in a single `git cat-file --batch` call:
// one process per document instead of one per version (profiled 2026-09-29:
// per-version `git show` was 42% of the run).
function readVersions(repo, revs, path) {
  const buf = execFileSync("git", ["-C", repo, "cat-file", "--batch"], {
    input: revs.map((r) => `${r}:${path}`).join("\n") + "\n",
    maxBuffer: 1 << 30,
  });
  const out = [];
  let at = 0;
  for (let i = 0; i < revs.length; i++) {
    const nl = buf.indexOf(10, at);
    const header = buf.subarray(at, nl).toString();
    const size = Number(header.split(" ")[2]);
    if (!Number.isFinite(size)) throw new Error(`git cat-file: ${header}`);
    out.push(buf.subarray(nl + 1, nl + 1 + size));
    at = nl + 1 + size + 1;
  }
  return out;
}

const watchlist = JSON.parse(readFileSync(watchlistPath, "utf8"));
const flagged = [];
const latest = {};
const t0 = performance.now();
let versions = 0;

for (const d of watchlist.documents) {
  const repo = join(DATA, "cache", d.repo);
  const log = timed("git log", () => git(repo, "log", "--reverse", "--format=%H %ad", "--date=short", "--", d.path)).toString().trim();
  if (!log) continue;
  const history = [];
  let prevChunks = null;
  let prevSh = null;
  const lines = log.split("\n").map((l) => l.split(" "));
  const bufs = timed("git cat-file", () => readVersions(repo, lines.map(([rev]) => rev), d.path));
  for (const [i, [rev, date]] of lines.entries()) {
    const buf = bufs[i];
    const text = buf.toString("utf8");
    let chunks;
    try {
      chunks = timed("stela", () => stela(buf, { strategy: "paragraph", file: d.path, validate: false }).chunks);
    } catch (err) {
      flagged.push({ vendor: d.vendor, doc: d.doc, date, rev, issues: [`stela rejected the text: ${err.message}`] });
      continue;
    }
    const hashes = timed("hash sets", () => new Set(chunks.map((c) => c.source.contentHash)));
    const sh = timed("shingles", () => shingles(text));
    const profile = {
      date,
      rev: rev.slice(0, 10),
      words: text.split(/\s+/).filter(Boolean).length,
      chunks: chunks.length,
      headings: (text.match(/^#{1,6} /gm) || []).length,
      botBlock: BOT_BLOCK.test(text.slice(0, 5000)),
      structure: prevChunks ? Number(newShare(hashes, prevChunks).toFixed(3)) : null,
      content: prevSh ? Number(newShare(sh, prevSh).toFixed(3)) : null,
    };
    const schema = timed("schema", () => schemaFor(history));
    const tv = performance.now();
    const result = await validateWithSchema(schema, profile);
    (timers.fieldtest ??= { ms: 0, calls: 0 }).ms += performance.now() - tv;
    timers.fieldtest.calls += 1;
    if (result && result.issues) {
      flagged.push({ vendor: d.vendor, doc: d.doc, ...profile, issues: result.issues.map((i) => i.message) });
    }
    history.push(profile);
    prevChunks = hashes;
    prevSh = sh;
    versions += 1;
  }
  if (history.length) latest[`${d.vendor}/${d.doc}`] = history.at(-1);
}

const out = join(DATA, "pipeline", "shape.json");
mkdirSync(dirname(out), { recursive: true });
// Report a flag once: skip versions already flagged by an earlier run.
let seen = new Set();
try {
  seen = new Set(JSON.parse(readFileSync(out, "utf8")).flagged.map((f) => `${f.vendor}/${f.doc}@${f.rev}`));
} catch {}
writeFileSync(out, JSON.stringify({ flagged, latest }, null, 1) + "\n");

const recent = flagged.filter((f) => f.date >= since && !seen.has(`${f.vendor}/${f.doc}@${f.rev}`));
console.error(`shape: ${versions} versions of ${watchlist.documents.length} documents, ${flagged.length} flagged (${recent.length} new since ${since}) in ${((performance.now() - t0) / 1000).toFixed(1)}s`);
if (profiling) {
  for (const [k, v] of Object.entries(timers).sort((a, b) => b[1].ms - a[1].ms)) {
    console.error(`  ${k.padEnd(10)} ${(v.ms / 1000).toFixed(2)}s over ${v.calls} calls (${(v.ms / v.calls).toFixed(2)} ms each)`);
  }
}
for (const f of recent) {
  console.log(`- ${f.vendor} ${f.doc} (${f.date}, ${f.rev}): ${f.issues.join("; ")}`);
}
