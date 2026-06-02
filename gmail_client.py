"""Gmail fetcher + offer parser for Capital One Shopping emails."""

import base64
import html
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

def _normalize(text: str) -> str:
    """Decode HTML entities and replace Unicode lookalike punctuation."""
    text = html.unescape(text)
    return (text
            .replace("․", ".")
            .replace("·", ".")
            .replace("’", "'")
            .replace("‘", "'"))


def extract_store(subject: str, snippet: str) -> str:
    """Extract retailer name. Tries patterns from most to least reliable."""
    subject = _normalize(subject)
    snippet = _normalize(snippet)

    # --- Snippet patterns ---
    snippet_pats = [
        # "Up to $1000 at {STORE}."  (very common in Capital One snippets)
        r'Up to \$[\d,]+\s+at\s+([\w][\w\s&\.\'\-]+?)(?:\.|,|\s+You|\s+Get)',
        # "Earn X% back at {STORE}" / "Earn X% in Rewards ... at {STORE}"
        r'(?:Earn|Get)\s+(?:up\s+to\s+)?[\$\d][\d,\.]*\s*%?\s+(?:back|in\s+Rewards|Rewards)\s+(?:too\s+)?at\s+([\w][\w\s&\.\'\-]+?)(?:\.|,|\s+You|\s+Get)',
        r'(?:Earn|Get)\s+(?:up\s+to\s+)?[\$\d][\d,\.]*\s*%?.*?\bat\s+([\w][\w\s&\.\'\-]+?)(?:\.|,|\s+You\s|\s+Get\s)',
        # "back at {STORE}" / "Rewards at {STORE}"
        r'(?:back|Rewards?)\s+at\s+([\w][\w\s&\.\'\-]+?)(?:\.|,|\s+You|\s+Get)',
    ]
    for pat in snippet_pats:
        m = re.search(pat, snippet, re.IGNORECASE)
        if m:
            s = _clean_store(m.group(1))
            if 2 < len(s) < 45:
                return s

    # --- Subject patterns ---
    subject_pats = [
        r'[Oo]ffer\s+at\s+(.+?)\s+for\s+you',                         # "Offer at {STORE} for you"
        r'[Yy]our\s+[\d\.]+%.*?\bat\s+(.+?)(?:[!,]|$)',               # "Your X% Reward at {STORE}"
        r'[Ee]xpiring\s+soon:.*?\bat\s+(.+?)(?:[!,]|$)',              # "Expiring soon: ... at {STORE}"
        r'[Nn]ew\s+[Rr]ewards\s+[Oo]ffer:.*?\bat\s+(.+?)(?:[!,]|$)', # "New Rewards Offer: at {STORE}"
        r'[Oo]ffer\s+alert:.*?\bat\s+([\w][\w\s&\.\'\-]+?)(?:[,!]|$)',# "Offer alert: ... at {STORE}"
        r'^([\w][\w\s&\.\'\-]+?)\s+[Oo]ffer:',                        # "{STORE} Offer: ..."
        r'[Ss]ave\s+on\s+.+?\bat\s+([\w][\w\s&\.\'\-]+?)(?:[,!]|$)', # "Save on X at {STORE}"
        r'[-|]\s+([\w][\w\s&\.\'\-]+?)(?:\.com)?[!]?\s*$',            # "- QVC.com!" at end
        r'\bat\s+([\w][\w\s&\.\'\-]{2,30}?)(?:[!,\.]|$)',             # broad "at {STORE}"
        r'[Ss]ave\s+on\s+([A-Z]\w+)',                                  # "Save on {Brand} product…" (no store suffix)
    ]
    for pat in subject_pats:
        m = re.search(pat, subject, re.IGNORECASE)
        if m:
            s = _clean_store(m.group(1))
            if 2 < len(s) < 45:
                return s

    return 'Unknown'


def _clean_store(name: str) -> str:
    name = name.strip().rstrip('!')
    name = re.sub(r'\.com$', '', name, flags=re.IGNORECASE)
    return name


def _parse_date(date_str: str) -> datetime:
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Body decoding
# ---------------------------------------------------------------------------

def _decode_payload(payload: dict) -> str:
    """Recursively extract text from a MIME payload.

    Capital One emails are HTML-only marketing messages — the text/plain
    alternative is usually just "View this email in your browser".
    We collect ALL parts and return whichever has the most content so the
    regex sees the full offer list from the HTML body.
    """
    mime = payload.get('mimeType', '')

    if mime == 'text/plain':
        data = payload.get('body', {}).get('data', '')
        if data:
            return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='ignore')

    if mime == 'text/html':
        data = payload.get('body', {}).get('data', '')
        if data:
            text = base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='ignore')
            text = re.sub(r'<[^>]+>', ' ', text)   # strip tags
            text = re.sub(r'[ \t]+', ' ', text)     # collapse whitespace
            return text

    # Multipart: collect all parts, return the longest (most content)
    candidates = []
    for part in payload.get('parts', []):
        result = _decode_payload(part)
        if result:
            candidates.append(result)

    return max(candidates, key=len) if candidates else ''


# ---------------------------------------------------------------------------
# Multi-offer extraction from body
# ---------------------------------------------------------------------------

# Patterns that yield (cashback_num_group, label_suffix, store_group)
_BODY_PCT_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*%'
    r'\s*(?:in\s+Rewards?|back|Rewards?).{0,80}?'
    r'\bat\s+([\w][\w\s&\.\'\-]{2,38}?)(?=\.|,|\s+You\s|\s+Up\s|\s+Get\s|\s+Activate|\n|$)',
    re.IGNORECASE | re.DOTALL,
)

_BODY_DOLLAR_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?\$(\d+(?:\.\d+)?)'
    r'\s+(?:in\s+)?Rewards?\s+at\s+([\w][\w\s&\.\'\-]{2,38}?)(?=\.|,|\s+You\s|\s+Get\s|\n|$)',
    re.IGNORECASE,
)


def _extract_offers_from_body(body: str, received: datetime, thread_id: str) -> list[dict]:
    """Return one dict per cashback offer found in the email body."""
    body = _normalize(body)
    gmail_url = f'https://mail.google.com/mail/u/0/#all/{thread_id}'
    received_str = received.strftime('%b %d, %Y  %H:%M UTC')
    seen: set[tuple] = set()
    results = []

    def add(cashback_num: float, label: str, store: str) -> None:
        store = _clean_store(store)
        key = (store.lower(), round(cashback_num, 2))
        if len(store) < 3 or len(store) > 44 or key in seen:
            return
        seen.add(key)
        results.append({
            'Store':        store,
            'Cashback':     label,
            'Cashback_num': cashback_num,
            'Received':     received_str,
            'Received_dt':  received,
            'Email':        gmail_url,
        })

    for m in _BODY_PCT_RE.finditer(body):
        num = float(m.group(1))
        pre = body[max(0, m.start() - 15): m.start()]
        prefix = 'Up to ' if re.search(r'up\s+to\s*$', pre, re.IGNORECASE) else ''
        add(num, f"{prefix}{m.group(1)}%", m.group(2).strip())

    for m in _BODY_DOLLAR_RE.finditer(body):
        add(float(m.group(1)), f"Up to ${m.group(1)} back", m.group(2).strip())

    return results


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

def fetch_capital_one_offers(
    days: int = 3,
    credentials_path: str = 'credentials.json',
    token_path: str = 'token.json',
) -> list[dict]:
    service = get_gmail_service(credentials_path, token_path)

    query = f'from:{SENDER} newer_than:{days}d'
    results = service.users().threads().list(
        userId='me', q=query, maxResults=100
    ).execute()
    threads = results.get('threads', [])

    all_offers: list[dict] = []
    for thread in threads:
        thread_id = thread['id']
        data = service.users().threads().get(
            userId='me',
            id=thread_id,
            format='full',
        ).execute()

        messages = data.get('messages', [])
        if not messages:
            continue

        msg = messages[0]
        headers = {h['name']: h['value']
                   for h in msg.get('payload', {}).get('headers', [])}
        received = _parse_date(headers.get('Date', ''))

        body = _decode_payload(msg.get('payload', {}))
        if body:
            all_offers.extend(_extract_offers_from_body(body, received, thread_id))

    all_offers.sort(key=lambda x: (-x['Cashback_num'], x['Store'].lower()))
    return all_offers
