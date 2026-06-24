# reset_attachments.py — wipe GridFS and reset attachment arrays so the
# backfill reruns cleanly with the fixed filename code.
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017")
db  = client["mydb"]
col = db["pst_items"]

# 1. Drop GridFS
before_files  = db["fs.files"].count_documents({})
before_chunks = db["fs.chunks"].count_documents({})
db["fs.files"].drop()
db["fs.chunks"].drop()
print(f"Dropped GridFS: {before_files} files, {before_chunks} chunks")

# 2. Strip gridfs_id / filename / content_type / extracted_text from every
#    attachment sub-document, keeping only {index, size_bytes}.
#    Use an aggregation-pipeline update so we can $map over the array.
result = col.update_many(
    {"has_attachments": True, "attachments.0": {"$exists": True}},
    [{"$set": {
        "attachments": {
            "$map": {
                "input": "$attachments",
                "as": "a",
                "in": {
                    "index":      "$$a.index",
                    "size_bytes": {"$ifNull": ["$$a.size_bytes", 0]},
                }
            }
        },
        "attachment_text": "",
    }}]
)
print(f"Reset {result.modified_count} docs → attachments stripped back to {{index, size_bytes}}")
print("Ready for backfill.")
