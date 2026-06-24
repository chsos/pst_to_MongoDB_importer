# verify_att.py — find a patched attachment and test the download endpoint
import sys
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    HAS_REQUESTS = False
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pymongo import MongoClient
col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']

# Find a doc that has been fully backfilled (has gridfs_id + filename with extension)
docs = col.find(
    {"attachments.gridfs_id": {"$exists": True, "$ne": None}},
    {"subject": 1, "attachments": 1}
).limit(10)

for doc in docs:
    for att in doc.get("attachments", []):
        gid  = att.get("gridfs_id")
        name = att.get("filename", "")
        if gid and "." in name:
            print(f"Subject  : {doc.get('subject','')[:60]}")
            print(f"Filename : {name}")
            print(f"GridFS ID: {gid}")
            print(f"Size     : {att.get('size_bytes',0):,} bytes")
            print(f"MIME     : {att.get('content_type','')}")

            # Test the Flask download endpoint
            url = f"http://localhost:5000/attachment/{gid}"
            try:
                if HAS_REQUESTS:
                    r  = requests.get(url, timeout=10)
                    cd = r.headers.get("Content-Disposition","")
                    ct = r.headers.get("Content-Type","")
                    body_len = len(r.content)
                else:
                    with urllib.request.urlopen(url, timeout=10) as r:
                        cd = r.headers.get("Content-Disposition","")
                        ct = r.headers.get("Content-Type","")
                        body_len = len(r.read())
                print(f"HTTP     : 200")
                print(f"Content-Type       : {ct}")
                print(f"Content-Disposition: {cd}")
                print(f"Body bytes received: {body_len:,}")
            except Exception as e:
                print(f"Request error: {e}")
            print()
            break
    else:
        continue
    break
