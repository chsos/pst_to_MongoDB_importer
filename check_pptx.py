from pymongo import MongoClient
col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']
docs = col.find(
    {"attachments.filename": {"$regex": r"\.(ppt|pptx)$", "$options": "i"}},
    {"subject": 1, "attachments": 1}
)
count = 0
for doc in docs:
    for a in doc.get("attachments", []):
        fn = a.get("filename", "")
        if fn.lower().endswith((".ppt", ".pptx")):
            print(f"  {fn:50s}  gridfs_id={a.get('gridfs_id')}")
            count += 1
print(f"\nTotal PowerPoint attachments in DB: {count}")
