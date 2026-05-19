import os
import sys
sys.path.insert(0, os.getcwd())
from core.helper import get_mongo_credentials
from pymongo import MongoClient
import certifi

mongo_uri, db_name = get_mongo_credentials()
client = MongoClient(mongo_uri, tlsCAFile=certifi.where())
db = client[db_name]
coll = db["Prompt"]
doc = coll.find_one({"template.versionName": "PlanPal JSON Creation"})
if doc:
    print(doc.get("template", {}))
    print(doc.get("promptText")[:100])
else:
    print("Not found")
