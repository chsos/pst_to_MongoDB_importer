# diag_att2.py — dump all readable attributes on a pypff attachment
import sys, os
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pypff
from pymongo import MongoClient
from pst_to_mongodb import safe_str, make_id

col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']
PST = r"C:\Users\andy\PycharmProjects\pst_to_MongoDB_importer\pst_files\backup112922.pst"

def dump_attachment(att, i):
    print(f"\n  --- attachment[{i}] ---")
    # Try every non-dunder attribute that isn't a method that takes args
    for attr in sorted(dir(att)):
        if attr.startswith('_'):
            continue
        try:
            val = getattr(att, attr)
            if callable(val):
                # Try calling with no args
                try:
                    result = val()
                    if result is not None and result != b'':
                        print(f"    {attr}() = {repr(result)[:120]}")
                except Exception:
                    pass
            else:
                if val is not None:
                    print(f"    {attr} = {repr(val)[:120]}")
        except Exception as e:
            pass

def walk(folder, path, found, limit=3):
    folder_name  = safe_str(folder.name, fallback="(unnamed)")
    current_path = (path + "/" + folder_name) if path else folder_name
    for i in range(folder.number_of_sub_messages):
        if len(found) >= limit:
            return
        try:
            msg = folder.get_sub_message(i)
            if msg.number_of_attachments == 0:
                continue
            email_id = make_id(current_path, i)
            doc = col.find_one({"_id": email_id, "has_attachments": True}, {"subject": 1})
            if not doc:
                continue
            found.append(email_id)
            print(f"\n=== Message: {doc.get('subject','')[:60]} ===")
            for ai in range(msg.number_of_attachments):
                dump_attachment(msg.get_attachment(ai), ai)
        except Exception as e:
            pass
    for i in range(folder.number_of_sub_folders):
        if len(found) >= limit:
            return
        try:
            walk(folder.get_sub_folder(i), current_path, found, limit)
        except: pass

pf = pypff.file()
pf.open(PST)
root = pf.get_root_folder()
found = []
for i in range(root.number_of_sub_folders):
    walk(root.get_sub_folder(i), "", found, limit=3)
    if len(found) >= 3: break
pf.close()
