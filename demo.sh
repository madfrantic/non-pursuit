#!/bin/bash
# Non-Pursuit Demo Launch Script
# Activates virtual environment and runs app with presentation-safe flags

cd "$(dirname "$0")" || exit 1

# Activate virtual environment
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
else
    echo "Error: Virtual environment not found at venv/"
    exit 1
fi

# Launch Streamlit with production/demo flags
streamlit run app.py \
    --client.toolbarMode=viewer \
    --server.headless=false \
    --global.developmentMode=false \
    --logger.level=error
