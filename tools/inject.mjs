// Field injection: takes a flat PDF plus a fields document (the shape
// engine.detect.detect() emits -- see eval/contracts/fields.schema.json) and
// returns a PDF with real AcroForm widgets added, one per field.
//
// This is the extraction of what used to live only as a browser-only spike
// inside demo/index.html (the "pdf-lib spike", ~line 264). It is the single
// place this logic is written down; tests/ and demo/index.html both call it
// so they cannot silently diverge from each other.
//
// Works in two environments:
//   - Node (tests, CLI): pdf-lib is loaded from node_modules via a dynamic
//     `import('pdf-lib')`.
//   - Browser (demo/index.html): demo/index.html already loads pdf-lib from
//     a CDN <script> tag as the global `PDFLib`, so this module reuses that
//     instance instead of asking the browser to fetch a second copy.
//
// Field shape consumed (per field, from fields.json):
//   { id, type: 'text'|'multiline'|'checkbox', page, rect: [x0,y0,x1,y1], value? }
// `page` is 1-indexed. `rect` is [x0,y0,x1,y1] in PDF points, origin
// bottom-left -- exactly engine.detect's convention, unchanged here.

async function getPDFLib() {
  if (typeof window !== "undefined" && window.PDFLib) return window.PDFLib;
  return import("pdf-lib");
}

/**
 * @param {Uint8Array|ArrayBuffer} pdfBytes  the flat, unmodified source PDF
 * @param {{fields: Array<object>}} fieldsDoc  the fields.json-shaped document
 * @param {{flatten?: boolean}} [opts]
 * @returns {Promise<{bytes: Uint8Array, made: number, failed: string[], deferredAppearances: boolean}>}
 */
export async function injectFields(pdfBytes, fieldsDoc, opts = {}) {
  const { flatten = false } = opts;
  const { PDFDocument, PDFName, PDFBool } = await getPDFLib();
  const doc = await PDFDocument.load(pdfBytes);
  const form = doc.getForm();

  let made = 0;
  const failed = [];

  // pdf-lib throws if two fields share a name, and the second field -- with
  // whatever the user typed into it -- is then dropped from the output PDF,
  // silent data loss. detect() de-duplicates its ids, but the demo lets a
  // person rename a box to one that already exists, so a colliding name can
  // still reach here. Disambiguate exactly the way detect() and the demo's
  // uid() do (base, base_2, base_3, ...) so every field lands. An empty id
  // falls back to 'field' rather than throwing on a nameless widget.
  const used = new Set();
  const uniqueName = (id) => {
    let base = String(id ?? "").trim() || "field";
    let name = base, n = 1;
    while (used.has(name)) name = `${base}_${++n}`;
    used.add(name);
    return name;
  };

  for (const f of fieldsDoc.fields) {
    const page = doc.getPage(f.page - 1);
    const [x0, y0, x1, y1] = f.rect;
    const box = { x: x0, y: y0, width: Math.max(6, x1 - x0), height: Math.max(6, y1 - y0), borderWidth: 0 };
    const name = uniqueName(f.id);
    try {
      if (f.type === "checkbox") {
        const cb = form.createCheckBox(name);
        cb.addToPage(page, box);
        if (f.value) cb.check();
      } else {
        // 'text' and 'multiline' are both pdf-lib text fields; 'multiline'
        // additionally gets enableMultiline() so long values wrap instead
        // of scrolling off a single line.
        const tf = form.createTextField(name);
        tf.addToPage(page, box);
        if (f.type === "multiline") tf.enableMultiline();
        tf.setFontSize(Math.max(8, Math.min(12, (y1 - y0) * 0.62))); // pdf-lib auto-sizes too big
        if (f.value) tf.setText(String(f.value));
      }
      made++;
    } catch (err) {
      failed.push(`${name}: ${err.message}`);
    }
  }

  // pdf-lib draws every field's appearance with the standard PDF font
  // (Helvetica / WinAnsi) at save() time. A value with any character outside
  // WinAnsi -- CJK, emoji, Cyrillic, most non-Latin scripts -- cannot be
  // encoded by that font, so ONE such character in ONE field otherwise makes
  // the WHOLE doc.save() throw and the user cannot download their form at all.
  // We do not bundle a Unicode font (a large asset with its own licence), so
  // for those values we defer appearance generation to the PDF viewer via the
  // AcroForm NeedAppearances flag. The field value (/V) is written either way,
  // so no typed data is lost -- the viewer just draws it with its own fonts.
  let deferredAppearances = false;
  let bytes;
  try {
    if (flatten) form.flatten();   // flatten bakes appearances, so it can throw too
    bytes = await doc.save();       // default: regenerates appearances with Helvetica
  } catch (err) {
    if (!/encode/i.test(String((err && err.message) || err))) throw err;
    // A value needs characters the standard font cannot draw. flatten() bakes
    // appearances, so a deferred save is necessarily an un-flattened (still
    // fillable) form -- the caller should say so.
    deferredAppearances = true;
    form.acroForm.dict.set(PDFName.of("NeedAppearances"), PDFBool.True);
    bytes = await doc.save({ updateFieldAppearances: false });
  }
  return { bytes, made, failed, deferredAppearances };
}
