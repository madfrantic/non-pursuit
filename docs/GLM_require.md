# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install PySide6 cryptography APScheduler

# Optional: For browser automation (when implementing handlers)
pip install playwright
playwright install

# Run the application
python main.py --password "your-secure-password"

# Or with default password (less secure)
python main.py
