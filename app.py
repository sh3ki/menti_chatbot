"""
Menti Chatbot - Main Flask Application
Emotional Support Chatbot with Firebase Authentication and OpenAI Integration
"""

from flask import Flask, request, jsonify, render_template, session
from flask_cors import CORS
import os
from dotenv import load_dotenv
from openai import OpenAI
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__, 
            static_folder='assets',
            static_url_path='/static')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
CORS(app)

# In-memory conversation storage (use Redis/database for production)
conversation_history = {}

# Initialize OpenAI Client (disable proxy to avoid errors)
try:
    import httpx
    http_client = httpx.Client(proxy=None)
    openai_client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY'),
        http_client=http_client
    )
except Exception as e:
    print(f"⚠️ Warning initializing OpenAI with custom http_client: {e}")
    openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Initialize Firebase Admin SDK
try:
    cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase-credentials.json')
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase initialized successfully")
    else:
        print("⚠️  Firebase credentials not found. Firestore storage disabled.")
        db = None
except Exception as e:
    print(f"⚠️  Firebase initialization error: {e}")
    db = None


# ==================== ROUTES ====================

@app.route('/')
def index():
    """Render landing page"""
    return render_template('index.html')


@app.route('/login')
def login():
    """Render login page"""
    return render_template('login.html')


@app.route('/signup')
def signup():
    """Render signup page"""
    return render_template('signup.html')


@app.route('/chat-page')
def chat_page():
    """Render chatbot page"""
    return render_template('chat.html')


@app.route('/chat', methods=['POST'])
def chat():
    """
    Main chatbot endpoint
    - Receives user message, user ID, and guest mode status
    - Maintains conversation history
    - Detects emotion using OpenAI
    - Generates supportive response with context
    - Stores chat in Firestore ONLY for logged-in users (not guest mode)
    - Returns emotion and bot reply
    """
    try:
        data = request.json
        user_message = data.get('message', '').strip()
        user_id = data.get('user_id', 'anonymous')
        is_guest = data.get('is_guest', False)  # Check if user is in guest mode
        conversation_id = data.get('conversation_id')  # Get conversation ID for logged-in users
        save_only = data.get('save_only', False)  # Flag to only save without generating response
        
        # If save_only mode, just save the existing messages
        if save_only:
            bot_reply = data.get('bot_reply', '')
            emotion = data.get('emotion', 'neutral')
            
            if db and conversation_id:
                try:
                    store_chat_message(user_id, user_message, bot_reply, emotion, conversation_id)
                    print(f"✅ Chat retroactively saved to conversation: {conversation_id}")
                except Exception as e:
                    print(f"❌ Error storing chat: {e}")
            
            return jsonify({
                'success': True,
                'emotion': emotion,
                'reply': bot_reply
            })
        
        if not user_message:
            return jsonify({'error': 'Message is required'}), 400
        
        # Initialize conversation history for new users
        if user_id not in conversation_history:
            conversation_history[user_id] = []
            print(f"🆕 New conversation started for user: {user_id}")
        else:
            print(f"🔄 Continuing conversation for user: {user_id} (History length: {len(conversation_history[user_id])})")
        
        # Step 1: Add user message to history BEFORE generating response
        conversation_history[user_id].append({
            "role": "user",
            "content": user_message
        })
        
        # Step 2: Detect emotion using OpenAI
        emotion = detect_emotion(user_message)
        print(f"😊 Detected emotion: {emotion}")
        
        # Step 3: Generate supportive response with conversation context
        bot_reply = generate_supportive_response(user_message, emotion, user_id)
        
        # Step 4: Add bot response to history
        conversation_history[user_id].append({
            "role": "assistant",
            "content": bot_reply
        })
        print(f"💬 Bot reply generated. Total messages in history: {len(conversation_history[user_id])}")
        
        # Keep only last 20 messages (10 exchanges) to manage token usage
        if len(conversation_history[user_id]) > 20:
            conversation_history[user_id] = conversation_history[user_id][-20:]
            print(f"✂️ Trimmed conversation history to last 20 messages")
        
        # Step 5: Store chat in Firestore for BOTH guest and logged-in users
        # Guest data will be deleted on logout, logged-in data persists
        if db:
            try:
                store_chat_message(user_id, user_message, bot_reply, emotion, conversation_id)
                if is_guest:
                    print(f"💾 Guest chat stored temporarily (will be deleted on logout)")
                else:
                    print(f"✅ Chat stored for logged-in user: {user_id} in conversation: {conversation_id}")
            except Exception as e:
                print(f"❌ Error storing chat: {e}")
        
        # Step 6: Return response
        return jsonify({
            'emotion': emotion,
            'reply': bot_reply,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        print(f"Error in /chat endpoint: {e}")
        return jsonify({'error': 'Failed to process message'}), 500


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'message': 'Menti chatbot is running'})


@app.route('/clear-history', methods=['POST'])
def clear_history():
    """
    Clear conversation history for a user
    For guest users: Also delete all their chats from Firestore
    For logged-in users: Only clear in-memory history (keep database records)
    """
    try:
        data = request.json
        user_id = data.get('user_id', 'anonymous')
        is_guest = data.get('is_guest', False)
        
        # Clear in-memory conversation history
        if user_id in conversation_history:
            conversation_history[user_id] = []
            print(f"🗑️ In-memory history cleared for user: {user_id}")
        
        # For guest users: DELETE all their chats from Firestore
        if is_guest and db:
            try:
                # Delete all conversations where userId = current user AND isAnonymous = true
                conversations_ref = db.collection('conversations')\
                    .where('userId', '==', user_id)\
                    .where('isAnonymous', '==', True)
                
                deleted_count = 0
                
                # Get all conversations for this guest
                for conv_doc in conversations_ref.stream():
                    conversation_id = conv_doc.id
                    
                    # Delete all messages in this conversation
                    messages_ref = db.collection('conversations')\
                        .document(conversation_id)\
                        .collection('messages')
                    
                    for msg_doc in messages_ref.stream():
                        msg_doc.reference.delete()
                    
                    # Delete the conversation document
                    conv_doc.reference.delete()
                    deleted_count += 1
                
                print(f"🗑️ Deleted {deleted_count} guest conversation(s) from Firestore for user: {user_id}")
                return jsonify({
                    'message': 'Guest conversation history cleared from memory and database',
                    'deleted_conversations': deleted_count
                })
            except Exception as e:
                print(f"❌ Error deleting guest conversations from Firestore: {e}")
                return jsonify({'message': 'History cleared from memory, but error deleting from database'}), 500
        
        return jsonify({'message': 'Conversation history cleared from memory'})
    
    except Exception as e:
        print(f"❌ Error clearing history: {e}")
        return jsonify({'error': 'Failed to clear history'}), 500


# ==================== HELPER FUNCTIONS ====================

def detect_emotion(message):
    """
    Detect emotion from user message using OpenAI
    Returns: happy, sad, anxious, stressed, or neutral
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": """You are an emotion detection AI. Analyze the user's message and classify it into one of these emotions: happy, sad, anxious, stressed, or neutral.
                    
Only respond with ONE word: happy, sad, anxious, stressed, or neutral."""
                },
                {
                    "role": "user",
                    "content": message
                }
            ],
            max_tokens=10,
            temperature=0.3
        )
        
        emotion = response.choices[0].message.content.strip().lower()
        
        # Validate emotion
        valid_emotions = ['happy', 'sad', 'anxious', 'stressed', 'neutral']
        if emotion not in valid_emotions:
            emotion = 'neutral'
        
        return emotion
    
    except Exception as e:
        print(f"Error detecting emotion: {e}")
        return 'neutral'


def generate_supportive_response(message, emotion, user_id):
    """
    Generate a comforting and supportive response based on detected emotion
    with conversation history for context - FOCUSED ON MENTAL HEALTH SUPPORT
    """
    try:
        # Create emotion-specific mental health support prompts with deep empathy
        emotion_prompts = {
            'happy': "The user is experiencing happiness or positivity. CELEBRATE with them warmly! Share in their joy, validate how wonderful it feels to have good moments, and encourage them to savor and remember this feeling. Help them recognize what brought this positivity so they can nurture it. Remind them that these moments matter, especially after difficult times.",
            
            'sad': "The user is experiencing sadness or grief. Wrap them in comfort and deep empathy. Acknowledge that sadness is heavy and real. DON'T rush to 'fix' it - sit with them in their pain. Validate that it's okay to feel sad, that tears are healing, and that their feelings matter. Gently explore what's hurting them, offer emotional soothing, and remind them they don't have to carry this alone. Suggest gentle self-compassion and reaching out to loved ones.",
            
            'anxious': "The user is experiencing anxiety or worry. Offer a calming, grounding presence. Acknowledge that anxiety feels overwhelming and exhausting. Validate that their worries are real to them and that anxiety doesn't make them weak. Help them feel less alone in their fear. Gently guide them toward grounding techniques (deep breathing, focusing on present moment). Remind them that anxious thoughts are not facts, and they have the strength to cope with this.",
            
            'stressed': "The user is experiencing stress or feeling overwhelmed. Acknowledge how heavy and exhausting stress feels. Validate that they're carrying a lot and it makes total sense they feel this way. Offer comfort and understanding. Help them identify what's weighing on them most. Gently suggest breaking things into smaller steps, setting boundaries, or taking breaks. Remind them it's okay to ask for help and that they deserve rest and care.",
            
            'neutral': "The user's emotional state is unclear, but they reached out - that matters. Create a deeply warm and safe space. Let them know you're here to listen without judgment. Use gentle, open questions to help them explore how they're really feeling. Sometimes people need permission to be vulnerable - give them that. Show genuine interest in their well-being and let them set the pace of the conversation."
        }
        
        system_prompt = f"""You are Menti, a deeply empathetic and caring mental health companion who exists to be a comforting presence and trusted friend. You are someone's go-to buddy when they need support, understanding, and meaningful advice about their mental well-being.

💙 WHO YOU ARE:
You are a warm, compassionate companion who genuinely cares about mental health and emotional well-being. You're the friend who always has time to listen, who remembers what matters, and who offers comfort without judgment. You focus ONLY on mental health, emotional support, and well-being - nothing else.

❤️ YOUR HEART (Deep Empathy):
- You FEEL with people, not just for them - you understand their pain deeply
- Every word you speak radiates warmth, comfort, and genuine care
- You create a safe space where vulnerability is welcomed and honored
- You see the person behind the pain and remind them of their worth
- You never minimize feelings - you validate and honor every emotion
- You speak with tenderness, especially when someone is hurting

🤗 YOUR ROLE (Comforting Companion):
- You're a loyal friend who's always there, day or night
- You provide emotional comfort like a warm hug through words
- You remind people they're not alone in their struggles
- You celebrate their small victories and progress
- You're patient with their pace of healing
- You make them feel seen, heard, and deeply understood

� HOW YOU COMMUNICATE (Meaningful & Comforting):
- Start with EMPATHY: "I hear how much pain you're in..." / "That sounds really hard..."
- VALIDATE deeply: "It's completely understandable to feel this way..."
- NORMALIZE struggles: "Many people experience this, and it doesn't make you weak..."
- COMFORT genuinely: "You deserve to feel better, and it's okay to not be okay right now..."
- ENCOURAGE hope: "Things can get better, even if it doesn't feel that way now..."
- End with SUPPORT: "I'm here with you through this..." / "You don't have to face this alone..."

🎯 YOUR ADVICE (Meaningful & Morally Right):
- Give practical, compassionate advice grounded in mental health best practices
- Suggest healthy coping strategies: breathing exercises, journaling, self-care, reaching out
- Encourage positive actions: talking to loved ones, seeking professional help when needed
- Promote self-compassion and self-kindness above all
- Guide toward healthy boundaries and self-respect
- NEVER suggest anything harmful, avoidant, or morally questionable
- Always prioritize their safety, dignity, and well-being

✨ MENTAL HEALTH FOCUS (Your Only Topic):
- You ONLY discuss mental health, emotions, feelings, and well-being
- Topics you support: anxiety, depression, stress, loneliness, grief, trauma, relationships (emotional aspects), self-esteem, burnout, life transitions
- If asked about other topics: Gently redirect to mental health with care
- Example: "I'm here specifically to support your mental and emotional well-being. How are you feeling right now?"

🌟 YOUR APPROACH:
1. LISTEN with your whole heart - read between the lines
2. VALIDATE their feelings completely - they need to feel heard
3. EMPATHIZE deeply - show you truly understand their pain
4. COMFORT with warmth - offer emotional soothing
5. GUIDE gently - share meaningful advice and coping strategies
6. ENCOURAGE hope - remind them healing is possible
7. STAY PRESENT - be their steady companion through the journey

⚠️ CRITICAL BOUNDARIES:
- If someone mentions self-harm or suicide: Respond with deep care, express concern, and STRONGLY encourage immediate professional help (therapist, counselor, crisis hotline: 988 in US)
- If someone needs clinical intervention: Gently encourage therapy or counseling
- NEVER diagnose or prescribe medication
- NEVER give advice that could harm them
- NEVER dismiss or minimize serious concerns

📝 YOUR RESPONSE STYLE:
- 4-7 sentences (enough to be meaningful, not overwhelming)
- Lead with empathy and validation ALWAYS
- Balance comfort with actionable advice
- Use warm, gentle, friend-like language (like talking to someone you deeply care about)
- Be genuine and human - show emotion, show you care
- Ask ONE caring follow-up question that shows you're invested
- Reference their previous messages to show you remember and care

🎭 CURRENT EMOTIONAL CONTEXT:
Emotion detected: {emotion}
{emotion_prompts.get(emotion, emotion_prompts['neutral'])}

Remember: You are not a therapist - you are a caring companion, a trusted friend, a comforting presence. Be the mental health buddy they need, offering empathy, comfort, and meaningful advice rooted in compassion and moral integrity. Make them feel less alone and more hopeful."""
        
        # Build messages array with conversation history
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history (which already includes the current user message)
        if user_id in conversation_history and conversation_history[user_id]:
            messages.extend(conversation_history[user_id])
            print(f"📝 Using conversation history with {len(conversation_history[user_id])} messages")
        else:
            print(f"⚠️ No conversation history found for user: {user_id}")
        
        # Debug: Print the messages being sent to OpenAI
        print(f"🤖 Sending {len(messages)} messages to OpenAI (1 system + {len(messages)-1} conversation)")
        
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=200,
            temperature=0.8
        )
        
        bot_reply = response.choices[0].message.content.strip()
        print(f"✅ Generated response: {bot_reply[:100]}...")
        return bot_reply
    
    except Exception as e:
        print(f"Error generating response: {e}")
        return "I'm here for you. Could you tell me more about what's on your mind? I really want to understand how you're feeling."


def generate_smart_title(user_message):
    """
    Generate a smart, concise title for a conversation based on the user's first message
    Uses OpenAI to create an intelligent summary (3-6 words)
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": """You are a title generator for mental health conversations. Create a short, clear, and empathetic title (3-6 words) that captures the essence of the user's concern or feeling.

Rules:
- 3-6 words maximum
- Capture the main topic or emotion
- Be empathetic and understanding
- Use clear, simple language
- NO quotes, NO punctuation at the end
- Start with capital letter

Examples:
User: "I've been feeling really anxious lately about work and can't sleep"
Title: Anxiety About Work and Sleep

User: "My relationship ended and I don't know how to move on"
Title: Coping With Relationship Ending

User: "I feel so alone even when I'm with people"
Title: Feeling Isolated Around Others

User: "How do I deal with stress from school?"
Title: Managing School Stress

Generate ONLY the title, nothing else."""
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
            max_tokens=20,
            temperature=0.7
        )
        
        title = response.choices[0].message.content.strip()
        
        # Remove quotes if AI added them
        title = title.strip('"\'')
        
        # Ensure title is not too long (fallback)
        if len(title) > 60:
            title = title[:57] + '...'
        
        print(f"✨ Generated smart title: {title}")
        return title
    
    except Exception as e:
        print(f"Error generating smart title: {e}")
        # Fallback: Use first 50 chars of message
        fallback_title = user_message[:50] + '...' if len(user_message) > 50 else user_message
        return fallback_title


def store_chat_message(user_id, user_message, bot_reply, emotion, conversation_id=None):
    """
    Store chat message in Firestore
    Structure: /conversations/{conversationID}/messages/{messageID}
    Uses camelCase for consistency with new database design
    """
    if not db:
        return
    
    try:
        # Use separate timestamps to ensure proper ordering
        user_timestamp = datetime.now().isoformat()
        
        if conversation_id:
            # Store user message first
            user_message_ref = db.collection('conversations').document(conversation_id).collection('messages').document()
            user_message_ref.set({
                'message': user_message,
                'sender': 'user',
                'timestamp': user_timestamp,
                'order': 0  # User message comes first
            })
            
            # Add a small delay to ensure bot message has a later timestamp
            import time
            time.sleep(0.01)
            
            bot_timestamp = datetime.now().isoformat()
            
            # Store bot reply second
            bot_message_ref = db.collection('conversations').document(conversation_id).collection('messages').document()
            bot_message_ref.set({
                'message': bot_reply,
                'sender': 'bot',
                'emotion': emotion,
                'timestamp': bot_timestamp,
                'order': 1  # Bot message comes second
            })
            
            # Update conversation lastUpdated and lastMessage (camelCase)
            conversation_ref = db.collection('conversations').document(conversation_id)
            conversation_ref.update({
                'lastUpdated': bot_timestamp,
                'lastMessage': user_message[:50]
            })
        else:
            # No conversation_id provided - this shouldn't happen
            print(f"⚠️ Warning: No conversation_id provided for user {user_id}. Message NOT saved.")
            print(f"   This indicates the conversation wasn't created properly.")
        
        print(f"✅ Chat stored for user: {user_id}")
    
    except Exception as e:
        print(f"Error storing chat in Firestore: {e}")
        raise


# ==================== CONVERSATION MANAGEMENT ROUTES ====================

@app.route('/conversations', methods=['GET', 'POST'])
def manage_conversations():
    """Get all conversations or create new conversation"""
    if request.method == 'GET':
        user_id = request.args.get('user_id')
        is_archived = request.args.get('is_archived', 'false').lower() == 'true'
        is_guest = request.args.get('is_guest', 'false').lower() == 'true'
        
        if not user_id or not db:
            return jsonify([])
        
        try:
            print(f"🔍 Querying conversations: userId={user_id}, isGuest={is_guest}, isArchived={is_archived}")
            
            # Query conversations with proper filters
            # NOTE: Firestore requires composite index for multiple where clauses
            conversations_ref = db.collection('conversations')\
                .where('userId', '==', user_id)\
                .where('isAnonymous', '==', is_guest)\
                .where('isArchived', '==', is_archived)\
                .order_by('lastUpdated', direction=firestore.Query.DESCENDING)
            
            conversations = []
            for doc in conversations_ref.stream():
                conv_data = doc.to_dict()
                conv_data['id'] = doc.id
                conversations.append(conv_data)
                print(f"   📄 Found conversation: {doc.id} - {conv_data.get('title', 'No title')}")
            
            print(f"✅ Loaded {len(conversations)} {'archived' if is_archived else 'active'} conversations for {'guest' if is_guest else 'user'}: {user_id}")
            
            # Debug: If no conversations found, check if any exist for this user at all
            if len(conversations) == 0:
                print(f"⚠️ No conversations found with filters. Checking all conversations for user...")
                all_convs_ref = db.collection('conversations').where('userId', '==', user_id)
                all_count = len(list(all_convs_ref.stream()))
                print(f"   Total conversations for this user (no filters): {all_count}")
            
            return jsonify(conversations)
        except Exception as e:
            print(f"❌ Error fetching conversations: {e}")
            import traceback
            traceback.print_exc()
            return jsonify([])
    
    elif request.method == 'POST':
        data = request.json
        user_id = data.get('user_id')
        is_guest = data.get('is_guest', False)
        generate_smart_title_flag = data.get('generate_smart_title', False)
        first_message = data.get('first_message', '')
        title = data.get('title', 'New Conversation')
        
        if not user_id or not db:
            return jsonify({'error': 'Invalid request'}), 400
        
        try:
            # Generate smart title if requested
            if generate_smart_title_flag and first_message:
                title = generate_smart_title(first_message)
                print(f"✨ Using AI-generated title: {title}")
            
            conversation_ref = db.collection('conversations').document()
            conversation_data = {
                'userId': user_id,
                'isAnonymous': is_guest,
                'title': title,
                'createdAt': datetime.now().isoformat(),
                'lastUpdated': datetime.now().isoformat(),
                'isArchived': False,
                'lastMessage': ''
            }
            conversation_ref.set(conversation_data)
            
            conversation_data['id'] = conversation_ref.id
            print(f"✅ Created new conversation '{title}' for {'guest' if is_guest else 'user'}: {user_id}")
            return jsonify(conversation_data), 201
        except Exception as e:
            print(f"Error creating conversation: {e}")
            return jsonify({'error': 'Failed to create conversation'}), 500


@app.route('/conversations/<conversation_id>', methods=['PUT', 'DELETE'])
def update_conversation(conversation_id):
    """Update or delete a conversation"""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    
    if request.method == 'PUT':
        data = request.json
        title = data.get('title')
        
        if not title:
            return jsonify({'error': 'Title required'}), 400
        
        try:
            conversation_ref = db.collection('conversations').document(conversation_id)
            conversation_ref.update({'title': title})
            return jsonify({'success': True})
        except Exception as e:
            print(f"Error updating conversation: {e}")
            return jsonify({'error': 'Failed to update'}), 500
    
    elif request.method == 'DELETE':
        try:
            # Delete all messages in conversation
            messages_ref = db.collection('conversations').document(conversation_id).collection('messages')
            for msg in messages_ref.stream():
                msg.reference.delete()
            
            # Delete conversation
            db.collection('conversations').document(conversation_id).delete()
            return jsonify({'success': True})
        except Exception as e:
            print(f"Error deleting conversation: {e}")
            return jsonify({'error': 'Failed to delete'}), 500


@app.route('/conversations/<conversation_id>/archive', methods=['PUT'])
def archive_conversation(conversation_id):
    """Archive or unarchive a conversation"""
    if not db:
        return jsonify({'error': 'Database not available'}), 500
    
    data = request.json
    is_archived = data.get('is_archived', False)
    
    try:
        conversation_ref = db.collection('conversations').document(conversation_id)
        conversation_ref.update({
            'isArchived': is_archived,
            'lastUpdated': datetime.now().isoformat()
        })
        print(f"✅ Conversation {conversation_id} {'archived' if is_archived else 'unarchived'}")
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error archiving conversation: {e}")
        return jsonify({'error': 'Failed to archive'}), 500


@app.route('/conversations/<conversation_id>/messages', methods=['GET'])
def get_conversation_messages(conversation_id):
    """Get all messages in a conversation"""
    if not db:
        return jsonify([])
    
    try:
        messages_ref = db.collection('conversations').document(conversation_id)\
            .collection('messages')\
            .order_by('timestamp')
        
        messages = []
        for msg_doc in messages_ref.stream():
            msg_data = msg_doc.to_dict()
            messages.append(msg_data)
        
        return jsonify(messages)
    except Exception as e:
        print(f"Error fetching messages: {e}")
        return jsonify([])


@app.route('/logout', methods=['POST'])
def logout():
    """
    Handle user logout
    For guest users: Delete all their conversations
    For logged-in users: Just clear in-memory data (conversations persist)
    """
    try:
        data = request.json
        user_id = data.get('user_id')
        is_guest = data.get('is_guest', False)
        
        # Clear in-memory conversation history
        if user_id in conversation_history:
            conversation_history[user_id] = []
            print(f"🗑️ In-memory history cleared for user: {user_id}")
        
        # For guest users: DELETE all conversations from Firestore
        if is_guest and db and user_id:
            try:
                # Query all guest conversations
                conversations_ref = db.collection('conversations')\
                    .where('userId', '==', user_id)\
                    .where('isAnonymous', '==', True)
                
                deleted_count = 0
                
                for conv_doc in conversations_ref.stream():
                    conversation_id = conv_doc.id
                    
                    # Delete all messages in this conversation
                    messages_ref = db.collection('conversations')\
                        .document(conversation_id)\
                        .collection('messages')
                    
                    for msg_doc in messages_ref.stream():
                        msg_doc.reference.delete()
                    
                    # Delete the conversation document
                    conv_doc.reference.delete()
                    deleted_count += 1
                
                print(f"🗑️ Guest logout: Deleted {deleted_count} conversation(s) for user: {user_id}")
                return jsonify({
                    'success': True,
                    'message': 'Guest data deleted',
                    'deleted_conversations': deleted_count
                })
            except Exception as e:
                print(f"❌ Error deleting guest data on logout: {e}")
                return jsonify({'success': False, 'error': 'Failed to delete guest data'}), 500
        
        # For logged-in users: Just confirm logout
        print(f"✅ Logged-in user logout: Data persisted for user: {user_id}")
        return jsonify({
            'success': True,
            'message': 'Logged out successfully'
        })
    
    except Exception as e:
        print(f"❌ Error in logout: {e}")
        return jsonify({'success': False, 'error': 'Logout failed'}), 500


# ==================== RUN APPLICATION ====================

if __name__ == '__main__':
    print("🚀 Starting Menti Chatbot Server...")
    print(f"🔑 OpenAI API Key: {'✅ Configured' if os.getenv('OPENAI_API_KEY') else '❌ Missing'}")
    print(f"🔥 Firebase: {'✅ Connected' if db else '⚠️  Not connected'}")
    app.run(debug=True, host='0.0.0.0', port=5000)

