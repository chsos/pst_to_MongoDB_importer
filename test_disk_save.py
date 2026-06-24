import sys, os, glob
sys.path.insert(0, os.path.dirname(__file__))
from pst_to_mongodb import _save_to_disk

base  = os.path.join(os.path.dirname(__file__), "Attachments")
tests = [
    ("invoice.pdf",   "pdf"),
    ("budget.xlsx",   "Excel"),
    ("contract.docx", "Word"),
    ("report.xls",    "Excel"),
    ("memo.doc",      "Word"),
    ("photo.jpg",     None),     # should be skipped
]
print("Testing _save_to_disk routing:\n")
saved = []
for fname, expected_sub in tests:
    result = _save_to_disk(b"test data", fname, base)
    if expected_sub:
        actual_sub = os.path.basename(os.path.dirname(result)) if result else None
        ok = actual_sub == expected_sub
        print(f"  {fname:20s} -> {os.path.relpath(result, base) if result else 'None':30s}  {'OK' if ok else 'FAIL'}")
        if result: saved.append(result)
    else:
        ok = result is None
        print(f"  {fname:20s} -> {'(skipped — not in target list)':30s}  {'OK' if ok else 'FAIL'}")

# Clean up test files
for f in saved:
    if os.path.exists(f) and open(f, "rb").read() == b"test data":
        os.remove(f)
print("\nTest files cleaned up.")
