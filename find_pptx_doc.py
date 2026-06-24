from pymongo import MongoClient
col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']

doc = col.find_one(
    {"attachments.filename": {"$regex": r"\.pptx?$", "$options": "i"}},
    {"_id": 1, "subject": 1, "folder_path": 1, "item_index": 1,
     "from_addr": 1, "date": 1, "attachments": 1}
)
if doc:
    print(f"_id        : {doc['_id']}")
    print(f"subject    : {doc.get('subject','')}")
    print(f"folder_path: {doc.get('folder_path','')}")
    print(f"item_index : {doc.get('item_index')}")
    print(f"from_addr  : {doc.get('from_addr','')}")
    print(f"date       : {doc.get('date','')}")
    for a in doc.get("attachments", []):
        print(f"attachment : {a}")
