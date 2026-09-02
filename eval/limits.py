"""Cross-platform resource caps for running untrusted PDFs in a child process.

Why this module exists: on macOS the kernel refuses to lower RLIMIT_AS at all,
so a child that sets it silently gets NO memory cap. RLIMIT_AS does work on
Linux, which is the deploy target, so it is still set where possible — but an
RSS-polling watchdog in the parent is the backstop that works everywhere.

Discovered by running the adversarial corpus: char_flood.pdf peaked at 1.16 GB,
2.3x the intended 512 MB cap, with the limit "set" and doing nothing.
"""
import subprocess
import threading

MEMORY_MB = 512
CPU_SECONDS = 30


def apply_child_limits(mem_limit_mb=MEMORY_MB, cpu_limit_s=CPU_SECONDS):
    """Call inside the child. Best-effort; the parent watchdog is the real cap."""
    try:
        import resource
    except ImportError:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_limit_s), int(cpu_limit_s)))
    except Exception:
        pass
    try:
        b = int(mem_limit_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (b, b))
    except Exception:
        pass    # macOS refuses; the watchdog covers it


def rss_bytes(pid):
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=2)
        line = out.stdout.strip()
        return int(line) * 1024 if line else None
    except Exception:
        return None


class MemoryWatchdog:
    """Polls a child's RSS and kills it on breach. Use as a context manager."""

    def __init__(self, proc, limit_mb=MEMORY_MB, poll_s=0.2):
        self.proc = proc
        self.limit = int(limit_mb) * 1024 * 1024
        self.poll_s = poll_s
        self._stop = threading.Event()
        self.fired = threading.Event()
        self._thread = None

    def _watch(self):
        while not self._stop.wait(self.poll_s):
            if self.proc.poll() is not None:
                return
            rss = rss_bytes(self.proc.pid)
            if rss is not None and rss > self.limit:
                self.fired.set()
                try:
                    self.proc.kill()
                except Exception:
                    pass
                return

    def __enter__(self):
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        return False
