"""Gmail fetcher + offer parser for Capital One Shopping emails."""

import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
SENDER = 'hello@capitaloneshopping.com'

TYPE_LABELS = {
    'price':    '📉 Price Drop',
    'expiring': '⏰ Expiring Soon',
    'activate': '🔔 Activate Rewards',
    'offer':    '🎯 Offer Alert',
    'coupon':   '🎟 Coupon',
}


def _load_secrets():
    """Return Streamlit secrets dict if available, else None."""
    try:
        import streamlit as st
        if 'GOOGLE_TOKEN' in st.secrets:
            return st.secrets
    except Exception:
        pass
    return None


def _creds_from_secrets(secrets) -> Credentials:
    """Build Credentials from Streamlit secrets. Refreshes if expired."""
    token = json.loads(secrets['GOOGLE_TOKEN'])
    creds = Credentials(
        token=token.get('token'),
        refresh_token=token.get('refresh_token'),
        token_uri=token.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=token.get('client_id'),
        client_secret=token.get('client_secret'),
        scopes=token.get('scopes'),
    )
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds.valid:
        raise RuntimeError(
            "Stored token is invalid and cannot be refreshed. "
            "Re-run setup_secrets.py locally and update the GOOGLE_TOKEN secret."
        )
    return creds


def get_gmail_service(credentials_path='credentials.json', token_path='token.json'):
    # --- Streamlit Cloud: read from secrets ---
    secrets = _load_secrets()
    if secrets:
        creds = _creds_from_secrets(secrets)
        return build('gmail', 'v1', credentials=creds)

    # --- Local: file-based OAuth ---
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(credentials_path)
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def extract_store(subject: str, snippet: str) -> str:
    """Extract retailer name. Snippet 'at {STORE}' patterns are most reliable."""

    # Snippet: "Earn X% in Rewards, Up to $1000 at {STORE}." / "Earn X% back at {STORE}"
    m = re.search(
        r'(?:Earn|Get)\s+(?:up\s+to\s+)?[\$\d][\d,\.]*\s*%?.*?\bat\s+([\w][\w\s&\.\-\']*?)(?:\s*\.|,|\s+You\s|\s+Get\s|\s*$)',
        snippet, re.IGNORECASE
    )
    if m:
        store = m.group(1).strip()
        if 3 < len(store) < 45:
            return _clean_store(store)

    # Subject: "Offer at {STORE} for you!"
    m = re.search(r'[Oo]ffer\s+at\s+(.+?)\s+for\s+you', subject, re.IGNORECASE)
    if m:
        return _clean_store(m.group(1))

    # Subject: "Your X% Reward at {STORE}"
    m = re.search(r'[Yy]our\s+[\d\.]+%.*?\bat\s+(.+?)(?:[!,]|$)', subject)
    if m:
        return _clean_store(m.group(1))

    # Subject: "Expiring soon: X% back at {STORE}!"
    m = re.search(r'[Ee]xpiring\s+soon:.*?\bat\s+(.+?)(?:[!,]|$)', subject)
    if m:
        return _clean_store(m.group(1))

    # Subject: "New Rewards Offer: ... at {STORE}!"
    m = re.search(r'[Nn]ew Rewards Offer:.*?\bat\s+(.+?)(?:[!,]|$)', subject)
    if m:
        return _clean_store(m.group(1))

    # Subject: "Offer alert: ... at {STORE}[,!]"
    m = re.search(r'[Oo]ffer\s+alert:.*?\bat\s+([\w][\w\s&\.\-]+?)(?:[,!]|$)', subject)
    if m:
        return _clean_store(m.group(1))

    # Subject: "{STORE} Offer: ..."
    m = re.search(r'^([\w][\w\s&\.\-]+?)\s+Offer:', subject)
    if m:
        return _clean_store(m.group(1))

    # Subject: "- {STORE}.com!" or "| {STORE}!" at end
    m = re.search(r'[-|]\s+([\w][\w\s&\.\-]+?)(?:\.com)?[!]?\s*$', subject)
    if m:
        return _clean_store(m.group(1))

    return 'Unknown'


def _clean_store(name: str) -> str:
    name = name.strip().rstrip('!')
    name = re.sub(r'\.com$', '', name, flags=re.IGNORECASE)
    return name


def extract_cashback(subject: str, snippet: str) -> tuple[float, str]:
    """Return (numeric_value_for_sorting, display_label)."""
    combined = snippet + ' ' + subject

    # Price drop amount
    drop_m = re.search(r'Price Drop \$?([\d,]+(?:\.\d+)?)', combined, re.IGNORECASE)
    drop_label = f"${drop_m.group(1).replace(',','')} off + " if drop_m else ''

    # Percentage
    pct_m = (
        re.search(r'(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:back|in Rewards|Rewards?)', combined, re.IGNORECASE)
        or re.search(r'(?:Earn|Get)\s+(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*%', combined, re.IGNORECASE)
    )

    # Dollar amount back
    dollar_m = re.search(
        r'(?:up\s+to\s+)?\$(\d+(?:\.\d+)?)\s*(?:back|in Rewards)',
        combined, re.IGNORECASE
    )

    if pct_m:
        val = float(pct_m.group(1))
        # Detect "up to" prefix
        pre_text = combined[:combined.lower().find(pct_m.group(0).lower())]
        prefix = 'Up to ' if re.search(r'up\s+to\s*$', pre_text.strip(), re.IGNORECASE) else ''
        return val, f"{drop_label}{prefix}{pct_m.group(1)}%"

    if dollar_m:
        val = float(dollar_m.group(1))
        return val, f"{drop_label}${dollar_m.group(1)} back"

    return 0.0, '—'


def classify_type(subject: str, snippet: str) -> str:
    s = subject.lower()
    if 'price drop' in s:
        return 'price'
    if 'expiring soon' in s:
        return 'expiring'
    if 'activate rewards' in s:
        return 'activate'
    if 'offer alert' in s or 'new rewards offer' in s:
        return 'offer'
    if 'coupon' in snippet.lower():
        return 'coupon'
    return 'offer'


def extract_expiry(snippet: str) -> str:
    # "Rewards offer ends on June 1, 2026"
    m = re.search(r'ends\s+on\s+(\w+\s+\d+,?\s*\d{4})', snippet, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # "Expiring Jun 1"
    m = re.search(r'[Ee]xpir(?:ing|es?)\s+(\w{3,9}\s+\d+)', snippet)
    if m:
        return m.group(1).strip()
    if 'single-use' in snippet.lower():
        return 'Single-use'
    return '—'


def _parse_date(date_str: str) -> datetime:
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

def fetch_capital_one_offers(
    days: int = 7,
    credentials_path: str = 'credentials.json',
    token_path: str = 'token.json',
) -> list[dict]:
    service = get_gmail_service(credentials_path, token_path)

    query = f'from:{SENDER} newer_than:{days}d'
    results = service.users().threads().list(
        userId='me', q=query, maxResults=100
    ).execute()
    threads = results.get('threads', [])

    offers = []
    for thread in threads:
        thread_id = thread['id']
        data = service.users().threads().get(
            userId='me',
            id=thread_id,
            format='metadata',
            metadataHeaders=['Subject', 'From', 'Date'],
        ).execute()

        messages = data.get('messages', [])
        if not messages:
            continue

        msg = messages[0]
        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        snippet = msg.get('snippet', '')
        subject = headers.get('Subject', '')
        received = _parse_date(headers.get('Date', ''))

        offer_type = classify_type(subject, snippet)
        cashback_num, cashback_label = extract_cashback(subject, snippet)
        store = extract_store(subject, snippet)
        expiry = extract_expiry(snippet)

        offers.append({
            'Store':        store,
            'Cashback':     cashback_label,
            'Cashback_num': cashback_num,
            'Type':         TYPE_LABELS.get(offer_type, offer_type),
            'Expiry':       expiry,
            'Received':     received.strftime('%b %d, %Y  %H:%M UTC'),
            'Received_dt':  received,
            'Email':        f'https://mail.google.com/mail/u/0/#all/{thread_id}',
        })

    offers.sort(key=lambda x: (-x['Cashback_num'], x['Store'].lower()))
    return offers
