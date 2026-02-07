# Groq API Setup (Free & Unlimited)

## ✨ What Changed?
Your Menti chatbot has been **switched from OpenAI to Groq**! 

**Benefits:**
- ✅ **Completely FREE** - No subscription required
- ✅ **Unlimited free API calls** - Use as much as you want
- ✅ **Same level of quality** - Using Mixtral-8x7b (comparable to GPT-3.5)
- ✅ **Super fast** - Often faster than OpenAI
- ✅ **No credit card needed** - Just sign up

## 🚀 How to Set Up Groq API Key

### Step 1: Create a Groq Account
1. Go to [https://console.groq.com](https://console.groq.com)
2. Click "Sign Up" (or "Sign In" if you already have an account)
3. Use Google, GitHub, or email to create your account
4. Verify your email

### Step 2: Get Your API Key
1. After logging in, go to the **API Keys** section
2. Click **"Create API Key"** button
3. Give it a name like "menti-chatbot"
4. Copy the API key (you'll only see it once!)

### Step 3: Add to Your Environment
1. Open your `.env` file in the menti_chatbot folder
2. Add this line:
   ```
   GROQ_API_KEY=your_api_key_here
   ```
   (Replace `your_api_key_here` with your actual API key)

3. **Remove** this line if it exists:
   ```
   OPENAI_API_KEY=...
   ```

### Step 4: Update Requirements
Run this command to install the new Groq package:
```bash
pip install -r requirements.txt
```

## 📋 What's Different?

### Model Used
- **Before:** `gpt-3.5-turbo` (OpenAI)
- **After:** `mixtral-8x7b-32768` (Groq)

**Mixtral-8x7b** is:
- A powerful open-source LLM
- Comparable to GPT-3.5 in quality
- Often faster and more efficient
- Completely free to use

### All Functionality Remains the Same
✅ Emotion detection (happy, sad, anxious, stressed, neutral)
✅ Supportive AI responses with empathy
✅ Conversation history
✅ Chat storage in Firebase
✅ All UI/UX features

## 💰 Cost Comparison

| Feature | OpenAI | Groq |
|---------|--------|------|
| Cost | $0.50 per 1M tokens | **FREE** |
| Free Tier | Limited | **Unlimited** |
| Subscription | Required | **Not Required** |
| Quality | Excellent | **Excellent** |

## 🎯 That's It!
Your Menti chatbot is now running on **free, unlimited Groq**. No other changes needed!

### Questions?
- Groq Docs: https://console.groq.com/docs
- Groq Community: https://discord.com/invite/groq
