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

def _strip_html(raw: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', raw)
    return re.sub(r'[ \t]+', ' ', text)


def _decode_payload(payload: dict) -> tuple[str, str]:
    """Recursively extract (html_text, plain_text) from a MIME payload.

    Returns a tuple so the caller can always prefer HTML — Capital One
    emails are rich HTML marketing messages. The plain text alternative
    inflates length with huge JWT URLs, so 'return longest' wrongly
    picks plain text. Callers should use html_text and fall back to
    plain_text only when html_text is empty.
    """
    mime = payload.get('mimeType', '')

    if mime == 'text/html':
        data = payload.get('body', {}).get('data', '')
        if data:
            raw = base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='ignore')
            return _strip_html(raw), ''

    if mime == 'text/plain':
        data = payload.get('body', {}).get('data', '')
        if data:
            return '', base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='ignore')

    # Multipart: recurse and accumulate both types across all parts
    html_acc, plain_acc = '', ''
    for part in payload.get('parts', []):
        h, p = _decode_payload(part)
        html_acc  += h
        plain_acc += p

    return html_acc, plain_acc


def get_body(payload: dict) -> str:
    """Return the best body text for offer extraction: HTML first, plain fallback."""
    html, plain = _decode_payload(payload)
    return html or plain


# ---------------------------------------------------------------------------
# Multi-offer extraction from body
# ---------------------------------------------------------------------------

# Stores/phrases that appear as branding noise in every Capital One email
_SKIP_STORES = {'capital one', 'capital one shopping', 'shopping'}

# Section headers that mark the start of low-quality / generic offers to ignore
_SECTION_CUTOFFS = re.compile(
    r"today'?s?\s+top\s+deals|more\s+ways\s+to\s+earn|you\s+might\s+also\s+like|"
    r"featured\s+offers|popular\s+stores|browse\s+more",
    re.IGNORECASE,
)

# Pass 1 — find every cashback percentage in the body
_PCT_VALUE_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:in\s+Rewards?|back|Rewards?)',
    re.IGNORECASE,
)

# Pass 2 — find every "at {STORE}" anchor
_AT_ANCHOR_RE = re.compile(
    r'\bat\s+([\w][\w &\.\'\-]{2,38}?)(?=\s*[.,]|\s+(?:You|Up|Get|Activate|Earn|Shop|We|\n)|$)',
    re.IGNORECASE,
)

# "Earn $X in Rewards at {STORE}"
_DOLLAR_AT_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?\$(\d+(?:\.\d+)?)'
    r'\s+(?:in\s+)?Rewards?\s+at\s+([\w][\w &\.\'\-]{2,38}?)(?=\.|,|\s+You\s|\s+Get\s|\n|$)',
    re.IGNORECASE,
)

# "Earn X% back" for logo-preceded single-store emails (plain text format)
_PCT_BACK_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:back|in\s+Rewards?|Rewards?)',
    re.IGNORECASE,
)

# "{Store Name} logo"
_LOGO_RE = re.compile(r'([A-Z][\w &\'\-]{1,38}?)\s+logo\b', re.IGNORECASE)

# Generic non-store phrases to reject as store names
_GENERIC_PHRASES = {
    'hotel', 'hotels', 'hotel bookings', 'event tickets', 'events',
    'travel', 'flights', 'car rentals', 'gift cards',
}


def _collapse_urls(text: str) -> str:
    """Replace long URLs with a short placeholder so proximity matching works.

    Capital One redirect URLs contain JWT tokens (500-1000 chars) that sit
    between '{Store} logo' and 'Earn X% back', breaking window-based search.
    """
    return re.sub(r'https?://\S{30,}', '[URL]', text)


def _extract_offers_from_body(body: str, received: datetime, thread_id: str) -> list[dict]:
    """Return one dict per cashback offer found in the email body."""
    body = _normalize(body)
    body = _collapse_urls(body)

    # Truncate at section headers that introduce generic/low-quality offers
    cutoff = _SECTION_CUTOFFS.search(body)
    if cutoff:
        body = body[:cutoff.start()]

    gmail_url   = f'https://mail.google.com/mail/u/0/#all/{thread_id}'
    received_str = received.strftime('%b %d, %Y  %H:%M UTC')
    seen: set[tuple] = set()
    results = []

    def _pct_label(num: float, pre_text: str) -> str:
        prefix = 'Up to ' if re.search(r'up\s+to\s*$', pre_text.strip(), re.IGNORECASE) else ''
        return f"{prefix}{num:g}%"

    def add(cashback_num: float, label: str, store: str) -> None:
        store = _clean_store(store)
        if len(store) < 3 or len(store) > 44:
            return
        if 'capital one' in store.lower() or store.lower() in _SKIP_STORES:
            return
        if store.lower() in _GENERIC_PHRASES:
            return
        if cashback_num <= 0 or cashback_num > 100:
            return
        key = (store.lower(), round(cashback_num, 2))
        if key in seen:
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

    # -----------------------------------------------------------------------
    # Strategy A — two-pass pairing (handles variable gap between % and store)
    #
    # Single-regex "Earn X%...{gap}...at {STORE}" breaks when the gap in the
    # stripped HTML exceeds the window size and the regex skips to the NEXT
    # "at {STORE}" block (wrong store). Two-pass solves this:
    #   Pass 1: record every "Earn X%" position
    #   Pass 2: record every "at {STORE}" position
    #   Pair: each % → nearest following store anchor
    # -----------------------------------------------------------------------
    pct_hits   = [(m.start(), m.end(), float(m.group(1)))
                  for m in _PCT_VALUE_RE.finditer(body)]
    store_hits = [(m.start(), _clean_store(m.group(1).strip()))
                  for m in _AT_ANCHOR_RE.finditer(body)]

    for pct_start, pct_end, num in pct_hits:
        # Nearest "at {STORE}" that starts AFTER this % token
        following = [(s, name) for s, name in store_hits if s >= pct_end]
        if not following:
            continue
        _, store = min(following, key=lambda x: x[0])
        pre = body[max(0, pct_start - 15): pct_start]
        add(num, _pct_label(num, pre), store)

    # -----------------------------------------------------------------------
    # Strategy B — logo-preceded single-store emails (plain text format)
    # "{Store} logo [URL] Earn X% back"
    # -----------------------------------------------------------------------
    for logo_m in _LOGO_RE.finditer(body):
        store_candidate = logo_m.group(1).strip()
        if 'capital one' in store_candidate.lower():
            continue
        window = body[logo_m.end(): logo_m.end() + 300]
        pct_m = _PCT_BACK_RE.search(window)
        if pct_m:
            num = float(pct_m.group(1))
            pre = window[:pct_m.start()]
            add(num, _pct_label(num, pre), store_candidate)

    # -----------------------------------------------------------------------
    # Strategy C — "Earn $X in Rewards at {STORE}"
    # -----------------------------------------------------------------------
    for m in _DOLLAR_AT_RE.finditer(body):
        add(float(m.group(1)), f"Up to ${m.group(1)} back", m.group(2).strip())

    return results


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

def fetch_capital_one_offers(
    days: int = 3,
    credentials_path: str = 'credentials.json',
    token_path: str = 'token.json',
    debug: bool = False,
) -> list[dict] | tuple[list[dict], list[dict]]:
    """Fetch offers. If debug=True, also return raw body samples for inspection."""
    service = get_gmail_service(credentials_path, token_path)

    query = f'from:{SENDER} newer_than:{days}d'
    results = service.users().threads().list(
        userId='me', q=query, maxResults=100
    ).execute()
    threads = results.get('threads', [])

    all_offers: list[dict] = []
    debug_samples: list[dict] = []

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

        body = get_body(msg.get('payload', {}))
        if body:
            offers = _extract_offers_from_body(body, received, thread_id)
            all_offers.extend(offers)
            if debug and len(debug_samples) < 3:
                html_raw, plain_raw = _decode_payload(msg.get('payload', {}))
                debug_samples.append({
                    'subject':      headers.get('Subject', ''),
                    'html_len':     len(html_raw),
                    'plain_len':    len(plain_raw),
                    'body_used':    'html' if html_raw else 'plain',
                    'body_snippet': body[:3000],
                    'offers_found': [o['Store'] + ' ' + o['Cashback'] for o in offers],
                })

    all_offers.sort(key=lambda x: (-x['Cashback_num'], x['Store'].lower()))
    if debug:
        return all_offers, debug_samples
    return all_offers
