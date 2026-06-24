# backfill_attachments.py
#
# Walks a PST file and fills in GridFS attachment binaries for documents
# that were imported BEFORE binary storage was added.
# Safe to re-run: docs that already have gridfs_id on every attachment
# are skipped.
#
# Usage:
#   .venv\Scripts\python.exe backfill_attachments.py --pst "pst_files\backup112922.pst"

import argparse
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Reuse all the heavy lifting from pst_to_mongodb.py
from pst_to_mongodb import (
    make_id, safe_str, extract_attachments, extract_text_from_bytes,
)

import pypff
from pymongo import MongoClient, UpdateOne
import gridfs as gridfs_module

MONGO_URI  = "mongodb://localhost:27017"
DB_NAME    = "mydb"
COLLECTION = "pst_items"


def needs_backfill(doc):
    """Return True if any attachment is missing a gridfs_id (or has no filename)."""
    atts = doc.get("attachments") or []
    if not atts:
        return False
    return any(not a.get("gridfs_id") for a in atts)


def backfill_message(col, fs, folder_path, index, message, stats, verbose):
    email_id = make_id(folder_path, index)

    # Only process docs that exist and still need backfilling
    doc = col.find_one({"_id": email_id}, {"attachments": 1, "has_attachments": 1})
    if doc is None:
        return
    if not needs_backfill(doc):
        return

    stats["checked"] += 1
    try:
        atts = extract_attachments(message, email_id, fs)
    except Exception as e:
        print(f"  ERROR extracting {email_id[:12]}…: {e}", flush=True)
        stats["errors"] += 1
        return

    if not atts:
        return

    stored = sum(1 for a in atts if a.get("gridfs_id"))
    if stored == 0:
        return  # nothing stored (e.g. all zero-byte attachments)

    # Rebuild attachment_text from extracted texts
    attachment_text = "\n\n".join(
        a["extracted_text"] for a in atts if a.get("extracted_text")
    )[:50_000]

    col.update_one(
        {"_id": email_id},
        {"$set": {
            "attachments":     atts,
            "attachment_text": attachment_text,
        }}
    )
    stats["patched"] += 1
    stats["files"]   += stored
    if verbose:
        subject = doc.get("subject", "")
        print(f"  Patched {email_id[:12]}… {stored} att  {subject[:50]}", flush=True)


def walk_folder(folder, path, col, fs, stats, verbose):
    folder_name  = safe_str(folder.name, fallback="(unnamed)")
    current_path = (path + "/" + folder_name) if path else folder_name

    for i in range(folder.number_of_sub_messages):
        try:
            message = folder.get_sub_message(i)
            backfill_message(col, fs, current_path, i, message, stats, verbose)
        except Exception as e:
            print(f"  WARNING: message {i} in '{current_path}': {e}", flush=True)
            stats["errors"] += 1

        stats["scanned"] += 1
        if stats["scanned"] % 500 == 0:
            print(f"  Scanned {stats['scanned']:>6}  |  "
                  f"need_backfill={stats['checked']}  "
                  f"patched={stats['patched']}  "
                  f"files={stats['files']}  "
                  f"errors={stats['errors']}",
                  flush=True)

    for i in range(folder.number_of_sub_folders):
        try:
            sub = folder.get_sub_folder(i)
            walk_folder(sub, current_path, col, fs, stats, verbose)
        except Exception as e:
            print(f"  WARNING: sub-folder {i} of '{current_path}': {e}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Backfill GridFS attachments for existing PST imports")
    ap.add_argument("--pst",     required=True, help="Path to the .pst file")
    ap.add_argument("--mongo",   default=MONGO_URI)
    ap.add_argument("--db",      default=DB_NAME)
    ap.add_argument("--col",     default=COLLECTION)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    pst_path = os.path.abspath(args.pst)
    if not os.path.isfile(pst_path):
        print(f"ERROR: File not found: {pst_path}")
        sys.exit(1)

    client = MongoClient(args.mongo, serverSelectionTimeoutMS=5000)
    col    = client[args.db][args.col]
    fs     = gridfs_module.GridFS(client[args.db])

    total_need = col.count_documents(
        {"has_attachments": True,
         "attachments": {"$elemMatch": {"gridfs_id": {"$in": [None, False, ""]}}}}
    )
    print(f"PST        : {pst_path}")
    print(f"MongoDB    : {args.mongo}  db={args.db}  col={args.col}")
    print(f"Need fill  : {total_need} docs have attachments but no gridfs_id")
    print()

    if total_need == 0:
        print("Nothing to do — all attachments already have gridfs_id.")
        return

    pst_file = pypff.file()
    pst_file.open(pst_path)
    root  = pst_file.get_root_folder()
    stats = {"scanned": 0, "checked": 0, "patched": 0, "files": 0, "errors": 0}

    print("Walking PST…\n")
    for i in range(root.number_of_sub_folders):
        try:
            top = root.get_sub_folder(i)
            walk_folder(top, "", col, fs, stats, args.verbose)
        except Exception as e:
            print(f"WARNING: top-level folder {i}: {e}", flush=True)

    pst_file.close()

    print()
    print("=" * 50)
    print("Backfill complete.")
    print(f"  Messages scanned   : {stats['scanned']}")
    print(f"  Docs needing fill  : {stats['checked']}")
    print(f"  Docs patched       : {stats['patched']}")
    print(f"  Attachment files   : {stats['files']}")
    print(f"  Errors             : {stats['errors']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
