#!/usr/bin/env node
// Renders one page of a PDF to a PNG using pdf.js + node-canvas.
//
// This is the "best-effort" second render engine for the golden-image tests
// (pypdfium2 is the must-have, in-process, Python-side engine; this is the
// pdf.js side, shelled out to from pytest since pdf.js is a JS-only library).
// Kept in tools/ (a .cjs file, not .mjs, because pdfjs-dist's legacy Node
// build for the pinned version, 3.11.174, ships CommonJS only) rather than
// tests/ because "render an arbitrary PDF page to a PNG via pdf.js" is
// general-purpose, not test-only, logic.
//
// Usage:
//   node tools/render_pdfjs_cli.cjs <in.pdf> <out.png> [pageIndex] [scale]
//
// pdf.js version is pinned to 3.11.174 to match the version demo/index.html
// already loads from the CDN (see demo/index.html's <script> tag) -- so the
// only pdf.js this repo ever exercises, in the browser or in tests, is one
// version. A quick spike found pdfjs-dist 6.x renders blank pages under
// node-canvas (a real incompatibility between that pdf.js release and the
// canvas package, not a bug in this script) -- 3.11.174 was verified to
// render both text and AcroForm widget appearances correctly.
const pdfjsLib = require("pdfjs-dist/legacy/build/pdf.js");
const { createCanvas } = require("canvas");
const fs = require("node:fs");
const path = require("node:path");

class NodeCanvasFactory {
  create(width, height) {
    const canvas = createCanvas(width, height);
    return { canvas, context: canvas.getContext("2d") };
  }
  reset(ctx, width, height) {
    ctx.canvas.width = width;
    ctx.canvas.height = height;
  }
  destroy(ctx) {
    ctx.canvas.width = 0;
    ctx.canvas.height = 0;
    ctx.canvas = null;
    ctx.context = null;
  }
}

const pkgDir = path.dirname(require.resolve("pdfjs-dist/package.json"));
const [, , srcPath, outPath, pageIndexArg, scaleArg] = process.argv;
const pageIndex = pageIndexArg ? Number(pageIndexArg) : 0;
const scale = scaleArg ? Number(scaleArg) : 3.0;

if (!srcPath || !outPath) {
  console.error("usage: node tools/render_pdfjs_cli.cjs <in.pdf> <out.png> [pageIndex] [scale]");
  process.exit(2);
}

(async () => {
  const data = new Uint8Array(fs.readFileSync(srcPath));
  const doc = await pdfjsLib.getDocument({
    data,
    // Local, packaged font/cmap data -- no network fetch, works offline.
    standardFontDataUrl: path.join(pkgDir, "standard_fonts") + path.sep,
    cMapUrl: path.join(pkgDir, "cmaps") + path.sep,
    cMapPacked: true,
    canvasFactory: new NodeCanvasFactory(),
  }).promise;
  const page = await doc.getPage(pageIndex + 1);
  const viewport = page.getViewport({ scale });
  const factory = new NodeCanvasFactory();
  const { canvas, context } = factory.create(viewport.width, viewport.height);
  await page.render({
    canvasContext: context,
    viewport,
    // ENABLE is pdf.js's own default, spelled out here because it is the
    // one setting these tests actually depend on: without it, AcroForm
    // widget appearance streams -- the whole point of these tests -- would
    // not be drawn.
    annotationMode: pdfjsLib.AnnotationMode.ENABLE,
  }).promise;
  fs.writeFileSync(outPath, canvas.toBuffer("image/png"));
})().catch((e) => {
  console.error(e.stack || String(e));
  process.exit(1);
});
