"""Test the /save-attachment endpoint with a real GridFS file."""
import sys, json, urllib.request
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pymongo import MongoClient
col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']

doc = col.find_one({"attachments.gridfs_id": {"$exists": True, "$ne": None}}, {"attachments": 1, "subject": 1})
att = next((a for a in doc.get("attachments",[]) if a.get("gridfs_id")), None)
print(f"Testing with: {doc.get('subject','')[:50]}")
print(f"Attachment  : {att.get('filename')}  ({att.get('size_bytes',0):,} bytes)")

req = urllib.request.Request(f"http://localhost:5000/save-attachment/{att['gridfs_id']}", method="POST")
with urllib.request.urlopen(req, timeout=10) as r:
    d = json.loads(r.read())
print(f"Response    : {d}")
