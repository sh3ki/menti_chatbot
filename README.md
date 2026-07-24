# Menti Emotional Support Chatbot 🌱

An AI-powered emotional support chatbot built with Flask, Firebase, and OpenAI that provides empathetic responses based on emotion detection.

## 🎯 Features (MVP 30-40%)

- ✅ User Authentication (Firebase Auth)
  - Email/Password login and signup
  - Google Sign-In
  - Anonymous login
- ✅ Real-time Chatbot Interface
  - Clean, responsive UI with chat bubbles
  - Auto-scroll chat window
  - Typing indicators
- ✅ Emotion Detection (OpenAI)
  - Detects: happy, sad, anxious, stressed, neutral
- ✅ Supportive AI Responses (OpenAI GPT-3.5)
  - Context-aware emotional support
  - Mental health focused responses
- ✅ Chat History Storage (Firestore)
  - Stores user conversations
  - Supports both authenticated and anonymous users

## 🛠️ Technologies

- **Backend**: Python, Flask
- **Frontend**: HTML, CSS, JavaScript
- **Authentication**: Firebase Authentication
- **Database**: Firebase Firestore
- **AI**: OpenAI API (GPT-3.5 Turbo)

## 📁 Project Structure

```
menti_chatbot/
├── app.py                      # Main Flask application
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables (API keys)
├── firebase-credentials.json   # Firebase Admin SDK credentials (to be added)
├── templates/
│   ├── login.html             # Login/Signup page
│   └── chat.html              # Chat interface
└── README.md                  # This file
```

## 🚀 Setup Instructions

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Firebase

You need to download your Firebase Admin SDK credentials:

1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Select your project: **menti-7b8a1**
3. Go to **Project Settings** > **Service Accounts**
4. Click **Generate New Private Key**
5. Save the downloaded JSON file as `firebase-credentials.json` in the project root

### 3. Firebase Web Configuration

The Firebase web configuration is already set up in the HTML files with these credentials:
- **Project ID**: menti-7b8a1
- **App ID**: 1:164612023817:web:c0437146e86951377f3ad8

**⚠️ IMPORTANT**: You need to add your Firebase Web API Key in both `login.html` and `chat.html`:
- Replace `"AIzaSyBCYdU_-ZJjws8JTdmLiCgwkYD6O4ze9z0"` with your actual Firebase Web API Key
- Find it in Firebase Console > Project Settings > General > Web API Key

### 4. Environment Variables

The `.env` file is already configured with your OpenAI API key. Ensure it contains:

```env
OPENAI_API_KEY=your-openai-key-here
FLASK_APP=app.py
FLASK_ENV=development
SECRET_KEY=your-secret-key
FIREBASE_CREDENTIALS_PATH=firebase-credentials.json
```

### 5. Enable Firebase Services

In your Firebase Console, make sure to enable:
- **Authentication**:
  - Email/Password
  - Google Sign-In
  - Anonymous
- **Firestore Database**:
  - Create database in production mode or test mode

## ▶️ Running the Application

```bash
python app.py
```

The application will start on `http://localhost:5000`

## 📝 Usage

1. **Login/Signup**: 
   - Visit `http://localhost:5000`
   - Sign up with email/password, Google, or continue as guest

2. **Chat**:
   - Type your message in the input box
   - The bot detects your emotion and responds supportively
   - Chat history is saved automatically

## 🔐 Security Notes

- Never commit `.env` or `firebase-credentials.json` to version control
- Add them to `.gitignore`
- Use environment variables for production deployment
- Rotate API keys regularly

## 🧪 Testing

Test endpoints:
- Health check: `http://localhost:5000/health`
- Chat API: POST to `http://localhost:5000/chat`

## Voice (Free/Open-Source) Setup

This project uses only free/open-source voice tools:
- **Speech-to-text**: Vosk (offline model)
- **Text-to-speech**: Piper ONNX voices (offline neural voices)

Two built-in voice options are available in the UI:
- Female (Neural)
- Male (Neural)

### Localhost

1. Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```
2. Start app normally:
  ```bash
  python app.py
  ```

On first use, the app automatically downloads Vosk and Piper model files into `models/`.

### Render Deployment Notes

- No paid APIs are required for voice.
- The service needs outbound internet on first run to download models.
- First voice request can be slower due to model download/warmup.
- Models are cached on the running instance filesystem.

Optional environment variables:
- `VOSK_MODELS_DIR` (default: `./models`)
- `PIPER_MODELS_DIR` (default: `./models/piper`)

## 📦 Dependencies

- `Flask==3.0.0` - Web framework
- `Flask-CORS==4.0.0` - CORS support
- `python-dotenv==1.0.0` - Environment variable management
- `openai==1.12.0` - OpenAI API client
- `firebase-admin==6.4.0` - Firebase Admin SDK

## 🔄 Next Steps (Future Enhancements)

- Crisis detection and escalation
- Voice input/output
- Multi-language support
- Admin dashboard
- Analytics and insights
- Mobile app version

## 📄 License

This is an MVP project for educational and demonstration purposes.

## 🤝 Support

For issues or questions, please check:
- OpenAI API documentation
- Firebase documentation
- Flask documentation

---

**Built with ❤️ for mental health support**
