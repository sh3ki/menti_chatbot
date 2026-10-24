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
echo [2/3] Creating MySQL tables and seeding the configured administrator...
python seed_database.py
if errorlevel 1 (
    echo ERROR: MySQL setup failed. Start Laragon MySQL and configure DATABASE_URL in .env
    pause
    exit /b 1
)

echo.
echo [3/3] Setup complete!
echo.
echo ========================================
echo IMPORTANT: Before running the app
echo ========================================
echo 1. Create the menti_chatbot database in Laragon MySQL
echo 2. Copy .env.example to .env and set Google OAuth values
echo 3. Register /auth/google/callback in your Google OAuth client
echo.
echo See MYSQL_GOOGLE_SETUP.md for detailed instructions
echo ========================================
echo.
echo To start the application, run:
echo    python app.py
echo.
pause
