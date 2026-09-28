// Replace site/build.py's plain removed/added blocks with @pierre/diffs
// word diffs, rendered ahead of time into declarative shadow DOM (no
// client-side JavaScript). Pierre's core stylesheet is written once to
// site/diffs.css and linked from each shadow root, so the browser caches it
// instead of inlining ~31 KB into every diff. Colours and fonts come from the
// page's own tokens through the --diffs-* custom properties (site/style.css).
//
// Usage: node build/render-diffs.mjs <site dir>
import { preloadDiffHTML } from "@pierre/diffs/ssr";
import fs from "node:fs";
import path from "node:path";

const siteDir = path.resolve(process.argv[2] || "site");
const BLOCK = /<div class="diff" data-old="([^"]*)" data-new="([^"]*)" data-words="([^"]*)">[\s\S]*?<\/div><!--\/diff-->/g;
const OPTIONS = {
  diffStyle: "unified",
    overflow: "wrap",
  disableFileHeader: true,
  disableLineNumbers: true,
  theme: "pierre-light",
  themeType: "light",
};

const unescape = (s) =>
  s.replace(/&(amp|lt|gt|quot|#x27|#39);/g, (_, e) => ({ amp: "&", lt: "<", gt: ">", quot: '"', "#x27": "'", "#39": "'" })[e]);

let coreCss = null;
const cache = new Map();

async function render(oldText, newText, words) {
  const key = words + "\u0000" + oldText + "\u0000" + newText;
  if (cache.has(key)) return cache.get(key);
  const out = await preloadDiffHTML({
    oldFile: { name: "policy.txt", contents: oldText ? oldText + "\n" : "" },
    newFile: { name: "policy.txt", contents: newText ? newText + "\n" : "" },
    options: { ...OPTIONS, lineDiffType: words },
  });
  if (!coreCss) coreCss = out.match(/<style data-core-css="">([\s\S]*?)<\/style>/)?.[1] ?? "";
  // Keep only the diff markup: drop the inline stylesheets and the icon sprite
  // (file headers and expand buttons, which these diffs never show).
  const markup = out
    .replace(/<style[^>]*>[\s\S]*?<\/style>/g, "")
    .replace(/<svg data-icon-sprite[\s\S]*?<\/svg>/, "");
  cache.set(key, markup);
  return markup;
}

function htmlFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = path.join(dir, d.name);
    if (d.isDirectory()) return d.name === "fonts" ? [] : htmlFiles(p);
    return d.name.endsWith(".html") ? [p] : [];
  });
}

let files = 0;
let diffs = 0;
for (const file of htmlFiles(siteDir)) {
  const text = fs.readFileSync(file, "utf8");
  const matches = [...text.matchAll(BLOCK)];
  if (!matches.length) continue;
  const css = path.relative(path.dirname(file), path.join(siteDir, "diffs.css")).split(path.sep).join("/");
  let out = "";
  let last = 0;
  for (const m of matches) {
    const markup = await render(unescape(m[1]), unescape(m[2]), m[3]);
    out += text.slice(last, m.index);
    out +=
      `<div class="diff"><diffs-container><template shadowrootmode="open">` +
      `<link rel="stylesheet" href="${css}">${markup}</template></diffs-container></div>`;
    last = m.index + m[0].length;
    diffs++;
  }
  fs.writeFileSync(file, out + text.slice(last));
  files++;
}

if (coreCss) {
  fs.writeFileSync(
    path.join(siteDir, "diffs.css"),
    "/* @pierre/diffs core styles. Copyright 2025 Pierre Computer Company.\n" +
      "   Licensed under the Apache License, Version 2.0: http://www.apache.org/licenses/LICENSE-2.0\n" +
      "   https://diffs.com */\n" +
      coreCss +
      "\n/* tos.watch additions: struck-through removed words, so the change never relies on colour alone. */\n" +
      "[data-line-type=change-deletion] [data-diff-span]{text-decoration:line-through;text-decoration-thickness:1px}\n" +
      "[data-line-type=change-deletion] [data-diff-span] *{text-decoration:inherit}\n",
  );
}
console.log(`diffs: ${diffs} rendered with @pierre/diffs across ${files} pages (${cache.size} unique)`);
