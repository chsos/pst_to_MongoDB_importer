"""Find a patched doc and print what /email/<id> returns for attachments."""
import sys, json, urllib.request
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pymongo import MongoClient
col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']

# Find a doc that has been backfilled
doc = col.find_one(
    {"attachments.gridfs_id": {"$exists": True, "$ne": None}},
    {"_id": 1, "subject": 1, "attachments": 1}
)
if not doc:
    print("No backfilled docs found yet")
    sys.exit()

doc_id = doc["_id"]
print(f"Doc _id  : {doc_id}")
print(f"Subject  : {doc.get('subject','')[:60]}")
print(f"Attachments in MongoDB:")
for a in doc.get("attachments", []):
    print(f"  {a}")

print()
print("=== What /email/<id> returns ===")
url = f"http://localhost:5000/email/{doc_id}"
with urllib.request.urlopen(url, timeout=10) as r:
    data = json.loads(r.read())
print(f"_id: {data.get('_id')}")
print(f"Attachments from API:")
for a in data.get("attachments", []):
    print(f"  {a}")
