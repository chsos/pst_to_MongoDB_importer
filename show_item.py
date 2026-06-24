from pymongo import MongoClient

client = MongoClient('mongodb://localhost:27017')
col = client['mydb']['pst_items']

doc = col.find_one({
    "$text": {"$search": "important garage"},
    "subject": {"$regex": "Important Update on Garage", "$options": "i"},
    "from_addr": {"$regex": "mmiller", "$options": "i"},
    "date": {"$gte": __import__('datetime').datetime(2022, 12, 2)}
})

if doc:
    print("Subject :", doc.get("subject"))
    print("From    :", doc.get("from_addr"))
    print("To      :", ", ".join(doc.get("to_addrs", [])))
    print("CC      :", ", ".join(doc.get("cc_addrs", [])))
    print("Date    :", doc.get("date"))
    print()
    print("--- BODY ---")
    print(doc.get("body_plain", "").strip())
else:
    print("Not found.")
