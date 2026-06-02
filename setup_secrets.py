#!/usr/bin/env python3
"""
Run this locally after your first Gmail authorization to generate Streamlit secrets.

    python3.11 setup_secrets.py

It writes .streamlit/secrets.toml (used when testing locally) and prints
the exact block to paste into Streamlit Cloud → App settings → Secrets.
"""
import json
import os
import sys

CREDS_PATH = 'credentials.json'
TOKEN_PATH  = 'token.json'

def main():
    if not os.path.exists(TOKEN_PATH):
        sys.exit(
            f"✗ {TOKEN_PATH} not found.\n"
            "Run the app locally first so Gmail OAuth can complete:\n"
            "  python3.11 -m streamlit run app.py\n"
            "Then click 🔄 Refresh and authorize in the browser."
        )

    creds = json.load(open(CREDS_PATH))
    token = json.load(open(TOKEN_PATH))

    # Encode both as compact JSON strings for TOML
    creds_str = json.dumps(json.dumps(creds, separators=(',', ':')))
    token_str  = json.dumps(json.dumps(token, separators=(',', ':')))

    secrets_toml = f"GOOGLE_CREDENTIALS = {creds_str}\nGOOGLE_TOKEN = {token_str}\n"

    os.makedirs('.streamlit', exist_ok=True)
    with open('.streamlit/secrets.toml', 'w') as f:
        f.write(secrets_toml)

    print("✓ .streamlit/secrets.toml written (local Streamlit will pick this up automatically)\n")
    print("─" * 64)
    print("Paste this into Streamlit Cloud → App settings → Secrets:")
    print("─" * 64)
    print(secrets_toml)

if __name__ == '__main__':
    main()
