@echo off
echo ========================================
echo MENTI CHATBOT - Quick Start
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8 or higher
    pause
    exit /b 1
)

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [2/3] Running setup verification test...
python test_setup.py

echo.
echo [3/3] Setup complete!
echo.
echo ========================================
echo IMPORTANT: Before running the app
echo ========================================
echo 1. Download firebase-credentials.json from Firebase Console
echo 2. Update Firebase Web API Key in login.html and chat.html
echo 3. Enable Authentication and Firestore in Firebase Console
echo.
echo See SETUP.md for detailed instructions
echo ========================================
echo.
echo To start the application, run:
echo    python app.py
echo.
pause
