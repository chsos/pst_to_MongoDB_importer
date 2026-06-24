# Quick diagnostic: inspect pypff attachment size attributes
import sys, os
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pypff
from pymongo import MongoClient
from pst_to_mongodb import safe_str, make_id

col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']

PST = r"C:\Users\andy\PycharmProjects\pst_to_MongoDB_importer\pst_files\backup112922.pst"

def walk(folder, path, found, limit=5):
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
            doc = col.find_one({"_id": email_id, "has_attachments": True}, {"attachments": 1})
            if not doc:
                continue
            # Check that it needs backfill
            if all(a.get("gridfs_id") for a in (doc.get("attachments") or [])):
                continue
            found.append((msg, current_path, i, email_id))
            print(f"Found: {email_id[:12]}… folder={current_path} idx={i}")
            for ai in range(msg.number_of_attachments):
                att = msg.get_attachment(ai)
                fname = ""
                for attr in ("name","long_filename","short_filename"):
                    try:
                        v = safe_str(getattr(att, attr, None))
                        if v and v.strip(): fname = v.strip(); break
                    except: pass
                # Try all size-like attributes
                for sattr in ("size","data_size","file_size","attachment_size"):
                    try:
                        print(f"  att[{ai}] {sattr}={getattr(att, sattr, 'N/A')}", end="")
                    except: pass
                # Try reading small amount to confirm data exists
                try:
                    probe = att.read_buffer(64)
                    print(f"  read_buffer(64)={len(probe)} bytes  filename={fname}")
                except Exception as e:
                    print(f"  read_buffer ERR={e}  filename={fname}")
        except Exception as e:
            pass
    for i in range(folder.number_of_sub_folders):
        if len(found) >= limit:
            return
        try:
            walk(folder.get_sub_folder(i), current_path, found, limit)
        except: pass

pf = pypff.file(); pf.open(PST)
root = pf.get_root_folder()
found = []
for i in range(root.number_of_sub_folders):
    walk(root.get_sub_folder(i), "", found, limit=3)
    if len(found) >= 3: break
pf.close()
print(f"\nDone. Found {len(found)} sample docs needing backfill.")
