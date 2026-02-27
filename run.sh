#!/bin/bash
# JobHunter Setup & Run Script

echo "🔧 Setting up JobHunter..."

# Create virtual environment if needed
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

# Activate and install
source venv/bin/activate
pip install -r requirements.txt --quiet
echo "✓ Dependencies installed"

# Initialize database
python3 -c "import app; app.init_db()" 2>/dev/null || true
echo "✓ Database initialized"

echo ""
echo "🚀 Starting JobHunter on http://0.0.0.0:5000"
echo "   Open http://localhost:5000 in your browser"
echo "   Press Ctrl+C to stop"
echo ""
python3 app.py
