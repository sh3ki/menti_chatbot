# 📋 Quick Setup Checklist for New Laptop

Print this or keep it open while setting up on your new laptop!

---

## ✅ Pre-Transfer (Current Laptop)

- [ ] Verify `firebase-credentials.json` is in the project folder
- [ ] Save your OpenAI API key somewhere secure (you'll need it)
- [ ] Zip the entire `menti_chatbot` folder
- [ ] Transfer ZIP to new laptop (USB, email, cloud, etc.)

---

## ✅ On New Laptop

### 1. Prerequisites
- [ ] Install Python 3.8+ from https://www.python.org/downloads/
  - [ ] ⚠️ Check "Add Python to PATH" during installation
- [ ] Extract the ZIP file to desired location

### 2. Create .env File
- [ ] Create new file named `.env` in project root
- [ ] Copy contents from `.env.example`
- [ ] Add your OpenAI API key
- [ ] Add a random SECRET_KEY

### 3. Install & Run
- [ ] Open PowerShell in project folder
- [ ] Run: `.\setup.bat`
- [ ] Wait for installation to complete
- [ ] Run: `python app.py`
- [ ] Open browser to `http://localhost:5000`

### 4. Test
- [ ] Login page loads
- [ ] Can create account or use guest mode
- [ ] Can send messages
- [ ] Bot responds correctly

---

## 🔑 Information You'll Need

**OpenAI API Key:**
- Get from: https://platform.openai.com/api-keys
- Paste into `.env` file

**SECRET_KEY:**
- Generate random string (20+ characters)
- Example: `a8f5d9c2e7b4f1a3d6c9e2f8b5a1c4d7`

---

## 🆘 If Something Goes Wrong

### Error: "Python not found"
→ Install Python and restart PowerShell

### Error: "No module named 'flask'"
→ Run: `pip install -r requirements.txt`

### Error: "Firebase credentials not found"
→ Check `firebase-credentials.json` is in folder

### Error: "Invalid OpenAI API key"
→ Check your `.env` file has correct key

---

## ⏱️ Estimated Time
- First time setup: **5-10 minutes**
- If Python already installed: **2-3 minutes**

---

**Ready? Open `SETUP_NEW_LAPTOP.md` for detailed instructions!**
