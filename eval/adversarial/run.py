"""Runs engine.detect.detect() over every file in eval/adversarial/corpus/,
each in its own subprocess with a 30s CPU cap and a 512MB memory cap.

For every file, records exactly one of: ok / timeout / memory / crash / rejected.
Writes eval/adversarial/results.json.

Exit code:
  0  every file was cleanly classified and the parent survived.
  1  something was left unclassified, or the detector returned a result that
     does not match eval/contracts/fields.schema.json (output that looks valid
     but is not -- the one outcome this harness treats as unacceptable).

Usage:
    .venv/bin/python eval/adversarial/generate.py   # build the corpus once
    .venv/bin/python eval/adversarial/run.py
"""
import json
import math
import multiprocessing as mp
import signal
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

CORPUS_DIR = Path(__file__).parent / "corpus"
RESULTS_PATH = Path(__file__).parent / "results.json"
SCHEMA_PATH = REPO_ROOT / "eval" / "contracts" / "fields.schema.json"

CPU_SECONDS = 30
MEMORY_BYTES = 512 * 1024 * 1024
# Wall-clock backstop beyond the CPU cap, for the (rare) case a process is
# blocked rather than burning CPU and so never trips RLIMIT_CPU on its own.
WALL_CLOCK_SECONDS = 40

# Exceptions that mean "the detector looked at this and correctly declined it,"
# as opposed to an unexpected internal failure. pdfplumber wraps essentially
# every pdfminer-level parse failure (bad header, no /Root, truncated, wrong
# password) in PdfminerException, so that single class covers most of the
# malformed/encrypted/truncated/not-a-pdf cases observed in practice.
CLEAN_REJECTION_TYPES = {
    "PdfminerException",       # pdfplumber.utils.exceptions -- wraps pdfminer parse errors
    "PDFSyntaxError",          # pdfminer.pdfparser
    "PDFEncryptionError",      # pdfminer.pdfdocument
    "PDFPasswordIncorrect",    # pdfminer.pdfdocument
    "PDFTextExtractionNotAllowed",
    "PdfReadError",            # pypdf.errors
    "PdfStreamError",          # pypdf.errors
    "ValueError",
    "EOFError",
}


def _set_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    except (ValueError, OSError):
        # macOS/XNU refuses to lower RLIMIT_AS at all ("current limit exceeds
        # maximum limit", even though the current limit is RLIM_INFINITY).
        # Confirmed against this repo's dev machine. RLIMIT_CPU still works
        # there and RLIMIT_AS works on Linux (the real deploy target), so the
        # RSS-polling watchdog below is the cross-platform backstop.
        pass


def _rss_kb(pid):
    """Current resident set size of `pid` in KB, via `ps` (portable, no deps)."""
    import subprocess
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=2)
        val = out.stdout.strip()
        return int(val) if val else None
    except Exception:
        return None


def _memory_watchdog(pid, limit_bytes, stop_event, fired_event, poll_interval=0.2):
    """Runs in a background thread in the parent. Kills `pid` the moment its
    RSS crosses limit_bytes. This is the backstop for platforms (macOS) where
    RLIMIT_AS cannot be lowered at all, so nothing inside the child ever stops it."""
    import os
    import signal as signal_module
    limit_kb = limit_bytes / 1024
    while not stop_event.is_set():
        rss_kb = _rss_kb(pid)
        if rss_kb is not None and rss_kb > limit_kb:
            fired_event.set()
            try:
                os.kill(pid, signal_module.SIGKILL)
            except ProcessLookupError:
                pass
            return
        stop_event.wait(poll_interval)


def _worker(path_str, conn):
    """Runs in the child process. Sends exactly one (kind, payload) message."""
    try:
        _set_limits()
    except Exception:
        pass
    try:
        from engine.detect import detect
        result = detect(path_str)
        conn.send(("result", result))
    except MemoryError:
        conn.send(("memory", "MemoryError raised inside the child process"))
    except BaseException as e:  # noqa: BLE001 - we must classify *everything*
        conn.send(("exception", f"{type(e).__module__}.{type(e).__name__}: {e}"))
    finally:
        conn.close()


def _load_schema():
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text())
    return schema, jsonschema.Draft202012Validator(schema)


def _all_finite(obj):
    if isinstance(obj, float):
        return math.isfinite(obj)
    if isinstance(obj, dict):
        return all(_all_finite(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_all_finite(v) for v in obj)
    return True


def _validate_result(result, expected_pages, validator):
    """Returns (contract_ok, suspicious, note)."""
    errors = sorted(validator.iter_errors(result), key=lambda e: e.path)
    if errors:
        return False, False, f"schema violation: {errors[0].message}"
    if not _all_finite(result):
        return False, False, "non-finite number (NaN/Infinity) in result"

    notes = []
    suspicious = False
    got_pages = len(result.get("pages", []))
    if expected_pages is not None and got_pages != expected_pages:
        suspicious = True
        notes.append(f"expected {expected_pages} pages, detector reported {got_pages}")
    n_fields = len(result.get("fields", []))
    if n_fields > 50_000:
        suspicious = True
        notes.append(f"implausible field count: {n_fields}")
    return True, suspicious, "; ".join(notes)


def run_one(path: Path, validator, expected_pages=None):
    import threading

    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(str(path), child_conn))
    start = time.time()
    proc.start()
    child_conn.close()

    stop_watchdog = threading.Event()
    memory_killed = threading.Event()
    watchdog = threading.Thread(
        target=_memory_watchdog,
        args=(proc.pid, MEMORY_BYTES, stop_watchdog, memory_killed),
        daemon=True,
    )
    watchdog.start()

    msg = None
    if parent_conn.poll(WALL_CLOCK_SECONDS):
        try:
            msg = parent_conn.recv()
        except EOFError:
            msg = None
    proc.join(5)
    elapsed = time.time() - start

    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        stop_watchdog.set()
        watchdog.join(2)
        if memory_killed.is_set():
            return {"status": "memory", "elapsed_s": round(elapsed, 2),
                     "detail": f"RSS crossed {MEMORY_BYTES // (1024*1024)}MB "
                               "(RSS watchdog); force-killed"}
        return {"status": "timeout", "elapsed_s": round(elapsed, 2),
                "detail": f"still running after {WALL_CLOCK_SECONDS}s wall clock; force-killed"}

    stop_watchdog.set()
    watchdog.join(2)
    exitcode = proc.exitcode
    parent_conn.close()

    if msg is not None:
        kind, payload = msg
        if kind == "result":
            ok, suspicious, note = _validate_result(payload, expected_pages, validator)
            record = {
                "status": "ok" if ok else "crash",
                "elapsed_s": round(elapsed, 2),
                "detail": "detect() returned" if ok else note,
                "fields": len(payload.get("fields", [])) if ok else None,
                "pages": len(payload.get("pages", [])) if ok else None,
            }
            if not ok:
                record["contract_violation"] = True
                record["detail"] = f"detect() returned a result that violates fields.schema.json: {note}"
            elif suspicious:
                record["suspicious"] = True
                record["detail"] = note
            return record
        if kind == "memory":
            return {"status": "memory", "elapsed_s": round(elapsed, 2), "detail": payload}
        if kind == "exception":
            cls = payload.split(":", 1)[0].rsplit(".", 1)[-1]
            status = "rejected" if cls in CLEAN_REJECTION_TYPES else "crash"
            return {"status": status, "elapsed_s": round(elapsed, 2), "detail": payload}

    # No message arrived: the child died from a signal before it could send one.
    if memory_killed.is_set():
        return {"status": "memory", "elapsed_s": round(elapsed, 2),
                "detail": f"RSS crossed {MEMORY_BYTES // (1024*1024)}MB (RSS watchdog); "
                          "killed before it could send a result"}
    if exitcode is not None and exitcode < 0:
        sig = -exitcode
        if sig == signal.SIGXCPU:
            return {"status": "timeout", "elapsed_s": round(elapsed, 2),
                    "detail": f"killed by SIGXCPU ({sig}): CPU cap ({CPU_SECONDS}s) exceeded"}
        if sig in (signal.SIGKILL, signal.SIGSEGV):
            return {"status": "memory", "elapsed_s": round(elapsed, 2),
                    "detail": f"killed by signal {sig} "
                              f"({'SIGKILL' if sig == signal.SIGKILL else 'SIGSEGV'}), "
                              f"consistent with the {MEMORY_BYTES // (1024*1024)}MB memory cap "
                              "(RLIMIT_AS, where supported)"}
        return {"status": "crash", "elapsed_s": round(elapsed, 2),
                "detail": f"killed by signal {sig}, no result received"}

    return {"status": "crash", "elapsed_s": round(elapsed, 2),
            "detail": f"child exited with code {exitcode} and sent no result "
                      "(harness gap: should not happen)",
            "harness_error": True}


def main():
    if not CORPUS_DIR.exists() or not any(CORPUS_DIR.glob("*.pdf")):
        print(f"No corpus found in {CORPUS_DIR}. Run generate.py first.", file=sys.stderr)
        return 2

    _, validator = _load_schema()
    manifest_path = CORPUS_DIR / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        for entry in json.loads(manifest_path.read_text()):
            manifest[entry["file"]] = entry

    files = sorted(CORPUS_DIR.glob("*.pdf"))
    results = {}
    for path in files:
        print(f"running {path.name} ...", flush=True)
        expected_pages = manifest.get(path.name, {}).get("expected_pages")
        record = run_one(path, validator, expected_pages)
        results[path.name] = record
        print(f"  -> {record['status']}  ({record['elapsed_s']}s)  {record.get('detail', '')}")

    RESULTS_PATH.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")

    known_statuses = {"ok", "timeout", "memory", "crash", "rejected"}
    failures = []
    for name, record in results.items():
        if record.get("status") not in known_statuses:
            failures.append(f"{name}: unclassified status {record.get('status')!r}")
        if record.get("contract_violation"):
            failures.append(f"{name}: detector returned a schema-invalid 'ok'-looking result")
        if record.get("harness_error"):
            failures.append(f"{name}: harness could not classify the outcome")

    print(f"\n{len(results)} files classified, written to {RESULTS_PATH}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("All files produced a clean classification. Parent process survived all of them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
