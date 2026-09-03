import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.main import app
import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Vision AI Backend is RUNNING on http://127.0.0.1:8000")
    print("   Open your browser or frontend now!")
    print("   (Press Ctrl+C to stop)")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=8000)