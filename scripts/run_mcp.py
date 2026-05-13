#!/usr/bin/env python3
"""Run the minimal NotebookLM MCP wrapper via Uvicorn.

Usage:
    python scripts/run_mcp.py

Default listens on 127.0.0.1:8000 so you can expose it safely via ngrok.
"""

import os
import sys

# Add src to path if running from root
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from notebooklm.mcp_server import main

if __name__ == "__main__":
    main()
