"""Check actual document structure in MongoDB."""
from pymongo import MongoClient

col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']

print(f"Total docs: {col.count_documents({})}")
print()

# Sample doc - show all top-level keys and attachment structure
doc = col.find_one({})
if doc:
    print("=== Top-level keys in a sample doc ===")
    for k, v in doc.items():
        if k == '_id': continue
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]  first={v[0] if v else 'empty'}")
        elif isinstance(v, str):
            print(f"  {k}: str  '{v[:80]}'")
        else:
            print(f"  {k}: {type(v).__name__}  {str(v)[:80]}")
    print()

# Sample doc that has attachments
doc2 = col.find_one({'has_attachments': True})
if doc2:
    print("=== Doc with has_attachments=True ===")
    print(f"  subject       : {doc2.get('subject','')[:80]}")
    print(f"  attachment_text: {len(str(doc2.get('attachment_text','')))} chars")
    atts = doc2.get('attachments') or []
    print(f"  attachments   : {len(atts)} items")
    for a in atts[:3]:
        print(f"    keys: {list(a.keys())}")
        print(f"    -> {a}")
