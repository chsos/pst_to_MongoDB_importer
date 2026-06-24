# export_attachments.py
# Exports all attachments from GridFS to the local Attachments folder tree.
#
# Usage:
#   .venv\Scripts\python.exe export_attachments.py
#   .venv\Scripts\python.exe export_attachments.py --filter pdf        # only PDFs
#   .venv\Scripts\python.exe export_attachments.py --filter Excel      # only Excel
#   .venv\Scripts\python.exe export_attachments.py --filter Word       # only Word

import sys, os, argparse
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pymongo import MongoClient
import gridfs as gridfs_module

MONGO_URI  = "mongodb://localhost:27017"
DB_NAME    = "mydb"
BASE_DIR   = os.path.join(os.path.dirname(__file__), "Attachments")

EXT_FOLDER = {
    ".pdf":  "pdf",
    ".doc":  "Word",  ".docx": "Word",  ".rtf": "Word",  ".odt": "Word",
    ".xls":  "Excel", ".xlsx": "Excel", ".csv": "Excel",
    ".tsv":  "Excel", ".ods":  "Excel",
    ".ppt":  "PowerPoint", ".pptx": "PowerPoint", ".odp": "PowerPoint",
    ".mp4":  "Videos", ".avi":  "Videos", ".mov":  "Videos",
    ".wmv":  "Videos", ".mkv":  "Videos", ".flv":  "Videos",
    ".webm": "Videos", ".m4v":  "Videos", ".mpeg": "Videos",
    ".mpg":  "Videos", ".3gp":  "Videos",
    ".jpg":  "Images", ".jpeg": "Images", ".png": "Images",
    ".gif":  "Images", ".bmp":  "Images", ".tiff": "Images",
    ".tif":  "Images", ".svg":  "Images", ".webp": "Images",
    ".txt":  "Text",  ".log":  "Text",  ".xml":  "Text",
    ".json": "Text",  ".html": "Text",  ".htm":  "Text",
}

def folder_for(filename):
    ext = os.path.splitext(filename or "")[1].lower()
    return EXT_FOLDER.get(ext, "Other")

def safe_dest(folder_path, filename):
    """Return a non-colliding file path."""
    dest = os.path.join(folder_path, filename)
    if not os.path.exists(dest):
        return dest
    base, ext = os.path.splitext(filename)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(folder_path, f"{base}_{n}{ext}")
        n += 1
    return dest

def main():
    ap = argparse.ArgumentParser(description="Export GridFS attachments to local folders")
    ap.add_argument("--mongo",  default=MONGO_URI)
    ap.add_argument("--db",     default=DB_NAME)
    ap.add_argument("--filter", default=None,
                    help="Only export files whose folder matches (e.g. pdf, Word, Excel, Images)")
    ap.add_argument("--out",    default=BASE_DIR,
                    help=f"Root output directory (default: {BASE_DIR})")
    args = ap.parse_args()

    client = MongoClient(args.mongo, serverSelectionTimeoutMS=5000)
    fs     = gridfs_module.GridFS(client[args.db])

    total = saved = skipped = errors = 0
    counts = {}

    for grid_out in fs.find():
        total += 1
        try:
            filename = grid_out.filename or "attachment"
            subfolder = folder_for(filename)

            if args.filter and subfolder.lower() != args.filter.lower():
                skipped += 1
                continue

            dest_dir = os.path.join(args.out, subfolder)
            os.makedirs(dest_dir, exist_ok=True)

            dest = safe_dest(dest_dir, filename)
            with open(dest, "wb") as fh:
                fh.write(grid_out.read())

            saved += 1
            counts[subfolder] = counts.get(subfolder, 0) + 1
            print(f"  [{subfolder:12s}] {filename}", flush=True)

        except Exception as e:
            errors += 1
            print(f"  ERROR {getattr(grid_out, 'filename', '?')}: {e}", flush=True)

    print()
    print("=" * 50)
    print(f"GridFS files found : {total}")
    print(f"Saved              : {saved}")
    print(f"Skipped (filter)   : {skipped}")
    print(f"Errors             : {errors}")
    print()
    for folder, count in sorted(counts.items()):
        print(f"  {folder:15s}: {count}")
    print("=" * 50)

if __name__ == "__main__":
    main()
