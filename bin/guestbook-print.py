#!/usr/bin/env python3
"""Print queued guest book reviews from the guest book Worker.

Polls `guestbook.joshbeckman.org` for reviews that have not been printed, renders each
one's print sheet to a PDF with headless Chromium, hands it to CUPS, and marks
the review printed only once the job has actually left the print queue (so a
printer that is switched off just means "try again next minute", not "printed").

    GUESTBOOK_KEY=<from the s store> guestbook-print.py
    guestbook-print.py --dry-run          # render PDFs, print nothing
    guestbook-print.py --reprint b7b9da36 # requeue + print one review
    guestbook-print.py --watch 60         # loop instead of a systemd timer

Run normally by the guestbook-print.timer systemd user unit, which supplies
GUESTBOOK_KEY from the encrypted `s` store (see memory: hosts/blueberry.md).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = os.environ.get("GUESTBOOK_URL", "https://guestbook.joshbeckman.org")
QUEUE = os.environ.get("GUESTBOOK_QUEUE", "guestroom")
CHROME = shutil.which("chromium") or "/usr/bin/chromium"
STATE = Path.home() / ".local/state/guestbook/state.json"
LOCK = Path.home() / ".local/state/guestbook/lock"
STALE_AFTER = 45 * 60  # seconds a job may sit in the CUPS queue before we give up on it

KEY = os.environ.get("GUESTBOOK_KEY", "")
if not KEY:
    sys.exit("GUESTBOOK_KEY is not set (expected the guestbook-print unit to pass it from `s`)")

# Black and white by default: this is a cheap inkjet and a keepsake page does not
# need color. Set GUESTBOOK_PRINT_COLOR=color to go back.
INK = os.environ.get("GUESTBOOK_PRINT_COLOR", "monochrome")
MEDIA = ["-o", "media=na_letter_8.5x11in", "-o", "cupsPrintQuality=Normal",
         f"-o", f"print-color-mode={INK}", "-o", "fit-to-page"]

# A review must never come out as two pages: shrink the sheet until it fits.
SCALES = [1.0, 0.94, 0.88, 0.82, 0.76, 0.7]


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def api(path: str, method: str = "GET") -> bytes:
    url = f"{BASE}{path}{'&' if '?' in path else '?'}key={urllib.parse.quote(KEY)}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def sheet_url(review_id: str, scale: float = 1.0) -> str:
    return (f"{BASE}/sheet/{review_id}?key={urllib.parse.quote(KEY)}"
            f"&scale={scale}")


def page_count(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 0


# ------------------------------------------------------------------ state

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {"pending": {}}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE)


# ----------------------------------------------------------------- queues

def queued_jobs() -> dict[str, str]:
    """Map of job id -> status line, for jobs still in the CUPS queue."""
    out = subprocess.run(["lpstat", "-o", QUEUE], capture_output=True, text=True).stdout
    jobs = {}
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].startswith(f"{QUEUE}-"):
            jobs[parts[0]] = line
    return jobs


def submit(pdf: Path) -> str:
    res = subprocess.run(["lp", "-d", QUEUE, *MEDIA, str(pdf)],
                         capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"lp failed: {res.stderr.strip() or res.stdout.strip()}")
    # "request id is guestroom-42 (1 file(s))"
    job = res.stdout.split("request id is", 1)[-1].strip().split()[0]
    log(f"  queued as {job}")
    return job


# ---------------------------------------------------------------- rendering

def render(review_id: str, outdir: Path) -> Path:
    """Render the sheet, shrinking it until it is a single page."""
    for scale in SCALES:
        pdf = outdir / f"{review_id}-{scale}.pdf"
        profile = outdir / f"chrome-{scale}"
        res = subprocess.run([
            CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
            "--no-pdf-header-footer", "--disable-extensions", "--disable-background-networking",
            f"--user-data-dir={profile}", f"--print-to-pdf={pdf}", sheet_url(review_id, scale),
        ], capture_output=True, text=True, timeout=180)
        if not pdf.exists() or pdf.stat().st_size < 15_000:
            raise RuntimeError(f"chromium produced no usable PDF for {review_id}: "
                               f"{res.stderr.strip()[-400:]}")
        n = page_count(pdf)
        if n == 1:
            if scale != 1.0:
                log(f"  long review, sheet scaled to {scale}")
            return pdf
        log(f"  {n} pages at scale {scale}, shrinking")
    # Still too long even at the smallest scale: print the first page only and
    # say so, rather than taping two sheets to the wall.
    first = outdir / f"{review_id}-page1.pdf"
    subprocess.run(["pdfseparate", "-f", "1", "-l", "1", str(pdf), str(first)],
                   capture_output=True, text=True, timeout=60)
    if first.exists():
        log(f"  WARNING: review {review_id} is too long for one page; printing page 1 only")
        return first
    return pdf


# -------------------------------------------------------------------- work

def resolve_pending(state: dict) -> None:
    """Mark reviews printed once their CUPS job has left the queue."""
    jobs = queued_jobs()
    for review_id, rec in list(state["pending"].items()):
        age = time.time() - rec["at"]
        if rec["job"] in jobs:
            if age > STALE_AFTER:
                log(f"{review_id}: job {rec['job']} stuck {age / 60:.0f}m, cancelling to retry later")
                subprocess.run(["cancel", rec["job"]], capture_output=True, text=True)
                del state["pending"][review_id]
                save_state(state)
            continue
        api(f"/printed/{review_id}", method="POST")
        log(f"{review_id}: printed ({rec['job']})")
        del state["pending"][review_id]
        save_state(state)


def print_new(state: dict, dry_run: bool) -> int:
    items = json.loads(api("/queue"))
    if not items:
        return 0
    count = 0
    for item in items:
        review_id = item["id"]
        if review_id in state["pending"]:
            continue
        log(f"{review_id}: {item['name']}")
        with tempfile.TemporaryDirectory(prefix="guestbook-") as td:
            try:
                pdf = render(review_id, Path(td))
            except Exception as e:  # noqa: BLE001 - one bad sheet must not stop the rest
                log(f"  render failed: {e}")
                continue
            if dry_run:
                keep = Path(f"/tmp/guestbook-{review_id}.pdf")
                shutil.copy(pdf, keep)
                log(f"  dry run, PDF at {keep}")
                count += 1
                continue
            try:
                job = submit(pdf)
            except Exception as e:  # noqa: BLE001
                log(f"  print failed: {e}")
                continue
        state["pending"][review_id] = {"job": job, "at": time.time(),
                                       "name": item["name"]}
        save_state(state)
        count += 1
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="render to /tmp, print nothing")
    ap.add_argument("--watch", type=int, metavar="SECONDS", help="loop forever")
    ap.add_argument("--reprint", metavar="REVIEW_ID", help="requeue one review and print it")
    args = ap.parse_args()

    STATE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # another run is mid-flight

    if args.reprint:
        api(f"/printed/{args.reprint}?undo=1", method="POST")
        state = load_state()
        state["pending"].pop(args.reprint, None)
        save_state(state)
        log(f"{args.reprint}: requeued")

    while True:
        state = load_state()
        try:
            resolve_pending(state)
            n = print_new(state, args.dry_run)
            if n == 0 and not args.watch:
                log("nothing to print")
        except Exception as e:  # noqa: BLE001 - keep the timer alive through transient errors
            log(f"error: {type(e).__name__}: {e}")
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
