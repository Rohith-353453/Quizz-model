from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()
uri = os.getenv('MONGO_URI')
if not uri:
    print("❌ MONGO_URI missing in .env!")
else:
    try:
        client = MongoClient(uri)
        client.admin.command('ismaster')
        print("✅ Connected to FLUX-cluster! Ready for users/quizzes.")
        db = client['flux_db']
        print(f"DB '{db.name}' good. Collections: {db.list_collection_names()}")
    except Exception as e:
        print(f"❌ Failed: {e}")
        print("Fix: Password? Encoding? IP whitelist in Atlas?")