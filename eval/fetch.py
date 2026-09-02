"""Polite fetcher + classifier for real-world government PDF forms.

We have exactly one hand-verified real form (fixtures/safer.pdf): produced by
"Microsoft Word for Microsoft 365", full of thin-rect table borders,
Webdings/Wingdings checkbox glyphs and underscore write-on lines. Adobe
LiveCycle Designer forms (checked: CRA T2201, IRS W-9) have none of that
structure -- they build fields with a real AcroForm instead. This module
fetches public government PDFs and classifies each one by how much it looks
like safer.pdf, so the "flat-wordlike" ones can become hand-labelled ground
truth.

    def fetch(urls, out_dir, limit=60) -> dict     # the manifest
    def classify(pdf_path) -> dict                 # one file's record

CLI:
    python -m eval.fetch --out eval/corpus/real [--limit 60]

classify() takes no network access and never raises -- on anything it cannot
parse it returns verdict "unusable" with a "reason" string.

Politeness, all non-negotiable because this runs unattended:
  - robots.txt is honoured (urllib.robotparser), per host, fetched once.
  - one request at a time per host, >=2s between requests to the same host.
  - a fixed, identifying User-Agent.
  - cached by URL and by content sha256 -- a URL or hash already in the
    manifest is never re-fetched.
  - hard caps: 60 files/run, 20 MB/file, 30 pages/file.
  - every request is timed out; at most 2 retries, with backoff, and only for
    transient errors. A 403 or 429 stops all further fetching from that host
    for the rest of the run.
"""
import argparse
import hashlib
import json
import signal
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pdfplumber
import pypdf

USER_AGENT = (
    "FormFill-research/0.1 "
    "(+PDF form accessibility research; contact ryan.xu282@gmail.com)"
)

REQUEST_TIMEOUT = 20            # seconds, every request
MIN_HOST_DELAY = 2.0            # seconds, minimum gap between requests to one host
MAX_RETRIES = 2                 # retries after the first attempt (never more than 2)
RETRY_BACKOFF = 2.0             # seconds; attempt n waits n * RETRY_BACKOFF

MAX_FILES = 60                  # hard cap: files fetched in one run
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 30
CLASSIFY_TIMEOUT_SECONDS = 20   # safety net against pathological PDFs

CHECK_GLYPHS = {"", ""}   # Webdings box, Wingdings box (same as engine/detect/rules.py)

VERDICTS = (
    "flat-wordlike", "flat-sparse", "fillable-livecycle",
    "fillable-other", "scan", "unusable",
)

# Candidate direct-PDF links on official government domains, gathered by
# searching each domain for downloadable application forms. Not all of these
# will turn out to be flat-wordlike (that is the whole point of classifying
# them) -- some are LiveCycle AcroForms, some are scans. The fetcher records
# whatever it finds, including failures.
SEED_URLS = [
    # gov.bc.ca -- Residential Tenancy Branch forms
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb1_chrome.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb1c.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb2.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb10.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12tpt.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12tct.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12texh.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12tdr.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12lct.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb13.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb51.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb52.pdf",
    # gov.bc.ca -- BC Employment and Assistance forms
    "https://www2.gov.bc.ca/assets/gov/british-columbians-our-governments/policies-for-government/bc-employment-assistance-policy-procedure-manual/forms/pdfs/hr2883.pdf",
    "https://www2.gov.bc.ca/assets/gov/british-columbians-our-governments/policies-for-government/bc-employment-assistance-policy-procedure-manual/forms/pdfs/hr2847.pdf",
    # alberta.ca -- assorted program application forms
    "https://www.alberta.ca/system/files/custom_downloaded_images/tr-tnc-application-form.pdf",
    "https://www.alberta.ca/system/files/custom_downloaded_images/scss-sfa-application-form.pdf",
    "https://www.alberta.ca/system/files/ag-sample-ofep-application-form.pdf",
    "https://www.alberta.ca/system/files/custom_downloaded_images/ahcip-rrnp-flat-fee-application-form.pdf",
    "https://www.alberta.ca/system/files/custom_downloaded_images/CARES-sample-application-form.pdf",
    "https://www.alberta.ca/system/files/custom_downloaded_images/agi-scap-water-program-application-form.pdf",
    "https://www.alberta.ca/system/files/pses-homeowner-tenant-harp-application-form.pdf",
    # ontario.ca -- program application forms
    "https://www.ontario.ca/files/2026-01/rural-ontario-development-community-development-application-en-2026-01-16.pdf",
    "https://www.ontario.ca/files/2025-06/mra-rod-business-development-application-form-en-2025-06-23.pdf",
    "https://www.ontario.ca/files/2024-05/moh-information-guide-application-for-psychiatric-assessment-form-1-en-2024-05-21.pdf",
    "https://files.ontario.ca/mccss-autism-workforce-capacity-fund-sector-innovation-application-form-en-2020-08-10.pdf",
    # canada.ca -- ESDC / student loan forms
    "https://www.canada.ca/content/dam/canada/employment-social-development/migration/documents/assets/portfolio/docs/en/student_loans/forms/SDE0031_EN.pdf",
    "https://www.canada.ca/content/dam/canada/employment-social-development/migration/documents/assets/portfolio/docs/en/student_loans/forms/confirmation_posting-en.pdf",
    "https://www.canada.ca/content/dam/canada/employment-social-development/services/funding/canada-summer-jobs/ESDC-EMP5616_EN.pdf",
    # canada.ca / ircc.canada.ca -- immigration forms
    "https://ircc.canada.ca/english/pdf/kits/forms/IMM5918E.pdf",
    "https://ircc.canada.ca/english/pdf/kits/forms/imm0008egen.pdf",
    "https://www.canada.ca/content/dam/ircc/migration/ircc/english/passport/forms/pdf/pptc190.pdf",
    "https://www.canada.ca/content/dam/ircc/migration/ircc/english/pdf/kits/forms/imm5280e.pdf",
    "https://www.canada.ca/content/dam/ircc/migration/ircc/english/pdf/kits/forms/imm5475e.pdf",
    # ssa.gov -- benefit application forms
    "https://www.ssa.gov/forms/ss-5.pdf",
    "https://www.ssa.gov/forms/ssa-8.pdf",
    "https://www.ssa.gov/forms/ssa-16-bk.pdf",
    "https://www.ssa.gov/forms/ssa-1696.pdf",
    "https://www.ssa.gov/forms/ssa-1-bk.pdf",
    "https://www.ssa.gov/forms/ssa-2-bk.pdf",
    "https://www.ssa.gov/forms/ssa-2490-bk.pdf",
    "https://www.ssa.gov/forms/ss-5fs.pdf",
    "https://www.ssa.gov/legislation/medicare/Part_D_application.pdf",
]


class FetchError(Exception):
    """Raised by _download on any request failure. status is the HTTP code
    when the server responded with one, else None (timeout/connection error)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# classify() -- no network, never raises
# --------------------------------------------------------------------------

class _ClassifyTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise _ClassifyTimeout()


def classify(pdf_path) -> dict:
    """Classify a local PDF file. Pure function of the file's bytes -- no
    network. Never raises: any failure comes back as verdict "unusable" with
    a human-readable "reason"."""
    pdf_path = Path(pdf_path)
    record = _blank_record(pdf_path)

    have_alarm = hasattr(signal, "SIGALRM")
    old_handler = None
    if have_alarm:
        try:
            old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(CLASSIFY_TIMEOUT_SECONDS)
        except Exception:
            have_alarm = False  # e.g. not the main thread -- skip the timeout, keep going
    try:
        return _classify_inner(pdf_path, record)
    except _ClassifyTimeout:
        record["verdict"] = "unusable"
        record["reason"] = f"classification exceeded {CLASSIFY_TIMEOUT_SECONDS}s"
        return record
    except Exception as e:  # belt and suspenders -- classify() must never raise
        record["verdict"] = "unusable"
        record["reason"] = f"unexpected error: {e!r}"
        return record
    finally:
        if have_alarm:
            try:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            except Exception:
                pass


def _blank_record(pdf_path):
    return {
        "file": pdf_path.name,
        "sha256": None,
        "bytes": None,
        "pages": None,
        "producer": None,
        "creator": None,
        "has_acroform": False,
        "widget_count": 0,
        "thin_h_rects": 0,
        "thin_v_rects": 0,
        "checkbox_glyphs": 0,
        "underscore_chars": 0,
        "fonts": [],
        "verdict": "unusable",
        "reason": None,
    }


def _classify_inner(pdf_path, record):
    try:
        data = pdf_path.read_bytes()
    except Exception as e:
        record["reason"] = f"cannot read file: {e}"
        return record

    record["bytes"] = len(data)
    record["sha256"] = hashlib.sha256(data).hexdigest()

    if record["bytes"] == 0:
        record["reason"] = "empty file"
        return record
    if record["bytes"] > MAX_FILE_BYTES:
        record["reason"] = f"exceeds {MAX_FILE_BYTES} byte limit"
        return record

    # ---- structural pass: encryption, page count, AcroForm, metadata ------
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                record["reason"] = "encrypted, no usable password"
                return record

        pages = len(reader.pages)
        record["pages"] = pages
        if pages == 0:
            record["reason"] = "zero pages"
            return record
        if pages > MAX_PAGES:
            record["reason"] = f"exceeds {MAX_PAGES} page limit ({pages} pages)"
            return record

        meta = reader.metadata or {}
        record["producer"] = str(meta.get("/Producer") or "")
        record["creator"] = str(meta.get("/Creator") or "")

        root = reader.trailer.get("/Root") or {}
        acroform = root.get("/AcroForm")
        record["has_acroform"] = bool(acroform)
        if acroform:
            try:
                fields = reader.get_fields()
            except Exception:
                fields = None
            record["widget_count"] = len(fields) if fields else 0
    except Exception as e:
        record["reason"] = f"malformed (pypdf): {e}"
        return record

    # ---- vector/text pass: rects, glyphs, underscores, fonts --------------
    try:
        vrects = hrects = glyphs = underscores = total_chars = 0
        font_counter = Counter()
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                rects = page.rects
                vrects += len([r for r in rects if r["width"] < 3 and r["height"] >= 5])
                hrects += len([r for r in rects if r["height"] < 3 and r["width"] >= 5])
                chars = page.chars
                total_chars += len(chars)
                for c in chars:
                    if c["text"] in CHECK_GLYPHS:
                        glyphs += 1
                    elif c["text"] == "_":
                        underscores += 1
                    fname = c.get("fontname")
                    if fname:
                        font_counter[fname] += 1
        record["thin_v_rects"] = vrects
        record["thin_h_rects"] = hrects
        record["checkbox_glyphs"] = glyphs
        record["underscore_chars"] = underscores
        record["fonts"] = [f for f, _ in font_counter.most_common(5)]
    except Exception as e:
        record["reason"] = f"malformed (pdfplumber): {e}"
        return record

    avg_chars_per_page = total_chars / record["pages"]
    if avg_chars_per_page < 50:
        record["verdict"] = "scan"
        record["reason"] = f"avg {avg_chars_per_page:.1f} chars/page, no real text layer"
        return record

    if record["has_acroform"]:
        signature = f"{record['producer']} {record['creator']}".lower()
        if "designer" in signature or "livecycle" in signature:
            record["verdict"] = "fillable-livecycle"
        else:
            record["verdict"] = "fillable-other"
        record["reason"] = None
        return record

    if record["thin_h_rects"] > 20 and record["thin_v_rects"] > 20:
        record["verdict"] = "flat-wordlike"
    else:
        record["verdict"] = "flat-sparse"
    record["reason"] = None
    return record


# --------------------------------------------------------------------------
# fetch() -- the polite network side
# --------------------------------------------------------------------------

def _fetch_robots_txt(robots_url):
    """Fetch robots.txt as text. Separated out so tests can monkeypatch this
    one function instead of hitting the network."""
    req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def _robots_allowed(robots_cache, url):
    """True if USER_AGENT may fetch url. Caches one RobotFileParser per host.
    An unreachable/missing robots.txt is treated as allow-all, per convention."""
    parsed = urlparse(url)
    host = parsed.netloc
    if host not in robots_cache:
        robots_url = f"{parsed.scheme}://{host}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        try:
            text = _fetch_robots_txt(robots_url)
            rp.parse(text.splitlines())
        except Exception:
            rp = None
        robots_cache[host] = rp
    rp = robots_cache[host]
    if rp is None:
        return True
    return rp.can_fetch(USER_AGENT, url)


def _throttle(last_times, host):
    """Block until at least MIN_HOST_DELAY seconds have passed since the last
    request to this host."""
    now = time.monotonic()
    wait = MIN_HOST_DELAY - (now - last_times.get(host, -1e9))
    if wait > 0:
        time.sleep(wait)
    last_times[host] = time.monotonic()


def _download(url):
    """One HTTP GET, timed out, capped at MAX_FILE_BYTES. Raises FetchError
    on any failure. Separated out so tests can monkeypatch the network."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise FetchError(f"exceeds {MAX_FILE_BYTES} byte cap")
            return data
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}", status=e.code) from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        raise FetchError(str(e)) from e


def _download_with_retries(url):
    """At most MAX_RETRIES retries, with backoff, for transient failures only.
    A 403/429 is never retried -- it is the caller's job to block the host."""
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _download(url)
        except FetchError as e:
            last_exc = e
            if e.status in (403, 429):
                raise
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            raise
    raise last_exc  # pragma: no cover -- loop always returns or raises


def _load_manifest(manifest_path):
    if not manifest_path.exists():
        return {"records": [], "skipped": [], "blocked_hosts": []}
    try:
        data = json.loads(manifest_path.read_text())
    except Exception:
        return {"records": [], "skipped": [], "blocked_hosts": []}
    data.setdefault("records", [])
    data.setdefault("skipped", [])
    data.setdefault("blocked_hosts", [])
    return data


def fetch(urls, out_dir, limit=MAX_FILES) -> dict:
    """Fetch and classify every URL not already cached, up to `limit` new
    files this run. Returns the manifest dict (also written to
    <out_dir>/manifest.json). Never fetches a URL or content hash it already
    holds from a previous run."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    manifest = _load_manifest(manifest_path)
    records = manifest["records"]
    skipped = manifest["skipped"] = []          # this run's skip log only
    blocked_hosts = set(manifest["blocked_hosts"])

    known_urls = {r["url"] for r in records if r.get("url")}
    known_hashes = {r["sha256"] for r in records if r.get("sha256")}

    robots_cache = {}
    last_times = {}
    fetched_this_run = 0

    for url in urls:
        if fetched_this_run >= limit:
            skipped.append({"url": url, "reason": "run limit reached"})
            continue
        if url in known_urls:
            continue  # already held -- never re-fetch

        host = urlparse(url).netloc
        if host in blocked_hosts:
            skipped.append({"url": url, "reason": "host blocked earlier this run"})
            continue

        try:
            if not _robots_allowed(robots_cache, url):
                skipped.append({"url": url, "reason": "disallowed by robots.txt"})
                continue
        except Exception as e:
            skipped.append({"url": url, "reason": f"robots.txt check failed: {e}"})
            continue

        _throttle(last_times, host)
        try:
            data = _download_with_retries(url)
        except FetchError as e:
            if e.status in (403, 429):
                blocked_hosts.add(host)
                skipped.append({"url": url, "reason": f"host blocked after HTTP {e.status}"})
            else:
                skipped.append({"url": url, "reason": f"fetch failed: {e}"})
            continue
        except Exception as e:
            skipped.append({"url": url, "reason": f"fetch failed: {e}"})
            continue

        digest = hashlib.sha256(data).hexdigest()
        if digest in known_hashes:
            skipped.append({"url": url, "reason": "duplicate content already held"})
            continue

        local_path = out_dir / f"{digest[:16]}.pdf"
        local_path.write_bytes(data)

        record = classify(local_path)
        record["url"] = url
        record["file"] = local_path.name
        records.append(record)
        known_urls.add(url)
        known_hashes.add(digest)
        fetched_this_run += 1

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
        "skipped": skipped,
        "blocked_hosts": sorted(blocked_hosts),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    _write_readme(out_dir, manifest)
    return manifest


def _write_readme(out_dir, manifest):
    records = manifest["records"]
    counts = Counter(r["verdict"] for r in records)
    lines = [
        "# Real-world government form corpus",
        "",
        f"Generated {manifest['generated_at']}. {len(records)} files held total.",
        "",
        "## Verdict counts",
        "",
    ]
    for v in VERDICTS:
        lines.append(f"- {v}: {counts.get(v, 0)}")
    lines.append("")

    flat = [r for r in records if r["verdict"] == "flat-wordlike"]
    lines.append(f"## flat-wordlike candidates ({len(flat)})")
    lines.append("")
    lines.append(
        "These are structurally like fixtures/safer.pdf (thin-rect table "
        "borders, Webdings/Wingdings checkbox glyphs, underscore write-on "
        "lines, no AcroForm) and are candidates for hand-labelled ground truth."
    )
    lines.append("")
    if not flat:
        lines.append("None found.")
    for r in flat:
        lines.append(f"- {r.get('url', r['file'])}")
        lines.append(
            f"  - file: {r['file']}, pages: {r['pages']}, producer: {r['producer']!r}"
        )
        lines.append(
            f"  - thin_h_rects={r['thin_h_rects']} thin_v_rects={r['thin_v_rects']} "
            f"checkbox_glyphs={r['checkbox_glyphs']} underscore_chars={r['underscore_chars']}"
        )
    lines.append("")

    others = [r for r in records if r["verdict"] != "flat-wordlike"]
    if others:
        lines.append(f"## Other verdicts ({len(others)})")
        lines.append("")
        for r in others:
            lines.append(
                f"- {r['verdict']}: {r.get('url', r['file'])}"
                + (f" ({r['reason']})" if r.get("reason") else "")
            )
        lines.append("")

    if manifest["skipped"]:
        lines.append(f"## Skipped this run ({len(manifest['skipped'])})")
        lines.append("")
        for s in manifest["skipped"]:
            lines.append(f"- {s['url']}: {s['reason']}")
        lines.append("")

    if manifest["blocked_hosts"]:
        lines.append("## Hosts blocked this run (403/429)")
        lines.append("")
        for h in manifest["blocked_hosts"]:
            lines.append(f"- {h}")
        lines.append("")

    (out_dir / "README.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="eval/corpus/real", help="output directory")
    parser.add_argument("--limit", type=int, default=MAX_FILES, help="max new files this run")
    parser.add_argument(
        "--urls", nargs="*", default=None,
        help="override the built-in seed URL list (mainly for testing)",
    )
    args = parser.parse_args(argv)
    urls = args.urls if args.urls is not None else SEED_URLS
    manifest = fetch(urls, args.out, limit=args.limit)
    counts = Counter(r["verdict"] for r in manifest["records"])
    print(f"fetched {len(manifest['records'])} files this run "
          f"({len(manifest['skipped'])} skipped)")
    for v in VERDICTS:
        print(f"  {v}: {counts.get(v, 0)}")


if __name__ == "__main__":
    main()
