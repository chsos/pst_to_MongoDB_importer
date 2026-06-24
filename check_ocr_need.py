"""Quick scan: how many PDF files on disk have no extractable text?"""
import os, sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pdfminer.high_level import extract_text

PDF_DIR   = os.path.join(os.path.dirname(__file__), "Attachments", "pdf")
MIN_CHARS = 100      # fewer than this = likely scanned

pdfs = sorted(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
total, has_text, needs_ocr, errors = 0, 0, 0, 0

for fname in pdfs:
    total += 1
    path = os.path.join(PDF_DIR, fname)
    try:
        text = (extract_text(path) or "").strip()
        if len(text) >= MIN_CHARS:
            has_text += 1
        else:
            needs_ocr += 1
            if needs_ocr <= 5:          # show first 5 examples
                print(f"  needs OCR: {fname}  ({len(text)} chars extracted)")
    except Exception as e:
        errors += 1
        if errors <= 3:
            print(f"  error: {fname}: {e}")

print()
print(f"Total PDFs      : {total}")
print(f"Already has text: {has_text}")
print(f"Needs OCR       : {needs_ocr}")
print(f"Errors          : {errors}")
