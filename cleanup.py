# cleanup.py - Remove old free_download transactions

import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
MONGO_URI = os.environ.get("MONGO_URI")

client = MongoClient(
    MONGO_URI,
    tls=True,
    tlsAllowInvalidCertificates=True
)
db = client.kgosibiodrone
transactions_col = db.transactions

print("🗑️  Cleaning up old free_download transactions...\n")

# Find all free_download transactions
free_downloads = list(transactions_col.find(
    {"reference": {"$regex": "^free_download_"}}
))

print(f"Found {len(free_downloads)} old free_download transactions:")
for t in free_downloads:
    print(f"  - {t['reference']}")

if free_downloads:
    confirm = input(f"\n⚠️  Delete these {len(free_downloads)} transactions? (type 'YES' to confirm): ")
    
    if confirm == "YES":
        result = transactions_col.delete_many(
            {"reference": {"$regex": "^free_download_"}}
        )
        print(f"✅ Deleted {result.deleted_count} transactions")
    else:
        print("Cancelled.")
else:
    print("✅ No old free_download transactions found - database is clean!")

client.close()