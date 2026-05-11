"""
Flipkart Price Scraper — CREDIT SAVER VERSION
WebScraping.AI + Supabase + GitHub Actions

CREDIT STRATEGY (2000 credits for 800-1000 products):
  Attempt 1 : STATIC HTML    (1 credit)  ← 95% cases yahi kaam kare
  Attempt 2 : JS Render      (2 credits) ← JS-heavy pages ke liye
  Attempt 3 : AI/fields      (5 credits) ← LAST RESORT sirf

PRICE CONCEPT (briefing ke hisaab se):
  Original Price = MRP = strikethrough price (e.g. Rs.2,999)
  Discount       = green badge % (e.g. 70%)
  Current Price  = selling price NOW = original - discount (e.g. Rs.899)
"""

import os
import re
import time
import json
import logging
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client


# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Supabase ───────────────────────────────────────────────────────────────
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"],
)

# ── WebScraping.AI ─────────────────────────────────────────────────────────
WS_KEY      = os.environ["WEBSCRAPING_AI_KEY"]
WS_HTML_URL = "https://api.webscraping.ai/html"
WS_AI_URL   = "https://api.webscraping.ai/ai/fields"


# ═══════════════════════════════════════════════════════════════════════════
# FETCH — STATIC first (1 credit), JS fallback (2 credits)
# ═══════════════════════════════════════════════════════════════════════════

def _fetch(url: str, js: bool) -> str | None:
    label  = "JS" if js else "STATIC"
    params = {
        "api_key": WS_KEY,
        "url"    : url,
        "js"     : "true" if js else "false",
        "country": "in",
        "timeout": 15000,
    }
    try:
        resp = requests.get(WS_HTML_URL, params=params, timeout=60)
        logger.info(f"    [{label}] HTTP {resp.status_code} — {len(resp.text)} chars")
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
        if resp.status_code == 402:
            logger.error("    Credits khatam!")
    except Exception as e:
        logger.warning(f"    [{label}] Error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# AI FIELDS — sirf last resort (5 credits)
# ═══════════════════════════════════════════════════════════════════════════

def _ai_fields(url: str) -> dict:
    """
    WebScraping.AI /ai/fields
    IMPORTANT: Response nested hai: {'result': {'current_price': ...}}
    """
    fields = {
        "current_price" : "The bold selling price (e.g. Rs.899 or ₹899). This is the price customer pays NOW.",
        "original_price": "The MRP/original price shown with strikethrough (e.g. Rs.2,999). Empty if no discount.",
        "discount"      : "Discount percentage in green (e.g. 70%). Empty if no discount.",
        "rating"        : "Star rating (e.g. 4.1). Empty if not shown.",
        "reviews"       : "Number of reviews Indian format (e.g. 34,452). Empty if not shown.",
        "ratings_count" : "Number of ratings if separate (e.g. 1,01,973). Empty if not shown.",
    }
    params = {
        "api_key": WS_KEY,
        "url"    : url,
        "fields" : json.dumps(fields),
        "country": "in",
        "js"     : "true",
    }
    try:
        resp = requests.get(WS_AI_URL, params=params, timeout=60)
        logger.info(f"    [AI] HTTP {resp.status_code}")
        if resp.status_code == 200:
            raw = resp.json()
            logger.info(f"    [AI] Raw: {str(raw)[:300]}")
            # FIX: Response 'result' key ke andar nested ho sakta hai
            if isinstance(raw, dict):
                data = raw.get("result", raw)
                if isinstance(data, dict):
                    return data
        logger.error(f"    [AI] Failed: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"    [AI] Error: {e}")
    return {}


# ═══════════════════════════════════════════════════════════════════════════
# FORMAT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def parse_int(text) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def to_rs_format(price_str: str) -> str:
    """
    AI se ₹ format aata hai — Rs. format mein convert karo.
    e.g. ₹1,999 → Rs.1,999
         Rs.1,999 → Rs.1,999 (unchanged)
    """
    if not price_str:
        return ""
    price_str = price_str.strip()
    # ₹ ya Rs. dono handle karo
    price_str = re.sub(r"^₹\s*", "Rs.", price_str)
    if not price_str.startswith("Rs."):
        # Sirf number hai — Rs. lagao
        num = re.sub(r"[^\d,]", "", price_str)
        if num:
            price_str = f"Rs.{num}"
    return price_str


def indian_price(val: int) -> str:
    """Integer ko Rs.X,XX,XXX format mein convert karo"""
    if not val:
        return ""
    s = str(val)
    if len(s) <= 3:
        return f"Rs.{s}"
    result = s[-3:]
    s = s[:-3]
    while len(s) > 2:
        result = s[-2:] + "," + result
        s = s[:-2]
    if s:
        result = s + "," + result
    return f"Rs.{result}"


def indian_number(val) -> str:
    raw = re.sub(r"[^\d]", "", str(val)) if val else ""
    if not raw:
        return ""
    s = raw
    if len(s) <= 3:
        return s
    result = s[-3:]
    s = s[:-3]
    while len(s) > 2:
        result = s[-2:] + "," + result
        s = s[:-2]
    if s:
        result = s + "," + result
    return result


# ═══════════════════════════════════════════════════════════════════════════
# BANK KEYWORD FILTER
# ═══════════════════════════════════════════════════════════════════════════

_BANK_KW = [
    "bank", "credit", "debit", "hdfc", "sbi", "axis", "icici",
    "cashback", "upi", "emi", "kotak", "rbl", "paytm", "rupay",
    "no cost", "instant discount", "additional", "card offer",
    "flat rs", "flat rupee",
]

def has_bank_kw(text: str) -> bool:
    return any(k in text.lower() for k in _BANK_KW)


# ═══════════════════════════════════════════════════════════════════════════
# CURRENT PRICE EXTRACTION — Robust Multi-Method
# ═══════════════════════════════════════════════════════════════════════════

# Flipkart current price CSS — multiple versions
_PRICE_CSS = [
    # Old Flipkart
    ("div",  "_30jeq3 _16Jk6d"),
    ("div",  "_30jeq3"),
    ("span", "_30jeq3"),
    # New Flipkart 2024-25
    ("div",  "CEmiEU"),
    ("span", "CEmiEU"),
    ("div",  "hl05au"),
    ("span", "hl05au"),
    ("div",  "Nx9bqj"),
    ("span", "Nx9bqj"),
]

def extract_current_price(soup: BeautifulSoup) -> int | None:
    """
    Current price = selling price NOW (bold price after discount).
    Multi-method: CSS → ₹ pattern → Rs. pattern
    """
    # Method 1: Known CSS classes
    for tag, cls in _PRICE_CSS:
        el = soup.find(tag, class_=cls.split())
        if el:
            v = parse_int(el.get_text())
            if v and 50 <= v <= 50_00_000:
                return v

    # Method 2: ₹ symbol pattern (new Flipkart uses ₹)
    for string in soup.strings:
        m = re.search(r"₹\s*([\d,]+)", string)
        if m:
            v = parse_int(m.group(1))
            if v and 50 <= v <= 50_00_000:
                return v

    # Method 3: Rs. pattern
    for string in soup.strings:
        m = re.search(r"Rs\.\s*([\d,]+)", string)
        if m:
            v = parse_int(m.group(1))
            if v and 50 <= v <= 50_00_000:
                return v

    return None


# ═══════════════════════════════════════════════════════════════════════════
# DISCOUNT EXTRACTION — L1 to L4
# ═══════════════════════════════════════════════════════════════════════════

_DISC_CSS = ["UkUFwK", "VGWI6a", "pPAw9j", "_3Ay6Sb", "Bs5uzZ", "_2Tpdn3",
             "_1psv1zeb9", "yRaY8j"]
_DISC_RE  = re.compile(r"(\d{1,2})%")
_DISC_OFF = re.compile(r"(\d{1,2})%\s*off", re.IGNORECASE)


def _valid_disc(val: int, ctx: str) -> bool:
    return 1 <= val <= 95 and not has_bank_kw(ctx)


def extract_discount(soup: BeautifulSoup) -> str:
    # L1: Structural — price tag se 6 levels upar
    for string in soup.strings:
        if re.search(r"[₹Rs\.]\s*[\d,]+", string):
            container = string.parent
            for _ in range(6):
                if not container or container.name in ("body", "html", "[document]"):
                    break
                container = container.parent
                for child in container.find_all(True):
                    ct = child.get_text(strip=True)
                    if len(ct) <= 10:
                        m = _DISC_RE.match(ct)
                        if m:
                            v = int(m.group(1))
                            if _valid_disc(v, container.get_text()):
                                return f"{v}%"
            break

    # L2: Known CSS badge
    for cls in _DISC_CSS:
        for tag in soup.find_all(["div", "span"], class_=cls):
            text = tag.get_text(strip=True)
            m = _DISC_RE.search(text)
            if m:
                v = int(m.group(1))
                if _valid_disc(v, text):
                    return f"{v}%"

    # L3: Short tag scan
    for tag in soup.find_all(True):
        text = tag.get_text(strip=True)
        if 2 <= len(text) <= 8:
            m = re.match(r"^(\d{1,2})%", text)
            if m:
                v = int(m.group(1))
                pt = tag.parent.get_text() if tag.parent else ""
                if _valid_disc(v, pt):
                    return f"{v}%"

    # L4: Full text
    full = soup.get_text()
    for m in _DISC_OFF.finditer(full):
        v = int(m.group(1))
        if 1 <= v <= 95:
            ctx = full[max(0, m.start() - 150): m.end() + 50]
            if not has_bank_kw(ctx):
                return f"{v}%"

    return ""


# ═══════════════════════════════════════════════════════════════════════════
# ORIGINAL PRICE — MRP (strikethrough price)
# ═══════════════════════════════════════════════════════════════════════════

_MRP_CSS = ["yRaY8j", "_3I9_wc", "_3auQ3N", "CAWmgp", "_2p6lqe",
            "se6cQ6", "line-through"]


def extract_original_price(
    soup: BeautifulSoup,
    cur: int,
    disc_str: str,
    iphone_mode: bool = False,
) -> str:
    """
    Original price = MRP = strikethrough price.
    Algorithm:
      Step 1: calc = cur / (1 - disc%)
      Step 2: Strikethrough numbers collect karo
      Step 3: Best match ±10% ya ±Rs.15
      Step 4: No match → calc use karo
    """
    if not disc_str or not cur:
        return ""
    disc = int(disc_str.replace("%", ""))
    if disc <= 0:
        return ""

    calc = round(cur / (1 - disc / 100))
    candidates = []

    # HTML strikethrough tags
    for tag in soup.find_all(["s", "del", "strike"]):
        v = parse_int(tag.get_text())
        if v and v > cur and 100 <= v <= 50_00_000:
            candidates.append(v)

    if not iphone_mode:
        # CSS line-through
        for tag in soup.find_all(style=re.compile(r"line-through", re.I)):
            v = parse_int(tag.get_text())
            if v and v > cur and 100 <= v <= 50_00_000:
                candidates.append(v)
        # MRP CSS classes
        for cls in _MRP_CSS:
            for tag in soup.find_all(["div", "span"], class_=cls):
                v = parse_int(tag.get_text())
                if v and v > cur and 100 <= v <= 50_00_000:
                    candidates.append(v)

    if candidates:
        best = min(candidates, key=lambda x: abs(x - calc))
        diff = abs(best - calc)
        if diff <= 15 or diff <= calc * 0.10:
            return indian_price(best)

    return indian_price(calc)


# ═══════════════════════════════════════════════════════════════════════════
# iPHONE SPECIAL
# ═══════════════════════════════════════════════════════════════════════════

def get_iphone_discount(soup: BeautifulSoup) -> tuple[str, str]:
    html = str(soup)
    bi   = html.find("Protect Promise Fee")
    lim  = BeautifulSoup(html[:bi], "html.parser") if bi != -1 else soup

    mrp = None
    for tag in lim.find_all(["s", "del"]):
        v = parse_int(tag.get_text())
        if v and 5_000 <= v <= 5_00_000:
            mrp = v
            break

    if mrp is None:
        return "", ""

    disc_str = ""
    for tag in lim.find_all(True):
        text = tag.get_text(strip=True)
        if 2 <= len(text) <= 8:
            m = re.match(r"^(\d{1,2})%", text)
            if m:
                v = int(m.group(1))
                pt = tag.parent.get_text() if tag.parent else ""
                if 1 <= v <= 50 and not has_bank_kw(pt):
                    disc_str = f"{v}%"
                    break

    return disc_str, indian_price(mrp)


# ═══════════════════════════════════════════════════════════════════════════
# RATING & REVIEWS
# ═══════════════════════════════════════════════════════════════════════════

def extract_rating(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(["div", "span"]):
        m = re.match(r"^(\d\.\d)\s*★?$", tag.get_text(strip=True))
        if m:
            return m.group(1)
    m = re.search(r"(\d\.\d)\s*★", soup.get_text())
    return m.group(1) if m else ""


def extract_reviews_pair(soup: BeautifulSoup) -> tuple[str, str]:
    full = soup.get_text()
    m = re.search(
        r"([\d,]+)\s+Ratings?\s*[&|]\s*([\d,]+)\s+Reviews?",
        full, re.IGNORECASE,
    )
    if m:
        return (
            indian_number(parse_int(m.group(1))),
            indian_number(parse_int(m.group(2))),
        )
    m = re.search(r"([\d,]+)\s+(?:Ratings?|Reviews?)", full, re.IGNORECASE)
    if m:
        return "", indian_number(parse_int(m.group(1)))
    return "", ""


def combined_rating_reviews(soup: BeautifulSoup) -> str:
    rating = extract_rating(soup)
    _, reviews = extract_reviews_pair(soup)
    if rating and reviews:
        return f"{rating} | {reviews}"
    return rating or ""


# ═══════════════════════════════════════════════════════════════════════════
# TABLE CONFIG
# ═══════════════════════════════════════════════════════════════════════════

TABLE_CONFIG = {
    "earbuds": {
        "link_col"   : "Product Link",
        "cur_col"    : "Current Price",
        "orig_col"   : "Original Price",
        "disc_col"   : "Discount",
        "rating_col" : "Rating",
        "reviews_col": "Number of Reviews",
        "combined"   : False,
        "iphone"     : False,
    },
    "gaming cpu": {
        "link_col"   : "Product Link",
        "cur_col"    : "Current Price",
        "orig_col"   : "Original Price",
        "disc_col"   : "Discount",
        "rating_col" : "Rating",
        "reviews_col": "Number of Reviews",
        "combined"   : False,
        "iphone"     : False,
    },
    "gaming pc": {
        "link_col"   : "Product Link",
        "cur_col"    : "Price",
        "orig_col"   : "Original Price-2",
        "disc_col"   : "Discount-2",
        "rating_col" : "Product Rating",
        "reviews_col": "product review",
        "combined"   : False,
        "iphone"     : False,
    },
    "induction": {
        "link_col"   : "ProductLink",        # bina space
        "cur_col"    : "Discounted Price",
        "orig_col"   : "Price",
        "disc_col"   : "Discount Percentage",
        "rating_col" : "Rating",
        "reviews_col": "Number of Reviews",
        "combined"   : False,
        "iphone"     : False,
    },
    "iphone": {
        "link_col"    : "Product URL",
        "cur_col"     : "Discounted Price",
        "orig_col"    : "Price",
        "disc_col"    : "Discount Percentage",
        "rating_col"  : "Product Rating",
        "reviews_col" : "Number of Reviews",
        "reviews2_col": "Number of Ratings",
        "combined"    : False,
        "iphone"      : True,
    },
    "keybord": {
        "link_col"   : "Product Link",
        "cur_col"    : "Price",
        "orig_col"   : "Original Price",
        "disc_col"   : "Discount",
        "rating_col" : "Rating",
        "reviews_col": "Number of Reviews",
        "combined"   : False,
        "iphone"     : False,
    },
    "laptop": {
        "link_col"    : "Product Link",
        "cur_col"     : "Price",
        "orig_col"    : "Original Price",
        "disc_col"    : "Discount",
        "combined_col": "Rating and Reviews",
        "combined"    : True,
        "iphone"      : False,
    },
    "monitar": {
        "link_col"   : "Product URL",
        "cur_col"    : "Current Price",
        "orig_col"   : "Original Price",
        "disc_col"   : "Discount",
        "rating_col" : "Rating",
        "reviews_col": "Number of Reviews",
        "combined"   : False,
        "iphone"     : False,
    },
    "mouse": {
        "link_col"   : "Product Link",
        "cur_col"    : "Current Price",
        "orig_col"   : "Original Price",
        "disc_col"   : "Discount",
        "rating_col" : "Rating",
        "reviews_col": "Number of Reviews",
        "combined"   : False,
        "iphone"     : False,
    },
    "smart phone": {
        "link_col"    : "Product Link",
        "cur_col"     : "Price",
        "orig_col"    : "Original Price",
        "disc_col"    : "Discount",
        "combined_col": "Ratings and Reviews",
        "combined"    : True,
        "iphone"      : False,
    },
    "smart+tv": {
        "link_col"    : "Product Link",
        "cur_col"     : "Price",
        "orig_col"    : "Original Price",
        "disc_col"    : "Discount",
        "combined_col": "Ratings and Reviews",
        "combined"    : True,
        "iphone"      : False,
    },
    "smartwatch": {
        "link_col"   : "Product Link",
        "cur_col"    : "Price",
        "orig_col"   : "Original Price",
        "disc_col"   : "Discount",
        "rating_col" : "Rating",
        "reviews_col": "Review",
        "combined"   : False,
        "iphone"     : False,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# BUILD UPDATE FROM HTML
# ═══════════════════════════════════════════════════════════════════════════

def build_from_html(soup: BeautifulSoup, cfg: dict) -> dict:
    update = {}

    if cfg["iphone"]:
        cur = extract_current_price(soup)
        if cur:
            update[cfg["cur_col"]] = indian_price(cur)
        disc_str, orig_str = get_iphone_discount(soup)
        update[cfg["disc_col"]] = disc_str
        update[cfg["orig_col"]] = orig_str
        rating = extract_rating(soup)
        if rating:
            update[cfg["rating_col"]] = rating
        rc, rv = extract_reviews_pair(soup)
        if rv:
            update[cfg["reviews_col"]] = rv
        if rc:
            update[cfg.get("reviews2_col", "Number of Ratings")] = rc
        return update

    if cfg["combined"]:
        cur = extract_current_price(soup)
        if cur:
            update[cfg["cur_col"]] = indian_price(cur)
        disc_str = extract_discount(soup)
        update[cfg["disc_col"]] = disc_str
        update[cfg["orig_col"]] = (
            extract_original_price(soup, cur, disc_str) if disc_str and cur else ""
        )
        cr = combined_rating_reviews(soup)
        if cr:
            update[cfg["combined_col"]] = cr
        return update

    # Standard
    cur = extract_current_price(soup)
    if cur:
        update[cfg["cur_col"]] = indian_price(cur)
    disc_str = extract_discount(soup)
    update[cfg["disc_col"]] = disc_str
    update[cfg["orig_col"]] = (
        extract_original_price(soup, cur, disc_str) if disc_str and cur else ""
    )
    rating = extract_rating(soup)
    if rating:
        update[cfg["rating_col"]] = rating
    _, reviews = extract_reviews_pair(soup)
    if reviews:
        update[cfg["reviews_col"]] = reviews
    return update


# ═══════════════════════════════════════════════════════════════════════════
# BUILD UPDATE FROM AI RESPONSE
# ═══════════════════════════════════════════════════════════════════════════

def build_from_ai(ai_data: dict, cfg: dict) -> dict:
    """
    AI se aaye data ko Supabase format mein convert karo.
    FIX 1: str(x or "") → None.strip() crash nahi hoga
    FIX 2: ₹ → Rs. format convert
    """
    update = {}
    if not ai_data:
        return update

    cur_price  = to_rs_format(str(ai_data.get("current_price")  or ""))
    orig_price = to_rs_format(str(ai_data.get("original_price") or ""))
    discount   = str(ai_data.get("discount")       or "").strip()
    rating     = str(ai_data.get("rating")         or "").strip()
    reviews    = str(ai_data.get("reviews")        or "").strip()
    ratings_c  = str(ai_data.get("ratings_count")  or "").strip()

    # Validate prices — original > current hona chahiye
    cur_int  = parse_int(cur_price)
    orig_int = parse_int(orig_price)
    if cur_int and orig_int and orig_int <= cur_int:
        # AI ne galat data diya — orig price current se chhota nahi ho sakta
        logger.warning(f"    [AI] orig ({orig_int}) <= cur ({cur_int}) — orig ignore kar raha hoon")
        orig_price = ""

    if cur_price:
        update[cfg["cur_col"]]  = cur_price
    if orig_price:
        update[cfg["orig_col"]] = orig_price
    if discount:
        update[cfg["disc_col"]] = discount

    if cfg["combined"]:
        if rating and reviews:
            update[cfg["combined_col"]] = f"{rating} | {reviews}"
    elif cfg["iphone"]:
        if rating:
            update[cfg["rating_col"]] = rating
        if reviews:
            update[cfg["reviews_col"]] = reviews
        if ratings_c and "reviews2_col" in cfg:
            update[cfg["reviews2_col"]] = ratings_c
    else:
        if rating:
            update[cfg["rating_col"]] = rating
        if reviews:
            update[cfg["reviews_col"]] = reviews

    return update


# ═══════════════════════════════════════════════════════════════════════════
# SCRAPE ROW — Credit saver strategy
# ═══════════════════════════════════════════════════════════════════════════

def scrape_row(url: str, cfg: dict) -> dict:
    # Attempt 1: STATIC (1 credit) ← target: 95% yahi kaam kare
    logger.info("  Attempt 1 — STATIC (1 credit)")
    html = _fetch(url, js=False)
    if html:
        soup   = BeautifulSoup(html, "html.parser")
        update = build_from_html(soup, cfg)
        if update.get(cfg["cur_col"]):
            logger.info("  ✓ STATIC se data mila (1 credit)")
            return update
        logger.warning("  STATIC HTML aaya lekin price nahi mila")

    # Attempt 2: JS Render (2 credits)
    logger.info("  Attempt 2 — JS RENDER (2 credits)")
    html = _fetch(url, js=True)
    if html:
        soup   = BeautifulSoup(html, "html.parser")
        update = build_from_html(soup, cfg)
        if update.get(cfg["cur_col"]):
            logger.info("  ✓ JS RENDER se data mila (2 credits)")
            return update
        logger.warning("  JS HTML bhi aaya lekin price nahi mila")

    # Attempt 3: AI fields — LAST RESORT (5 credits)
    logger.info("  Attempt 3 — AI/fields LAST RESORT (5 credits)")
    ai_data = _ai_fields(url)
    update  = build_from_ai(ai_data, cfg)
    if update:
        logger.info("  ✓ AI se data mila (5 credits)")
    else:
        logger.error("  ✗ Sab attempts fail")
    return update


# ═══════════════════════════════════════════════════════════════════════════
# TABLE PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════

def process_table(table_name: str, cfg: dict):
    logger.info(f"\n{'━' * 60}")
    logger.info(f"  TABLE: {table_name}")
    logger.info(f"{'━' * 60}")

    try:
        rows = supabase.table(table_name).select("*").execute().data or []
    except Exception as e:
        logger.error(f"  Supabase fetch failed: {e}")
        return

    logger.info(f"  {len(rows)} products found")
    link_col = cfg["link_col"]
    success = fail = skip = 0

    for i, row in enumerate(rows, 1):
        url = (row.get(link_col) or "").strip()
        if not url:
            skip += 1
            continue

        logger.info(f"\n  [{i}/{len(rows)}] {url[:80]}")

        try:
            update = scrape_row(url, cfg)
        except Exception as e:
            logger.error(f"  Exception: {e}")
            fail += 1
            time.sleep(3)
            continue

        if not update:
            fail += 1
            time.sleep(2)
            continue

        for k, v in update.items():
            logger.info(f"    {k}: {v!r}")

        try:
            supabase.table(table_name).update(update).eq(link_col, url).execute()
            logger.info("    ✓ Supabase updated")
            success += 1
        except Exception as e:
            logger.error(f"    Supabase update failed: {e}")
            fail += 1

        time.sleep(1)

    logger.info(f"\n  TABLE DONE — success={success}  fail={fail}  skip={skip}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 60)
    logger.info("  Flipkart Scraper — Credit Saver Mode")
    logger.info("  Strategy: STATIC(1) → JS(2) → AI(5)")
    logger.info("=" * 60)

    for table_name, cfg in TABLE_CONFIG.items():
        try:
            process_table(table_name, cfg)
        except Exception as e:
            logger.error(f"FATAL in '{table_name}': {e}")
            continue

    logger.info("\n" + "=" * 60)
    logger.info("  ALL TABLES DONE")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

