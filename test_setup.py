"""
Test script to verify Menti Chatbot setup
Run this to check if all dependencies and configurations are correct
"""

import sys
import os

def test_imports():
    """Test if all required packages are installed"""
    print("🔍 Testing Python imports...")
    
    try:
        import flask
        print("✅ Flask installed:", flask.__version__)
    except ImportError:
        print("❌ Flask not installed")
        return False
    
    try:
        import flask_cors
        print("✅ Flask-CORS installed")
    except ImportError:
        print("❌ Flask-CORS not installed")
        return False
    
    try:
        import openai
        print("✅ OpenAI installed:", openai.__version__)
    except ImportError:
        print("❌ OpenAI not installed")
        return False
    
    try:
        import firebase_admin
        print("✅ Firebase Admin installed")
    except ImportError:
        print("❌ Firebase Admin not installed")
        return False
    
    try:
        from dotenv import load_dotenv
        print("✅ python-dotenv installed")
    except ImportError:
        print("❌ python-dotenv not installed")
        return False
    
    return True


def test_env_file():
    """Test if .env file exists and has required variables"""
    print("\n🔍 Testing .env configuration...")
    
    if not os.path.exists('.env'):
        print("❌ .env file not found")
        return False
    
    print("✅ .env file exists")
    
    from dotenv import load_dotenv
    load_dotenv()
    
    openai_key = os.getenv('OPENAI_API_KEY')
    if openai_key and len(openai_key) > 20:
        print("✅ OPENAI_API_KEY configured")
    else:
        print("❌ OPENAI_API_KEY not properly configured")
        return False
    
    return True


def test_firebase_credentials():
    """Test if Firebase credentials file exists"""
    print("\n🔍 Testing Firebase credentials...")
    
    cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH', 'firebase-credentials.json')
    
    if os.path.exists(cred_path):
        print(f"✅ Firebase credentials file found: {cred_path}")
        return True
    else:
        print(f"⚠️  Firebase credentials not found: {cred_path}")
        print("   Download it from Firebase Console → Project Settings → Service Accounts")
        return False


def test_templates():
    """Test if template files exist"""
    print("\n🔍 Testing template files...")
    
    templates = ['templates/login.html', 'templates/chat.html']
    all_exist = True
    
    for template in templates:
        if os.path.exists(template):
            print(f"✅ {template} exists")
        else:
            print(f"❌ {template} not found")
            all_exist = False
    
    return all_exist


def test_openai_connection():
    """Test if OpenAI API key is valid"""
    print("\n🔍 Testing OpenAI connection...")
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        import openai
        openai.api_key = os.getenv('OPENAI_API_KEY')
        
        # Try a simple API call
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5
        )
        
        print("✅ OpenAI API connection successful")
        return True
    
    except Exception as e:
        print(f"❌ OpenAI API connection failed: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 60)
    print("🧪 MENTI CHATBOT - SETUP VERIFICATION TEST")
    print("=" * 60)
    
    results = []
    
    # Test 1: Python imports
    results.append(test_imports())
    
    # Test 2: .env file
    results.append(test_env_file())
    
    # Test 3: Firebase credentials
    firebase_result = test_firebase_credentials()
    results.append(firebase_result)
    
    # Test 4: Template files
    results.append(test_templates())
    
    # Test 5: OpenAI connection (optional, may use API credits)
    print("\n🔍 Do you want to test OpenAI API connection? (uses API credits)")
    test_api = input("Test OpenAI? (y/n): ").lower().strip() == 'y'
    if test_api:
        results.append(test_openai_connection())
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 TEST SUMMARY")
    print("=" * 60)
    
    if all(results):
        print("✅ All tests passed! You're ready to run the application.")
        print("\nRun: python app.py")
    else:
        print("⚠️  Some tests failed. Please check the errors above.")
        print("\nRefer to SETUP.md for detailed setup instructions.")
    
    print("=" * 60)


if __name__ == '__main__':
    main()
