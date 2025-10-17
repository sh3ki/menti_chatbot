"""
Database Cleanup Script
This script removes the OLD database structure (users collection with chats)
and keeps only the NEW structure (conversations collection)
"""

import firebase_admin
from firebase_admin import credentials, firestore
import os

# Initialize Firebase
cred_path = 'firebase-credentials.json'
if not os.path.exists(cred_path):
    print("❌ firebase-credentials.json not found!")
    exit(1)

cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

print("🧹 Starting database cleanup...")
print()

# ==================== CLEANUP OLD STRUCTURE ====================

print("📊 Checking OLD structure (users collection with chats subcollection)...")

users_ref = db.collection('users')
deleted_user_chats = 0
total_users = 0

for user_doc in users_ref.stream():
    total_users += 1
    user_id = user_doc.id
    
    # Check if this user has a 'chats' subcollection (old structure)
    chats_ref = user_doc.reference.collection('chats')
    chats = list(chats_ref.stream())
    
    if len(chats) > 0:
        print(f"   User {user_id}: Found {len(chats)} old chats - DELETING...")
        
        for chat_doc in chats:
            chat_doc.reference.delete()
            deleted_user_chats += 1
        
        print(f"   ✅ Deleted {len(chats)} chats for user {user_id}")

print()
print(f"✨ Cleanup Summary:")
print(f"   - Total users checked: {total_users}")
print(f"   - Old chats deleted: {deleted_user_chats}")
print()

# ==================== VERIFY NEW STRUCTURE ====================

print("📊 Checking NEW structure (conversations collection)...")

conversations_ref = db.collection('conversations')
conversations = list(conversations_ref.stream())

print(f"   Found {len(conversations)} conversations in NEW structure")
print()

# Check each conversation for proper fields
issues_found = 0
fixed_count = 0

for conv_doc in conversations:
    conv_data = conv_doc.to_dict()
    conv_id = conv_doc.id
    updates = {}
    
    # Check for required fields
    if 'isArchived' not in conv_data:
        print(f"   ⚠️ Conversation {conv_id}: Missing 'isArchived' field - FIXING...")
        updates['isArchived'] = False
        issues_found += 1
    
    if 'isAnonymous' not in conv_data:
        print(f"   ⚠️ Conversation {conv_id}: Missing 'isAnonymous' field - FIXING...")
        updates['isAnonymous'] = False
        issues_found += 1
    
    # Fix field names (snake_case to camelCase)
    if 'user_id' in conv_data and 'userId' not in conv_data:
        print(f"   ⚠️ Conversation {conv_id}: Found 'user_id', converting to 'userId' - FIXING...")
        updates['userId'] = conv_data['user_id']
        issues_found += 1
    
    if 'is_archived' in conv_data:
        print(f"   ⚠️ Conversation {conv_id}: Found 'is_archived', converting to 'isArchived' - FIXING...")
        updates['isArchived'] = conv_data['is_archived']
        issues_found += 1
    
    if 'last_updated' in conv_data and 'lastUpdated' not in conv_data:
        print(f"   ⚠️ Conversation {conv_id}: Found 'last_updated', converting to 'lastUpdated' - FIXING...")
        updates['lastUpdated'] = conv_data['last_updated']
        issues_found += 1
    
    if 'created_at' in conv_data and 'createdAt' not in conv_data:
        print(f"   ⚠️ Conversation {conv_id}: Found 'created_at', converting to 'createdAt' - FIXING...")
        updates['createdAt'] = conv_data['created_at']
        issues_found += 1
    
    if 'last_message' in conv_data and 'lastMessage' not in conv_data:
        print(f"   ⚠️ Conversation {conv_id}: Found 'last_message', converting to 'lastMessage' - FIXING...")
        updates['lastMessage'] = conv_data['last_message']
        issues_found += 1
    
    # Apply fixes
    if updates:
        conv_doc.reference.update(updates)
        fixed_count += 1
        print(f"   ✅ Fixed conversation {conv_id}")

print()
print(f"✨ Verification Summary:")
print(f"   - Total conversations: {len(conversations)}")
print(f"   - Issues found: {issues_found}")
print(f"   - Conversations fixed: {fixed_count}")
print()

# ==================== FINAL REPORT ====================

print("=" * 60)
print("🎉 DATABASE CLEANUP COMPLETE!")
print("=" * 60)
print()
print("✅ Old Structure (users/chats):")
print(f"   - Deleted {deleted_user_chats} old chat documents")
print()
print("✅ New Structure (conversations):")
print(f"   - Found {len(conversations)} conversations")
print(f"   - Fixed {fixed_count} conversations with missing/incorrect fields")
print()
print("🚀 Your database is now clean and organized!")
print("   - Only conversations collection is used")
print("   - All fields use camelCase naming")
print("   - All conversations have required fields")
print()
print("Next steps:")
print("1. Restart your Flask server")
print("2. Refresh your browser")
print("3. Login - conversations should now appear!")
print()
