#!/usr/bin/env node
// Alert-file check: every alerts/*.md has the front matter and layout that
// pipeline/lib/alerts.py writes and that site/build.py and the email
// drafter read. Catches a vendor or document name that breaks the YAML
// (render() writes them unquoted), a track missing from the watchlist, or a
// hand edit that dropped a field.
//
//   node shape/alerts.mjs <alerts-dir> [--watchlist pipeline/watchlist.json]
//
// Prints one line per problem (file:line) and exits 1 if any file fails.
import { readdirSync, readFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { checkDocument, formatIssues, readDocument } from "@fieldtest/core";
import { z } from "zod";

const CODE = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const opt = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
const dir = args.find((a, i) => !a.startsWith("--") && !args[i - 1]?.startsWith("--"));
if (!dir) {
  console.error("usage: node shape/alerts.mjs <alerts-dir> [--watchlist path]");
  process.exit(2);
}
const watchlist = JSON.parse(readFileSync(opt("--watchlist") || join(CODE, "pipeline", "watchlist.json"), "utf8"));
const tracks = watchlist.tracks.map((t) => (typeof t === "string" ? t : t.id));

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const frontmatter = z.object({
  vendor: z.string().min(1),
  doc: z.string().min(1),
  date: z.string().regex(DATE, "expected YYYY-MM-DD"),
  source_url: z.string().url(),
  // Alerts written before 2026-09-26 may have no track.
  track: z.enum(tracks).optional(),
  // Only OTA-sourced alerts have a commit URL.
  ota_commit_url: z.string().url().optional(),
  scores: z.string().regex(/^[a-z_]+=\d+\.\d{2}( [a-z_]+=\d+\.\d{2})*$/, "expected key=0.00 pairs"),
});
const outline = z.object({
  headings: z
    .array(z.object({ depth: z.number() }))
    .refine((hs) => hs.filter((h) => h.depth === 1).length === 1, "expected exactly one H1 title"),
});

// Cross-field checks a schema on one half can't see.
const layout = (doc) => {
  if (doc.issues.length) return []; // parse failed; the parse issue says why
  const fm = doc.frontmatter;
  const out = [];
  const issue = (message, line) => ({ message, path: [], severity: "error", source: "rule", rule: "alert-layout", line });
  const name = basename(doc.path);
  if (typeof fm.date === "string" && !name.startsWith(`${fm.date}-`))
    out.push(issue(`file name doesn't start with the front-matter date ${fm.date}`, doc.keyLines.date));
  const h1 = doc.outline.headings.find((h) => h.depth === 1);
  const title = `${fm.vendor} ${fm.doc} changed — ${fm.date}`;
  if (h1 && fm.vendor && h1.text !== title) out.push(issue(`title should be "${title}"`, h1.line));
  if (!doc.outline.links.some((l) => l.href === fm.source_url)) out.push(issue("no Source link to source_url"));
  if (fm.ota_commit_url && !doc.outline.links.some((l) => l.href === fm.ota_commit_url))
    out.push(issue("no link to ota_commit_url"));
  return out;
};

const files = readdirSync(dir).filter((f) => f.endsWith(".md")).sort();
const issues = [];
let failed = 0;
for (const f of files) {
  const r = await checkDocument(await readDocument(join(dir, f)), { frontmatter, outline, rules: [layout] });
  if (!r.ok) failed += 1;
  issues.push(...r.issues);
}
if (issues.length) console.log(formatIssues(issues));
console.error(`alerts: ${files.length} files, ${failed} failed`);
process.exit(failed ? 1 : 0);
