#!/bin/bash
# TSUNAMI — Agentic Reborn
# One-command setup script
#
# Usage:
#   bash setup.sh              # Install deps + verify
#   bash setup.sh --with-ollama  # Also install Ollama + pull model
#   bash setup.sh --with-browser # Also install Playwright + Chromium

set -e

echo "================================================"
echo "  TSUNAMI — Agentic Reborn"
echo "  Setting up the standing wave..."
echo "================================================"
echo ""

# Core dependencies
echo "[1/4] Installing core dependencies..."
pip install -q httpx pyyaml rich 2>/dev/null || pip3 install -q httpx pyyaml rich

# Optional: search
echo "[2/4] Installing search backend..."
pip install -q duckduckgo-search 2>/dev/null || pip3 install -q duckduckgo-search 2>/dev/null || echo "  (duckduckgo-search failed — search will use HTTP fallback)"

# Optional: Ollama
if [[ "$*" == *"--with-ollama"* ]]; then
    echo "[3/4] Installing Ollama..."
    if ! command -v ollama &> /dev/null; then
        curl -fsSL https://ollama.ai/install.sh | sh
    else
        echo "  Ollama already installed"
    fi
    echo "  Pulling model (this may take a while)..."
    ollama pull qwen2.5:7b  # Start with 7B for testing
    echo "  For full capability: ollama pull qwen2.5:72b"
else
    echo "[3/4] Skipping Ollama (use --with-ollama to install)"
fi

# Optional: Browser
if [[ "$*" == *"--with-browser"* ]]; then
    echo "[3b] Installing Playwright + Chromium..."
    pip install -q playwright 2>/dev/null || pip3 install -q playwright
    playwright install chromium
else
    echo "  Skipping browser (use --with-browser to install Playwright)"
fi

# Verify
echo "[4/4] Running verification..."
echo ""
python3 verify.py

echo ""
echo "================================================"
echo "  Setup complete."
echo ""
echo "  To start Tsunami:"
echo "    python3 run.py                    # Interactive"
echo "    python3 run.py --task 'Do X'      # Single task"
echo ""
echo "  To configure:"
echo "    Edit config.yaml"
echo "    Or set TSUNAMI_* environment variables"
echo "================================================"
