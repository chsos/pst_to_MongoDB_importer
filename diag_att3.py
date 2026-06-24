# diag_att3.py — read MAPI record_sets on attachments to find filename properties
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pypff
from pymongo import MongoClient
from pst_to_mongodb import safe_str, make_id

# Known MAPI property tags for attachment metadata
MAPI_TAGS = {
    0x3001: "PR_DISPLAY_NAME",
    0x3703: "PR_ATTACH_EXTENSION",
    0x3704: "PR_ATTACH_FILENAME",
    0x3705: "PR_ATTACH_METHOD",
    0x3707: "PR_ATTACH_LONG_FILENAME",
    0x370E: "PR_ATTACH_MIME_TAG",
    0x3712: "PR_ATTACH_CONTENT_ID",
    0x3714: "PR_ATTACH_FLAGS",
    0x0037: "PR_SUBJECT",
}

col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']
PST = r"C:\Users\andy\PycharmProjects\pst_to_MongoDB_importer\pst_files\backup112922.pst"

def dump_record_sets(att, att_idx):
    print(f"\n  attachment[{att_idx}]  size={att.size}")
    try:
        rsets = att.record_sets
        for rs_i, rs in enumerate(rsets):
            print(f"    record_set[{rs_i}]:")
            for entry in rs.entries:
                try:
                    entry_type = entry.entry_type          # MAPI property tag (int)
                    value_type = entry.value_type
                    tag_name   = MAPI_TAGS.get(entry_type, f"0x{entry_type:04X}")
                    # Try to get the value as various types
                    val = None
                    for getter in ("get_data_as_string", "get_data_as_utf8_string",
                                   "data", "value"):
                        try:
                            v = getattr(entry, getter)
                            if callable(v): v = v()
                            if v is not None:
                                val = v; break
                        except: pass
                    print(f"      {tag_name:30s}  vtype=0x{value_type:04X}  "
                          f"val={repr(val)[:80] if val is not None else '(none)'}")
                except Exception as e:
                    print(f"      entry error: {e}")
    except Exception as e:
        print(f"    record_sets error: {e}")

def walk(folder, path, found, limit=2):
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
            print(f"\n=== {doc.get('subject','')[:70]} ===")
            for ai in range(min(msg.number_of_attachments, 2)):
                dump_record_sets(msg.get_attachment(ai), ai)
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
    walk(root.get_sub_folder(i), "", found, limit=2)
    if len(found) >= 2: break
pf.close()
