# 🚀 Setup Guide for New Laptop

This guide will help you set up the Menti Chatbot project on a new laptop.

---

## 📦 Step 1: Prepare Files on Current Laptop

### What to Include in the ZIP
When zipping the project folder, make sure to include:

✅ **Include these files:**
- `app.py`
- `requirements.txt`
- `setup.bat`
- `test_setup.py`
- `check_database.py`
- `cleanup_database.py`
- All `.md` files (documentation)
- `templates/` folder (all HTML files)
- `assets/` folder (CSS files)
- `firebase-credentials.json` ⚠️ **IMPORTANT**

❌ **DO NOT include:**
- `.env` file (contains sensitive API keys - you'll create a new one)
- `__pycache__/` folders (Python cache)
- Any virtual environment folders (`venv/`, `env/`)

### Create the ZIP
1. Select the `menti_chatbot` folder
2. Right-click → Send to → Compressed (zipped) folder
3. Transfer the ZIP file to your new laptop (USB drive, cloud storage, etc.)

---

## 💻 Step 2: Setup on New Laptop

### Prerequisites
Make sure you have:
- **Python 3.8 or higher** installed ([Download Python](https://www.python.org/downloads/))
  - During installation, check "Add Python to PATH"
- **Internet connection** (to install dependencies)

### Installation Steps

#### 1. Extract the ZIP File
```powershell
# Extract to your desired location, for example:
# C:\Users\YourName\Documents\menti_chatbot
```

#### 2. Open PowerShell in the Project Folder
- Navigate to the extracted folder
- Hold `Shift` + Right-click in the folder → "Open PowerShell window here"
- OR open PowerShell and run:
```powershell
cd "C:\path\to\menti_chatbot"
```

#### 3. Create .env File
Create a new file called `.env` in the project root with the following content:

```env
# Groq API Key
GROQ_API_KEY=your-groq-api-key-here

# Flask Configuration
FLASK_APP=app.py
FLASK_ENV=development
SECRET_KEY=your-secret-key-here

# Firebase Configuration
FIREBASE_CREDENTIALS_PATH=firebase-credentials.json
```

**⚠️ IMPORTANT:** Replace the placeholder values:
- Get your **Groq API Key** from: https://console.groq.com/keys
- Generate a **SECRET_KEY** (any random string, e.g., use a password generator)

#### 4. Verify Firebase Credentials
Make sure `firebase-credentials.json` is in the project root folder. This file should have been included in the ZIP.

#### 5. Run the Setup Script
```powershell
.\setup.bat
```

This will:
- ✅ Check if Python is installed
- ✅ Install all required Python packages
- ✅ Run setup verification tests
- ✅ Display any missing configuration

**Expected output:**
```
[1/3] Installing Python dependencies...
[2/3] Running setup verification test...
[3/3] Setup complete!
```

---

## ▶️ Step 3: Run the Application

### Start the Server
```powershell
python app.py
```

### Access the Application
1. Open your web browser
2. Go to: `http://localhost:5000`
3. You should see the login page

### Test the Application
1. Click "Sign Up" to create a new account
2. Or use "Continue as Guest" for quick testing
3. Start chatting with the bot!

---

## 🔧 Troubleshooting

### Problem: "Python is not recognized"
**Solution:** 
- Install Python from https://www.python.org/downloads/
- During installation, check "Add Python to PATH"
- Restart PowerShell after installation

### Problem: "pip is not recognized"
**Solution:**
```powershell
python -m pip install --upgrade pip
```

### Problem: "No module named 'flask'"
**Solution:**
```powershell
pip install -r requirements.txt
```

### Problem: "Firebase credentials not found"
**Solution:**
- Make sure `firebase-credentials.json` is in the project root
- Check that the file name matches exactly (case-sensitive)
- Verify the file path in `.env` is correct

### Problem: "Groq API error"
**Solution:**
- Check your `.env` file has the correct `GROQ_API_KEY`
- Verify your Groq API key is valid at https://console.groq.com/keys
- Check your internet connection

### Problem: Port 5000 already in use
**Solution:**
- Close any other applications using port 5000
- OR modify `app.py` to use a different port:
```python
# At the bottom of app.py, change:
app.run(debug=True, port=5001)  # Use port 5001 instead
```

---

## 📝 Quick Command Reference

```powershell
# Navigate to project folder
cd "C:\path\to\menti_chatbot"

# Install dependencies
pip install -r requirements.txt

# Run setup verification
python test_setup.py

# Start the application
python app.py

# Check Python version
python --version

# Check pip version
pip --version
```

---

## 🔐 Security Notes

### Sensitive Files
- **`.env`**: Contains API keys - NEVER commit to Git or share publicly
- **`firebase-credentials.json`**: Contains Firebase credentials - Keep secure

### Best Practices
1. Create a `.gitignore` file if using Git:
```
.env
__pycache__/
*.pyc
venv/
env/
```

2. Keep your API keys secure
3. Don't share your `.env` file
4. Regenerate keys if accidentally exposed

---

## 📚 Additional Resources

- **Flask Documentation**: https://flask.palletsprojects.com/
- **Firebase Console**: https://console.firebase.google.com/
- **Groq Console**: https://console.groq.com/
- **Python Download**: https://www.python.org/downloads/

---

## ✅ Checklist for New Setup

- [ ] Python 3.8+ installed
- [ ] Project ZIP extracted
- [ ] `.env` file created with all keys
- [ ] `firebase-credentials.json` present
- [ ] Dependencies installed (`setup.bat` run successfully)
- [ ] Application runs (`python app.py` works)
- [ ] Can access http://localhost:5000
- [ ] Can login/signup successfully
- [ ] Chat functionality works

---

## 🆘 Need Help?

If you encounter issues:
1. Check the troubleshooting section above
2. Verify all prerequisites are met
3. Review the error messages carefully
4. Check that all files were copied correctly

**Common Issues:**
- Missing `.env` file → Create it manually
- Wrong Python version → Install Python 3.8+
- Missing dependencies → Run `pip install -r requirements.txt`
- Firebase errors → Check `firebase-credentials.json` exists

---

**Last Updated:** October 17, 2025
