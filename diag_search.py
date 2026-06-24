"""Diagnose why PDF content isn't showing up in search."""
from pymongo import MongoClient

db  = MongoClient('mongodb://localhost:27017')['mydb']
col = db['pst_items']

# 1. Check the text index
print("=== Text indexes on pst_items ===")
for idx in col.index_information().values():
    if any(v == 'text' for v in dict(idx.get('key', {})).values()):
        print(f"  name   : {idx['name']}")
        print(f"  key    : {idx['key']}")
        print(f"  weights: {idx.get('weights', {})}")
print()

# 2. Count docs with PDF attachments + attachment_text
total_pdf = col.count_documents(
    {'attachments.filename': {'$regex': r'\.pdf$', '$options': 'i'}}
)
has_text = col.count_documents({
    'attachments.filename': {'$regex': r'\.pdf$', '$options': 'i'},
    'attachment_text': {'$exists': True, '$not': {'$in': [None, '']}}
})
print(f"=== PDF attachment_text population ===")
print(f"  Docs with PDF attachments : {total_pdf}")
print(f"  ...that have attachment_text : {has_text}")
print(f"  ...missing attachment_text   : {total_pdf - has_text}")
print()

# 3. Show a sample doc with PDF attachment + text
sample = col.find_one(
    {'attachments.filename': {'$regex': r'\.pdf$', '$options': 'i'},
     'attachment_text': {'$exists': True, '$not': {'$in': [None, '']}}},
    {'subject': 1, 'attachment_text': 1, 'attachments': 1}
)
if sample:
    print("=== Sample doc with PDF text ===")
    print(f"  subject : {sample.get('subject','')[:80]}")
    fname = next((a['filename'] for a in sample.get('attachments',[])
                  if a.get('filename','').lower().endswith('.pdf')), '?')
    print(f"  pdf file: {fname}")
    print(f"  text len: {len(sample.get('attachment_text',''))} chars")
    print(f"  preview : {sample.get('attachment_text','')[:200]}")
    print()

# 4. Try a $text search for a word likely in a PDF
test_words = ['invoice', 'agreement', 'payment', 'report', 'letter']
print("=== $text search hits per test word ===")
for word in test_words:
    try:
        n = col.count_documents({'$text': {'$search': word}})
        print(f"  '{word}': {n} docs")
    except Exception as e:
        print(f"  '{word}': ERROR — {e}")
