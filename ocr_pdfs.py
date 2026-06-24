# ocr_pdfs.py
#
# Scans Attachments\pdf\, finds scanned PDFs (no extractable text), OCRs them
# with ocrmypdf (adds a real text layer so you can select/copy in any viewer),
# then updates MongoDB so those PDFs show up in the main search.
#
# ── Prerequisites ────────────────────────────────────────────────────────────
#  1. Tesseract OCR (the engine)
#       Download the Windows installer from:
#       https://github.com/UB-Mannheim/tesseract/wiki
#       → run the installer, tick "Add to PATH", keep English data selected.
#       Verify with:  tesseract --version
#
#  2. ocrmypdf Python package  (already installed if you saw this message)
#       .venv\Scripts\pip.exe install ocrmypdf
#
#  Ghostscript is optional — ocrmypdf will use it if found, but works without
#  it for standard PDFs.
#
# ── Usage ────────────────────────────────────────────────────────────────────
#   .venv\Scripts\python.exe ocr_pdfs.py              # process all
#   .venv\Scripts\python.exe ocr_pdfs.py --dry-run    # detect only, no changes
#   .venv\Scripts\python.exe ocr_pdfs.py --min-chars 200  # stricter threshold
#   .venv\Scripts\python.exe ocr_pdfs.py --verbose    # show skipped files too
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pdfminer.high_level import extract_text as _pdfminer_extract
from pymongo import MongoClient

MONGO_URI  = "mongodb://localhost:27017"
DB_NAME    = "mydb"
COLLECTION = "pst_items"
ATTACH_DIR = os.path.join(os.path.dirname(__file__), "Attachments")
PDF_DIR    = os.path.join(ATTACH_DIR, "pdf")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_text(pdf_path: str, min_chars: int) -> bool:
    """Return True if pdfminer can extract >= min_chars of real text."""
    try:
        t = (_pdfminer_extract(pdf_path) or "").strip()
        return len(t) >= min_chars
    except Exception:
        return False


def _extract_text(pdf_path: str) -> str:
    try:
        return (_pdfminer_extract(pdf_path) or "").strip()
    except Exception:
        return ""


def _ocr_pdf(src: str, dst: str) -> tuple[bool, str]:
    """
    Run ocrmypdf on src → dst.
    --skip-text  : leave pages that already have a text layer alone
    --force-ocr  : override on pages with no text (vs --redo-ocr which always re-runs)
    Returns (success, error_message).
    """
    cmd = [
        sys.executable, "-m", "ocrmypdf",
        "--skip-text",           # keep existing text layers, OCR image pages only
        "--output-type", "pdf",  # plain PDF (not PDF/A – no Ghostscript needed)
        "--jobs", "1",           # one thread per file; raise if your CPU has cores to spare
        "--quiet",
        src, dst,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        # ocrmypdf exit codes: 0=ok, 6=already has text (treated as ok here)
        if result.returncode in (0, 6):
            return True, ""
        msg = (result.stderr or result.stdout or "").strip()[-300:]
        return False, msg
    except subprocess.TimeoutExpired:
        return False, "timeout after 5 min"
    except Exception as exc:
        return False, str(exc)


def _update_mongo(col, fname: str, ocr_text: str, dry_run: bool) -> int:
    """
    Find MongoDB docs with an attachment matching fname whose attachment_text
    is currently short (likely unextracted / scanned). Append the OCR text.
    Returns the number of docs updated.
    """
    if not ocr_text:
        return 0

    # Find docs that have this filename in their attachments array
    docs = list(col.find(
        {"attachments.filename": fname},
        {"_id": 1, "attachment_text": 1},
    ))

    if not docs:
        return 0

    updated = 0
    for doc in docs:
        existing = (doc.get("attachment_text") or "").strip()
        # Skip if existing text is already substantial relative to what we extracted
        if existing and len(existing) >= min(len(ocr_text) * 0.5, 300):
            continue
        combined = (existing + ("\n\n" if existing else "") + ocr_text)[:50_000]
        if not dry_run:
            col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"attachment_text": combined}},
            )
        updated += 1

    return updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="OCR scanned PDFs in Attachments\\pdf\\ and update MongoDB"
    )
    ap.add_argument("--pdf-dir",   default=PDF_DIR,
                    help=f"Folder to scan (default: {PDF_DIR})")
    ap.add_argument("--mongo",     default=MONGO_URI)
    ap.add_argument("--db",        default=DB_NAME)
    ap.add_argument("--col",       default=COLLECTION)
    ap.add_argument("--min-chars", type=int, default=100,
                    help="Chars of extractable text needed to consider a PDF already OK (default: 100)")
    ap.add_argument("--dry-run",   action="store_true",
                    help="Detect scanned PDFs but do NOT OCR or update MongoDB")
    ap.add_argument("--verbose",   action="store_true",
                    help="Also print files that already have text")
    args = ap.parse_args()

    # ── Check Tesseract ──────────────────────────────────────────────────────
    try:
        r = subprocess.run(["tesseract", "--version"],
                           capture_output=True, text=True, timeout=10)
        tess_ver = r.stdout.splitlines()[0] if r.returncode == 0 else "unknown"
        print(f"Tesseract   : {tess_ver}")
    except FileNotFoundError:
        print()
        print("ERROR: Tesseract not found on PATH.")
        print()
        print("  Install from: https://github.com/UB-Mannheim/tesseract/wiki")
        print("  During install: tick 'Add to PATH' and keep English selected.")
        print("  After install, open a NEW terminal and re-run this script.")
        print()
        sys.exit(1)

    # ── Check ocrmypdf ───────────────────────────────────────────────────────
    try:
        subprocess.run([sys.executable, "-m", "ocrmypdf", "--version"],
                       capture_output=True, check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: ocrmypdf not available.")
        print("  .venv\\Scripts\\pip.exe install ocrmypdf")
        sys.exit(1)

    pdf_dir = os.path.abspath(args.pdf_dir)
    if not os.path.isdir(pdf_dir):
        print(f"ERROR: PDF folder not found: {pdf_dir}")
        sys.exit(1)

    col = MongoClient(
        args.mongo, serverSelectionTimeoutMS=5_000
    )[args.db][args.col]

    pdfs = sorted(
        f for f in os.listdir(pdf_dir)
        if f.lower().endswith(".pdf") and
           os.path.isfile(os.path.join(pdf_dir, f))
    )

    stats = {
        "total":         len(pdfs),
        "already_ok":    0,
        "needs_ocr":     0,
        "ocr_done":      0,
        "ocr_failed":    0,
        "mongo_updated": 0,
        "errors":        0,
    }

    print(f"PDF folder  : {pdf_dir}")
    print(f"Total PDFs  : {stats['total']}")
    print(f"Min chars   : {args.min_chars}")
    print(f"Dry run     : {args.dry_run}")
    print()

    for idx, fname in enumerate(pdfs, 1):
        fpath   = os.path.join(pdf_dir, fname)
        label   = f"[{idx:>4}/{stats['total']}]"
        display = fname if len(fname) <= 55 else fname[:52] + "…"

        # ── Fast check: does this PDF already have text? ─────────────────────
        if _has_text(fpath, args.min_chars):
            stats["already_ok"] += 1
            if args.verbose:
                print(f"{label} OK      {display}")
            continue

        stats["needs_ocr"] += 1
        print(f"{label} OCR→   {display}", end="", flush=True)

        if args.dry_run:
            print("  (dry-run, skipping)")
            continue

        # ── OCR ──────────────────────────────────────────────────────────────
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(tmp_fd)
        try:
            ok, err_msg = _ocr_pdf(fpath, tmp_path)

            if not ok or not os.path.getsize(tmp_path):
                print(f"  FAILED — {err_msg or 'empty output'}")
                stats["ocr_failed"] += 1
                continue

            # Extract the OCR'd text
            ocr_text = _extract_text(tmp_path)
            char_count = len(ocr_text)

            # Replace original with the searchable version
            shutil.move(tmp_path, fpath)
            stats["ocr_done"] += 1

            # Update MongoDB
            n = _update_mongo(col, fname, ocr_text, args.dry_run)
            stats["mongo_updated"] += n

            print(f"  {char_count:>7,} chars  →  {n} doc(s) updated")

        except Exception as exc:
            print(f"  ERROR: {exc}")
            stats["errors"] += 1

        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print("=" * 58)
    print("OCR run complete.")
    print(f"  Total PDFs           : {stats['total']}")
    print(f"  Already had text     : {stats['already_ok']}")
    print(f"  Detected as scanned  : {stats['needs_ocr']}")
    if not args.dry_run:
        print(f"  OCR'd successfully   : {stats['ocr_done']}")
        print(f"  OCR failed           : {stats['ocr_failed']}")
        print(f"  MongoDB docs updated : {stats['mongo_updated']}")
    print("=" * 58)


if __name__ == "__main__":
    main()
