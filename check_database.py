"""
Quick script to check what's actually in the Firestore database
This will help diagnose why conversations aren't loading
"""
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Initialize Firebase Admin SDK
try:
    cred = credentials.Certificate('firebase-credentials.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Connected to Firebase\n")
except Exception as e:
    print(f"❌ Error connecting to Firebase: {e}")
    exit(1)

# Get user ID from input
user_id = input("Enter your userId (from browser console): ").strip()

print(f"\n{'='*70}")
print(f"🔍 CHECKING DATABASE FOR USER: {user_id}")
print(f"{'='*70}\n")

# Check ALL conversations for this user (no filters)
print("📊 Step 1: Checking ALL conversations for this user (no filters)...")
all_convs = db.collection('conversations').where('userId', '==', user_id).stream()

all_conversations = []
for doc in all_convs:
    data = doc.to_dict()
    data['id'] = doc.id
    all_conversations.append(data)

print(f"   Found {len(all_conversations)} total conversations\n")

if len(all_conversations) == 0:
    print("❌ NO CONVERSATIONS FOUND FOR THIS USER!")
    print("\n🔍 Let me check if there are ANY conversations in the database...")
    
    any_convs = db.collection('conversations').limit(5).stream()
    sample = list(any_convs)
    
    if len(sample) == 0:
        print("   ❌ Database is completely empty - no conversations at all!")
    else:
        print(f"   ✅ Found {len(sample)} conversations in database (from other users)")
        print("\n   Sample conversation structure:")
        for doc in sample:
            data = doc.to_dict()
            print(f"   - ID: {doc.id}")
            print(f"     userId: {data.get('userId', 'MISSING')}")
            print(f"     isAnonymous: {data.get('isAnonymous', 'MISSING')}")
            print(f"     isArchived: {data.get('isArchived', 'MISSING')}")
            print(f"     title: {data.get('title', 'MISSING')}")
            print()
    
    print("\n🔍 Checking old database structure (users collection)...")
    old_chats = db.collection('users').document(user_id).collection('chats').stream()
    old_chats_list = list(old_chats)
    
    if len(old_chats_list) > 0:
        print(f"   ⚠️ Found {len(old_chats_list)} chats in OLD structure (users/{user_id}/chats/)")
        print("   This is the problem! Data is in old structure, not migrated to new structure.")
        print("\n   💡 SOLUTION: Run cleanup_database.py to migrate data")
    else:
        print("   ✅ No data in old structure")
    
    exit(0)

# Print details of each conversation
print("📋 Step 2: Analyzing each conversation...")
print(f"{'='*70}\n")

for idx, conv in enumerate(all_conversations, 1):
    print(f"Conversation #{idx}:")
    print(f"   ID: {conv.get('id')}")
    print(f"   Title: {conv.get('title', 'MISSING TITLE')}")
    print(f"   userId: {conv.get('userId', 'MISSING')}")
    print(f"   isAnonymous: {conv.get('isAnonymous', 'MISSING')}")
    print(f"   isArchived: {conv.get('isArchived', 'MISSING')}")
    print(f"   createdAt: {conv.get('createdAt', 'MISSING')}")
    print(f"   lastUpdated: {conv.get('lastUpdated', 'MISSING')}")
    print(f"   lastMessage: {conv.get('lastMessage', 'MISSING')}")
    
    # Check for old field names (snake_case)
    has_old_fields = False
    if 'user_id' in conv:
        print(f"   ⚠️ OLD FIELD: user_id = {conv.get('user_id')}")
        has_old_fields = True
    if 'is_anonymous' in conv:
        print(f"   ⚠️ OLD FIELD: is_anonymous = {conv.get('is_anonymous')}")
        has_old_fields = True
    if 'is_archived' in conv:
        print(f"   ⚠️ OLD FIELD: is_archived = {conv.get('is_archived')}")
        has_old_fields = True
    
    if has_old_fields:
        print(f"   🚨 This conversation has OLD field names (snake_case)!")
        print(f"   💡 Needs migration to camelCase")
    
    # Check messages
    messages_ref = db.collection('conversations').document(conv['id']).collection('messages')
    messages = list(messages_ref.stream())
    print(f"   Messages: {len(messages)} messages")
    
    print()

# Now test the actual query that's failing
print(f"\n{'='*70}")
print("🧪 Step 3: Testing the ACTUAL query from frontend...")
print(f"{'='*70}\n")

# Test for logged-in user (isAnonymous=false, isArchived=false)
print("Query: userId == user_id AND isAnonymous == false AND isArchived == false")
try:
    query = db.collection('conversations')\
        .where('userId', '==', user_id)\
        .where('isAnonymous', '==', False)\
        .where('isArchived', '==', False)\
        .order_by('lastUpdated', direction=firestore.Query.DESCENDING)
    
    results = list(query.stream())
    print(f"✅ Query executed successfully")
    print(f"   Results: {len(results)} conversations found\n")
    
    if len(results) == 0:
        print("❌ QUERY RETURNED 0 RESULTS!")
        print("\n🔍 Debugging why query failed...")
        
        # Check each filter individually
        print("\n   Testing filter: userId == user_id")
        test1 = list(db.collection('conversations').where('userId', '==', user_id).stream())
        print(f"   ✅ Found {len(test1)} conversations")
        
        print("\n   Testing filter: isAnonymous == false")
        for conv in all_conversations:
            is_anon = conv.get('isAnonymous')
            print(f"   - Conversation {conv['id']}: isAnonymous = {is_anon} (type: {type(is_anon)})")
            if is_anon is None:
                print(f"     🚨 MISSING isAnonymous field! This is why it's not in query results!")
        
        print("\n   Testing filter: isArchived == false")
        for conv in all_conversations:
            is_arch = conv.get('isArchived')
            print(f"   - Conversation {conv['id']}: isArchived = {is_arch} (type: {type(is_arch)})")
            if is_arch is None:
                print(f"     🚨 MISSING isArchived field! This is why it's not in query results!")
        
        print("\n💡 SOLUTION:")
        print("   Run cleanup_database.py to add missing fields to existing conversations!")
    else:
        print("✅ Query is working correctly!")
        for doc in results:
            data = doc.to_dict()
            print(f"   - {data.get('title')} (ID: {doc.id})")

except Exception as e:
    print(f"❌ Query FAILED with error: {e}")
    print("\n🔍 This might be a Firestore index issue!")
    print("   Check the error message for a link to create the required index.")

print(f"\n{'='*70}")
print("🎯 DIAGNOSIS COMPLETE")
print(f"{'='*70}")
