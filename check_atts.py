from pymongo import MongoClient
col = MongoClient('mongodb://localhost:27017')['mydb']['pst_items']

# Find the DMARC/yahoo email with attachments
doc = col.find_one(
    {'from_addr': {'$regex': 'noreply@dmarc.yahoo', '$options': 'i'}, 'has_attachments': True},
    {'attachments': 1, 'subject': 1}
)
if doc:
    print('Subject:', doc.get('subject', '')[:80])
    atts = doc.get('attachments', [])
    print('Attachments count:', len(atts))
    for a in atts:
        print(' ', a)
else:
    print('DMARC doc not found')

print()

# Check how many docs have any attachment with a gridfs_id
sample = col.find_one(
    {'attachments.gridfs_id': {'$exists': True, '$ne': None}},
    {'attachments': 1, 'subject': 1}
)
if sample:
    print('Found doc WITH gridfs_id:', sample.get('subject','')[:60])
    for a in sample.get('attachments', []):
        print(' ', a)
else:
    print('NO documents have gridfs_id — PST was imported before binary storage was added')
    print('Need to re-import to get clickable attachments.')

# Count docs with attachments array vs just has_attachments flag
has_flag  = col.count_documents({'has_attachments': True})
has_array = col.count_documents({'attachments': {'$exists': True, '$not': {'$size': 0}}})
print()
print(f'has_attachments=True : {has_flag}')
print(f'has attachments array: {has_array}')
