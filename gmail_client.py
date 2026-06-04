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
    name = name.strip()
    # Strip product-description junk like "Lunar New Year... Was 2% back"
    name = re.sub(r'\s+[Ww]as\s+.*', '', name)
    # Strip trailing ellipsis (… or ...) and anything after it
    name = re.sub(r'\s*….*$', '', name)
    name = re.sub(r'\s*\.{2,}.*$', '', name)
    # Strip .com suffix, then any remaining trailing punctuation
    name = re.sub(r'\.com$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[.,!?]+$', '', name)
    return name.strip()


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

# Strategy A1: "Earn X% back at WORD WORD" — exactly 2 words, strong separator required.
# "…" is intentionally excluded: in Capital One emails it appears AFTER the product
# description, not immediately after the store, so including it would wrongly capture
# "MomCozy SomePump" instead of stopping at just "MomCozy".
# Stops at: word+colon ("Plus:"), pipe, comma, period+space, "Was", newline, end-of-string,
# or one/two words followed by a pipe (catches "Warby Parker Intake Form |").
_DIRECT_OFFER_2W_RE = re.compile(
    r'(?:Earn|Get|Activate|Save)\s+(?:up\s+to\s+)?'
    r'(\d+(?:\.\d+)?)\s*%\s*'
    r'(?:in\s+Rewards?,\s+Up\s+to\s+\$[\d,]+\s+at|(?:in\s+Rewards?|back|Rewards?)\s+at)\s+'
    r'([\w][\w\'\-&]*\s+[\w][\w\'\-&]*)'              # exactly 2 words (no . so period is a stop)
    r'(?='
        r'\s+\w+:'                                      # word+colon  e.g. "Plus:"
        r'|\s*[|,]'                                     # pipe or comma  (NOT ellipsis)
        r'|\s*\.(?:\s|$)'                              # period then space or end
        r'|\s+Was\b'                                   # "Was" (previous rate)
        r'|\s*\n|\s*$'                                 # newline or end-of-string
        r'|\s+\w+\s+\w+\s*\|'                          # two words then pipe
        r'|\s+\w+\s*\|'                                # one word then pipe
    r')',
    re.IGNORECASE,
)

# Strategy A2: "Earn X% back at WORD" — single word, stops at first capital after store.
_DIRECT_OFFER_RE = re.compile(
    r'(?:Earn|Get|Activate|Save)\s+(?:up\s+to\s+)?'
    r'(\d+(?:\.\d+)?)\s*%\s*'
    r'(?:in\s+Rewards?,\s+Up\s+to\s+\$[\d,]+\s+at|(?:in\s+Rewards?|back|Rewards?)\s+at)\s+'
    r'([\w][\w\'\-\.&]*)'                              # single word (greedy)
    r'(?=\s+[A-Z][a-zA-Z]|\s*[,.](?:\s|$)|\s*…|\s+Was\b|\s*\n|\s*$)',
    re.IGNORECASE,
)

# Strategy A3: bare "X% back at STORE" — no Earn/Get prefix (e.g. "Today's Top Offer" blocks).
# Intentionally 1-word to avoid "Serta Top"-style false positives where "Top" is
# part of "Today's Top Offer" heading, not the store name.
_TOP_OFFER_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*%\s*back\s+at\s+'
    r'([\w][\w\'\-\.&]*)'                              # single word only
    r'(?=\s+[A-Z][a-zA-Z]|\s*[,.](?:\s|$)|\s*…|\s+Was\b|\s*\n|\s*$)',
    re.IGNORECASE,
)

# Retained for Strategy B (logo-preceded single-store emails)
_PCT_VALUE_RE = re.compile(
    r'(?:Earn|Get|Activate|Save)\s+(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:in\s+Rewards?|back|Rewards?)',
    re.IGNORECASE,
)

# "Earn $X [in Rewards] [back] at STORE" — 2-word version (strong separator)
_DOLLAR_AT_2W_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?\$(\d+(?:\.\d+)?)'
    r'\s+(?:(?:in\s+)?Rewards?\s+)?(?:back\s+)?at\s+'
    r'([\w][\w\'\-&]*\s+[\w][\w\'\-&]*)'              # exactly 2 words
    r'(?='
        r'\s+\w+:'                                          # word+colon e.g. "Plus:"
        r'|\s*[|,]'                                         # pipe or comma
        r'|\s*\.(?:\s|$)'                                  # period then space or end
        r'|\s+(?:Spend|Check|Was|You)\b'                   # explicit stop words
        r'|\s+(?:\w+\s+){1,4}Was\b'                        # product words then "Was" (catches "Consumer Reports Graco Was")
        r'|\s*\n|\s*$'                                     # newline or end-of-string
        r'|\s+\w+\s+\w+\s*\|'                              # two words then pipe
        r'|\s+\w+\s*\|'                                    # one word then pipe
    r')',
    re.IGNORECASE,
)

# "Earn $X [in Rewards] [back] at STORE" — 1-word version
_DOLLAR_AT_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?\$(\d+(?:\.\d+)?)'
    r'\s+(?:(?:in\s+)?Rewards?\s+)?(?:back\s+)?at\s+'
    r'([\w][\w\'\-\.&]*)'                              # single word
    r'(?=\s+[A-Z][a-zA-Z]|\s*[,.](?:\s|$)|\s+(?:Spend|Check|Was|You)\b|\s*\n|\s*$)',
    re.IGNORECASE,
)

# "Earn $X back on hotel bookings / event tickets" (category bonuses, not at-store)
_DOLLAR_ON_CATEGORY_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?\$(\d+(?:\.\d+)?)'
    r'\s+(?:(?:in\s+)?Rewards?\s+)?back\s+on\s+'
    r'(hotel\s+bookings?|event\s+tickets?|car\s+rentals?|flights?)',
    re.IGNORECASE,
)

# "Earn X% back" for logo-preceded single-store emails (plain text format)
_PCT_BACK_RE = re.compile(
    r'(?:Earn|Get)\s+(?:up\s+to\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:back|in\s+Rewards?|Rewards?)',
    re.IGNORECASE,
)

# "{Store Name} logo"
_LOGO_RE = re.compile(r'([A-Z][\w &\'\-]{1,38}?)\s+logo\b', re.IGNORECASE)

# Generic non-store phrases to reject as store names from Strategy A/B.
# Note: "hotel bookings" / "event tickets" are intentionally kept here to avoid
# false "at hotel bookings" matches — Strategy E handles them via _DOLLAR_ON_CATEGORY_RE.
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


def _extract_offers_from_body(body: str, received: datetime, thread_id: str,
                              subject: str = '') -> list[dict]:
    """Return one dict per cashback offer found in the email body."""
    body = _normalize(body)
    body = _collapse_urls(body)

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

    # claimed: tracks body positions of cashback numbers already matched by a
    # percent regex, so A2/A3 don't produce a duplicate 1-word entry for the
    # same offer that A1 already captured as a 2-word store name.
    claimed: set[int] = set()

    # claimed_dollar tracks body positions of dollar amounts already matched,
    # so the 1-word fallback doesn't produce a duplicate "Total" entry when
    # the 2-word version already captured "Total Wireless".
    claimed_dollar: set[int] = set()

    # -----------------------------------------------------------------------
    # Strategy C — Dollar amounts at store.
    # Runs BEFORE percent strategies so that a "$80 back" label always wins
    # over any spurious "80%" match for the same store+value.
    # 2-word version first so "Total Wireless" beats "Total".
    # -----------------------------------------------------------------------
    for m in _DOLLAR_AT_2W_RE.finditer(body):
        add(float(m.group(1)), f"${float(m.group(1)):g} back", m.group(2).strip())
        claimed_dollar.add(m.start(1))

    for m in _DOLLAR_AT_RE.finditer(body):
        if m.start(1) in claimed_dollar:
            continue
        add(float(m.group(1)), f"${float(m.group(1)):g} back", m.group(2).strip())

    # -----------------------------------------------------------------------
    # Strategy A1 — 2-word percent stores (strong separator after 2nd word).
    # Covers "Best Buy Plus:", "Warby Parker Intake Form |", "Allen Edmonds."
    # -----------------------------------------------------------------------
    for m in _DIRECT_OFFER_2W_RE.finditer(body):
        num = float(m.group(1))
        store = m.group(2).strip()
        pre = body[max(0, m.start() - 15): m.start()]
        add(num, _pct_label(num, pre), store)
        claimed.add(m.start(1))

    # -----------------------------------------------------------------------
    # Strategy A2 — 1-word percent stores (skips positions claimed by A1).
    # -----------------------------------------------------------------------
    for m in _DIRECT_OFFER_RE.finditer(body):
        if m.start(1) in claimed:
            continue
        num = float(m.group(1))
        store = m.group(2).strip()
        pre = body[max(0, m.start() - 15): m.start()]
        add(num, _pct_label(num, pre), store)
        claimed.add(m.start(1))

    # -----------------------------------------------------------------------
    # Strategy A3 — bare "X% back at STORE" without Earn/Get prefix.
    # Covers "Today's Top Offer" blocks like "25% back at Famous Footwear".
    # Greedy 1-or-2 words; skips positions already claimed by A1/A2.
    # -----------------------------------------------------------------------
    for m in _TOP_OFFER_RE.finditer(body):
        if m.start(1) in claimed:
            continue
        num = float(m.group(1))
        store = m.group(2).strip()
        pre = body[max(0, m.start() - 10): m.start()]
        add(num, _pct_label(num, pre), store)
        claimed.add(m.start(1))

    # -----------------------------------------------------------------------
    # Strategy B — logo-preceded single-store emails (plain text format)
    # "{Store} logo [URL] Earn X% back"
    # Guard: if "Earn X% back" is followed by "at {DIFFERENT_STORE}", the
    # cashback belongs to that other store — skip the logo attribution.
    # -----------------------------------------------------------------------
    for logo_m in _LOGO_RE.finditer(body):
        store_candidate = logo_m.group(1).strip()
        if 'capital one' in store_candidate.lower():
            continue
        window = body[logo_m.end(): logo_m.end() + 300]
        pct_m = _PCT_BACK_RE.search(window)
        if pct_m:
            num = float(pct_m.group(1))
            post = window[pct_m.end(): pct_m.end() + 80]
            at_m = re.search(r'\bat\s+([\w][\w &\'\-]{1,30})', post, re.IGNORECASE)
            if at_m:
                other = _clean_store(at_m.group(1))
                if other.lower() != store_candidate.lower():
                    continue  # cashback is for a different store
            pre = window[:pct_m.start()]
            add(num, _pct_label(num, pre), store_candidate)

    # -----------------------------------------------------------------------
    # Strategy E — "Earn $X back on hotel bookings / event tickets / …"
    # These use "on {category}" not "at {store}" so they bypass Strategy C.
    # Added directly to results (skipping _GENERIC_PHRASES gate in add()).
    # -----------------------------------------------------------------------
    for m in _DOLLAR_ON_CATEGORY_RE.finditer(body):
        dollar = float(m.group(1))
        category = m.group(2).strip().title()  # e.g. "Hotel Bookings"
        key = (category.lower(), round(dollar, 2))
        if key not in seen and dollar > 0:
            seen.add(key)
            results.append({
                'Store':        category,
                'Cashback':     f'${dollar:g} back',
                'Cashback_num': dollar,
                'Received':     received_str,
                'Received_dt':  received,
                'Email':        gmail_url,
            })

    # -----------------------------------------------------------------------
    # Fallback — no offers found; try extracting from subject line
    # -----------------------------------------------------------------------
    if not results and subject:
        store = extract_store(subject, '')
        if store and store != 'Unknown':
            # Look for any percentage in the subject
            pct_m = re.search(r'(\d+(?:\.\d+)?)\s*%', subject)
            if pct_m:
                num = float(pct_m.group(1))
                pre = subject[:pct_m.start()]
                add(num, _pct_label(num, pre), store)

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
            offers = _extract_offers_from_body(body, received, thread_id,
                                               subject=headers.get('Subject', ''))
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
