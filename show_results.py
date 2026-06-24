from pymongo import MongoClient

client = MongoClient('mongodb://localhost:27017')
col = client['mydb']['pst_items']

results = list(col.find(
    {"$text": {"$search": "important"}},
    {"score": {"$meta": "textScore"}, "subject": 1, "from_addr": 1, "date": 1}
).sort([("score", {"$meta": "textScore"})]).limit(50))

print(f"{'#':>2}  {'Date':<12}  {'From':<35}  Subject")
print("-" * 110)
for i, d in enumerate(results, 1):
    subj = (d.get("subject") or "(no subject)")[:55]
    frm  = (d.get("from_addr") or "").split("<")[-1].replace(">", "").strip()[:33]
    dt   = str(d.get("date", ""))[:10]
    print(f"{i:>2}. {dt:<12}  {frm:<35}  {subj}")

print(f"\nTotal matching 'important' in database: {col.count_documents({'$text': {'$search': 'important'}})}")
