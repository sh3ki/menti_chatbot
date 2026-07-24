"""
Quick script to add admin@menti.com to Firestore admins collection
"""
import firebase_admin
from firebase_admin import credentials, firestore
import hashlib
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# Initialize Firebase
cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase-credentials.json')
if os.path.exists(cred_path):
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    print("❌ firebase-credentials.json not found!")
    exit(1)

# Admin email and password
admin_email = 'admin@menti.com'
admin_password = 'Admin123!'  # Use the password from your env or set a new one

# Hash the password
password_hash = hashlib.sha256(admin_password.encode()).hexdigest()

# Check if already exists
existing = list(db.collection('admins').where('email', '==', admin_email.lower()).limit(1).stream())

if existing:
    print(f"⚠️  {admin_email} is already in the admins collection")
    doc = existing[0]
    data = doc.to_dict()
    print(f"   Email: {data.get('email')}")
    print(f"   Created at: {data.get('created_at')}")
    print(f"   Created by: {data.get('created_by')}")
else:
    # Add to admins collection
    db.collection('admins').add({
        'email': admin_email.lower(),
        'password_hash': password_hash,
        'created_at': datetime.now().isoformat(),
        'created_by': 'setup-script'
    })
    print(f"✅ Successfully added {admin_email} to admins collection!")
    print(f"   Password: {admin_password}")
    print(f"   Password Hash: {password_hash[:16]}...")
    print("\n📝 You can now login with:")
    print(f"   Email: {admin_email}")
    print(f"   Password: {admin_password}")
