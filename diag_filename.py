"""Quick check: print filenames the fixed _att_filename returns for a few messages."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pypff
from pymongo import MongoClient
from pst_to_mongodb import safe_str, make_id, _att_filename, _att_mime

col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']
PST = r"C:\Users\andy\PycharmProjects\pst_to_MongoDB_importer\pst_files\backup112922.pst"

def walk(folder, path, found, limit=5):
    fn = safe_str(folder.name, fallback="(unnamed)")
    cp = (path + "/" + fn) if path else fn
    for i in range(folder.number_of_sub_messages):
        if len(found) >= limit: return
        try:
            msg = folder.get_sub_message(i)
            if msg.number_of_attachments == 0: continue
            eid = make_id(cp, i)
            doc = col.find_one({"_id": eid, "has_attachments": True}, {"subject": 1})
            if not doc: continue
            found.append(eid)
            print(f"\nMsg: {doc.get('subject','')[:60]}")
            for ai in range(msg.number_of_attachments):
                att  = msg.get_attachment(ai)
                name = _att_filename(att, ai)
                mime = _att_mime(att, name)
                print(f"  [{ai}] filename={name!r:40s}  mime={mime}")
        except: pass
    for i in range(folder.number_of_sub_folders):
        if len(found) >= limit: return
        try: walk(folder.get_sub_folder(i), cp, found, limit)
        except: pass

pf = pypff.file(); pf.open(PST)
root = pf.get_root_folder()
found = []
for i in range(root.number_of_sub_folders):
    walk(root.get_sub_folder(i), "", found, 5)
    if len(found) >= 5: break
pf.close()
print(f"\nChecked {len(found)} messages.")
