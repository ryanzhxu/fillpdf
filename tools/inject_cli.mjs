#!/usr/bin/env node
// CLI wrapper around injectFields() (tools/inject.mjs) so non-Node callers
// (the pytest render-test suite) can invoke the exact same injection logic
// the browser demo uses, as a subprocess -- there is no Python port of this
// logic, and there should not be one; this is the one implementation.
//
// Usage:
//   node tools/inject_cli.mjs <source.pdf> <fields.json> <out.pdf> [--flatten]
//
// fields.json is the fields.json-shaped document (see
// eval/contracts/fields.schema.json): { fields: [ {id,type,page,rect,value?}, ... ] }

import fs from "node:fs";
import { injectFields } from "./inject.mjs";

const [, , srcPath, fieldsPath, outPath, ...rest] = process.argv;
const flatten = rest.includes("--flatten");

if (!srcPath || !fieldsPath || !outPath) {
  console.error("usage: node tools/inject_cli.mjs <source.pdf> <fields.json> <out.pdf> [--flatten]");
  process.exit(2);
}

const pdfBytes = fs.readFileSync(srcPath);
const fieldsDoc = JSON.parse(fs.readFileSync(fieldsPath, "utf8"));

const { bytes, made, failed } = await injectFields(pdfBytes, fieldsDoc, { flatten });
fs.writeFileSync(outPath, bytes);

if (failed.length) {
  console.error(`inject: ${made}/${fieldsDoc.fields.length} fields made; failures: ${JSON.stringify(failed)}`);
  process.exit(1);
}
console.error(`inject: ${made}/${fieldsDoc.fields.length} fields made, 0 failures`);
