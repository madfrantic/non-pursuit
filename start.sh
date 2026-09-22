#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
elif [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

# Export environment variables from .env if present
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "Starting FastAPI remediation/scanning backend on port 8000..."
uvicorn api.main:app --host 0.0.0.0 --port 8000 > "$DIR/api.log" 2>&1 &
API_PID=$!

echo "Starting Streamlit UI on port 8501..."
streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --server.headless=true \
    --server.enableCORS=false \
    --browser.gatherUsageStats=false > "$DIR/streamlit.log" 2>&1 &
STREAMLIT_PID=$!

cleanup() {
    echo "Shutting down services (API: $API_PID, UI: $STREAMLIT_PID)..."
    kill $API_PID $STREAMLIT_PID 2>/dev/null || true
    wait $API_PID 2>/dev/null || true
    wait $STREAMLIT_PID 2>/dev/null || true
}
trap cleanup SIGINT SIGTERM

echo "Non-Pursuit (Sovereign Agent) is running!"
echo "API Backend: http://localhost:8000"
echo "Streamlit UI: http://localhost:8501"

wait $STREAMLIT_PID $API_PID
