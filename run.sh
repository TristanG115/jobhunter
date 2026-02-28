#!/bin/bash
# JobHunter Setup & Run Script

echo "🔧 Setting up JobHunter..."

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

source venv/bin/activate
pip install -r requirements.txt --quiet
echo "✓ Dependencies installed"

mkdir -p data uploads

# Initialize / migrate database
python3 -c "import app; app.init_db()"
echo "✓ Database initialized"

echo ""
echo "🚀 Starting JobHunter on http://0.0.0.0:5000"
echo "   Open http://localhost:5000 in your browser"
echo "   Press Ctrl+C to stop"
echo ""
python3 app.py
