# 🗄️ Database Redesign - Clean & Organized Structure

## ❌ Current Problems

1. **Messy Structure:** Data scattered across multiple collections
2. **Duplicates:** Same conversation data stored multiple times
3. **No Clear Separation:** Logged-in and guest data mixed
4. **Unorganized:** Hard to query and maintain

---

## ✅ New Database Structure

### **Collection: `users`**
Stores user profile information (logged-in users only)

```
users/
├── {userId}/
    ├── email: string
    ├── displayName: string
    ├── firstName: string
    ├── lastName: string
    ├── isActive: boolean
    ├── createdAt: timestamp
    └── lastLogin: timestamp
```

### **Collection: `conversations`**
Stores all conversations (both logged-in and anonymous)

```
conversations/
├── {conversationId}/
    ├── userId: string (Firebase Auth UID)
    ├── isAnonymous: boolean (true for guests, false for logged-in)
    ├── title: string (auto-generated from first message)
    ├── isArchived: boolean (false by default)
    ├── createdAt: timestamp
    ├── lastUpdated: timestamp
    ├── lastMessage: string (preview text)
    └── messages/ (subcollection)
        ├── {messageId}/
            ├── message: string
            ├── sender: "user" | "bot"
            ├── emotion: string (for bot messages)
            ├── timestamp: timestamp
            └── order: number (0, 1, 2, 3...)
```

---

## 🎯 Key Design Principles

### **1. Single Source of Truth**
- All conversations in ONE collection: `conversations`
- No duplicates, no scattered data
- Easy to query and maintain

### **2. Clear User Type Identification**
```javascript
{
  userId: "abc123",
  isAnonymous: false  // Logged-in user - PERSIST forever
}

{
  userId: "guest-xyz",
  isAnonymous: true   // Guest user - DELETE on logout
}
```

### **3. Archive Flag**
```javascript
{
  isArchived: false  // Active conversation (default)
}

{
  isArchived: true   // Archived conversation
}
```

### **4. Chronological Messages**
```javascript
messages/ (ordered by timestamp)
├── msg1: { sender: "user", timestamp: "...", order: 0 }
├── msg2: { sender: "bot", timestamp: "...", order: 1 }
├── msg3: { sender: "user", timestamp: "...", order: 2 }
└── msg4: { sender: "bot", timestamp: "...", order: 3 }
```

---

## 🔄 Data Flow

### **For Logged-In Users:**

#### **Login:**
```
1. User logs in with email/password
2. Firebase Auth creates userId
3. User data saved to users/{userId}
4. Load conversations where userId matches AND isAnonymous = false
```

#### **Chat:**
```
1. User sends message
2. Bot responds
3. Create conversation (if first message):
   - conversationId: auto-generated
   - userId: current user's UID
   - isAnonymous: false
   - isArchived: false
   - title: from first message
4. Save messages to conversations/{conversationId}/messages/
```

#### **Logout:**
```
1. User clicks logout
2. Firebase Auth signs out
3. Conversations REMAIN in database (isAnonymous = false)
4. Redirect to login page
```

#### **Login Again:**
```
1. User logs in with same account
2. Load conversations where userId matches
3. All previous conversations appear in sidebar
4. Can continue chatting
```

---

### **For Anonymous/Guest Users:**

#### **Login as Guest:**
```
1. User clicks "Continue as Guest"
2. Firebase Auth creates anonymous session
3. userId = anonymous UID
4. isAnonymous flag set to true
```

#### **Chat:**
```
1. Guest sends message
2. Bot responds
3. Create conversation:
   - userId: anonymous UID
   - isAnonymous: true
   - isArchived: false
   - title: from first message
4. Save messages
```

#### **Logout:**
```
1. Guest clicks logout
2. Query all conversations where userId = current user AND isAnonymous = true
3. DELETE all matching conversations (cascade delete messages)
4. Firebase Auth signs out
5. Redirect to login
```

---

## 🎨 Sidebar Features

### **Default View: Active Chats**
```
Shows conversations where:
- userId = current user
- isArchived = false
- Sorted by lastUpdated (newest first)
```

### **Archived View: Archived Chats**
```
Click "Show Archived" button:
- Toggle view
- Shows conversations where:
  - userId = current user
  - isArchived = true
  - Sorted by lastUpdated (newest first)
```

### **Archive Action:**
```
Click "Archive" in context menu:
- Update conversation: isArchived = true
- Move from active to archived section
- Still accessible, just hidden from main view
```

### **Unarchive Action:**
```
Click "Unarchive" in context menu (in archived view):
- Update conversation: isArchived = false
- Move from archived to active section
```

### **Delete Action:**
```
Click "Delete" in context menu:
- Confirmation dialog
- Delete all messages in conversation
- Delete conversation document
- Permanently removed (cannot be undone)
```

---

## 📊 Database Queries

### **Load Active Conversations:**
```javascript
db.collection('conversations')
  .where('userId', '==', currentUserId)
  .where('isAnonymous', '==', isGuest)
  .where('isArchived', '==', false)
  .orderBy('lastUpdated', 'desc')
```

### **Load Archived Conversations:**
```javascript
db.collection('conversations')
  .where('userId', '==', currentUserId)
  .where('isAnonymous', '==', isGuest)
  .where('isArchived', '==', true)
  .orderBy('lastUpdated', 'desc')
```

### **Delete Guest Conversations on Logout:**
```javascript
const conversations = db.collection('conversations')
  .where('userId', '==', currentUserId)
  .where('isAnonymous', '==', true);

conversations.forEach(conv => {
  // Delete all messages
  conv.collection('messages').forEach(msg => msg.delete());
  // Delete conversation
  conv.delete();
});
```

---

## 🔐 Security Rules

### **Firestore Security Rules:**
```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    
    // Users collection - only owner can read/write
    match /users/{userId} {
      allow read, write: if request.auth != null && request.auth.uid == userId;
    }
    
    // Conversations - only owner can access
    match /conversations/{conversationId} {
      allow read, write: if request.auth != null && 
                          resource.data.userId == request.auth.uid;
      
      // Messages subcollection
      match /messages/{messageId} {
        allow read, write: if request.auth != null &&
                            get(/databases/$(database)/documents/conversations/$(conversationId)).data.userId == request.auth.uid;
      }
    }
  }
}
```

---

## ✨ Benefits

### **1. Clean Structure:**
✅ Single conversations collection
✅ No duplicates
✅ Easy to understand
✅ Easy to maintain

### **2. Clear Separation:**
✅ `isAnonymous` flag distinguishes user types
✅ Guest data easily identifiable
✅ Can bulk delete guest data

### **3. Persistent for Logged-In:**
✅ Conversations survive logout
✅ Available on next login
✅ Data never lost unless deleted

### **4. Temporary for Guests:**
✅ Conversations created during session
✅ Automatically deleted on logout
✅ No orphaned data

### **5. Archive Functionality:**
✅ Hide conversations without deleting
✅ Toggle between active and archived views
✅ Can unarchive anytime

### **6. Proper Deletion:**
✅ Delete removes everything
✅ Cascading delete (messages → conversation)
✅ Cannot be recovered

---

## 🚀 Implementation Plan

### **Phase 1: Update Backend (app.py)**
1. ✅ Update conversation creation (add isAnonymous flag)
2. ✅ Update message storage (proper timestamps)
3. ✅ Add guest cleanup on logout
4. ✅ Update archive/unarchive endpoints
5. ✅ Update query filters

### **Phase 2: Update Frontend (chat.html)**
1. ✅ Add archive view toggle button
2. ✅ Update conversation loading (filter by isArchived)
3. ✅ Add guest cleanup on logout
4. ✅ Update context menu (add unarchive option)
5. ✅ Update UI to show current view state

### **Phase 3: Test**
1. ✅ Test logged-in user persistence
2. ✅ Test guest deletion on logout
3. ✅ Test archive/unarchive
4. ✅ Test delete functionality
5. ✅ Test view toggling

---

## 📈 Migration Strategy

### **For Existing Data:**

**Option 1: Clean Slate (Recommended)**
- Delete all existing conversations
- Start fresh with new structure
- Users will need to start new conversations

**Option 2: Migrate Data**
- Add `isAnonymous: false` to all existing conversations
- Add `isArchived: false` to all existing conversations
- Update message format if needed

---

## 🎉 Final Result

**Logged-In Users:**
- ✅ Create conversations
- ✅ Logout and login - conversations persist
- ✅ Archive conversations (hide them)
- ✅ View archived conversations
- ✅ Unarchive conversations
- ✅ Delete conversations permanently

**Guest Users:**
- ✅ Create conversations
- ✅ Chat normally
- ✅ On logout - all data deleted
- ✅ Fresh start on next visit

**Database:**
- ✅ Clean, organized structure
- ✅ No duplicates
- ✅ Easy to query
- ✅ Easy to maintain
- ✅ Scalable

---

This is the complete database redesign! Ready to implement? 🚀
