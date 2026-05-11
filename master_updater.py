"""
Flipkart Price Scraper — AGENT VERSION
WebScraping.AI API + Supabase + GitHub Actions

5-Attempt Strategy:
  Attempt 1 : Static HTML          (1 credit)
  Attempt 2 : JS Rendering         (2 credits)
  Attempt 3 : Residential + JS     (10 credits)
  Attempt 4 : Residential + JS     (10 credits — retry)
  Attempt 5 : AI Fields extraction (5 credits — direct structured data)

Docs: https://webscraping.ai/docs
Auth: api_key as query param
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
# WEBSCRAPING.AI — HTML FETCH
# ═══════════════════════════════════════════════════════════════════════════

def _ws_html(url: str, js: bool, residential: bool = False) -> str | None:
    """
    Single HTML fetch call to WebScraping.AI.
    js=False + datacenter  → 1 credit
    js=True  + datacenter  → 2 credits
    js=True  + residential → 10 credits
    """
    label = f"{'RES' if residential else 'DC'}/{'JS' if js else 'STATIC'}"

    params = {
        "api_key" : WS_KEY,
        "url"     : url,
        "js"      : "true" if js else "false",
        "country" : "in",
        "timeout" : 15000,
    }
    if residential:
        params["proxy"] = "residential"

    try:
        resp = requests.get(WS_HTML_URL, params=params, timeout=60)
        logger.info(f"    [{label}] HTTP {resp.status_code} — {len(resp.text)} chars")

        if resp.status_code == 200:
            if len(resp.text) > 1000:
                logger.info(f"    [{label}] ✓ OK")
                return resp.text
            else:
                logger.warning(f"    [{label}] Response too small: {resp.text[:150]}")
                return None

        elif resp.status_code == 402:
            logger.error("    Credits khatam — WebScraping.AI account top up karo!")
            return None

        elif resp.status_code == 403:
            logger.warning(f"    [{label}] 403 Forbidden")
            return None

        else:
            logger.warning(f"    [{label}] Unexpected: {resp.text[:150]}")
            return None

    except requests.exceptions.Timeout:
        logger.warning(f"    [{label}] Request timeout")
        return None
    except Exception as e:
        logger.error(f"    [{label}] Error: {e}")
        return None


def fetch_html(url: str) -> str | None:
    """
    Attempts 1-4: HTML fetch with escalating power.
    Returns HTML string or None.
    """
    # Attempt 1: Static (cheapest)
    logger.info("  Attempt 1/5 — STATIC (1 credit)")
    html = _ws_html(url, js=False, residential=False)
    if html:
        return html
    time.sleep(2)

    # Attempt 2: JS Rendering
    logger.info("  Attempt 2/5 — JS RENDER (2 credits)")
    html = _ws_html(url, js=True, residential=False)
    if html:
        return html
    time.sleep(3)

    # Attempt 3: Residential + JS
    logger.info("  Attempt 3/5 — RESIDENTIAL+JS (10 credits)")
    html = _ws_html(url, js=True, residential=True)
    if html:
        return html
    time.sleep(3)

    # Attempt 4: Residential + JS retry
    logger.info("  Attempt 4/5 — RESIDENTIAL+JS retry (10 credits)")
    html = _ws_html(url, js=True, residential=True)
    if html:
        return html

    # Attempt 5 handled in scrape_row()
    return None


# ═══════════════════════════════════════════════════════════════════════════
# WEBSCRAPING.AI — AI FIELDS (Attempt 5)
# ═══════════════════════════════════════════════════════════════════════════

def ws_ai_fields(url: str) -> dict:
    """
    Attempt 5 — WebScraping.AI AI extraction.
    Directly returns structured data — no HTML parsing needed.
    Cost: 5 credits + proxy cost.
    """
    logger.info("  Attempt 5/5 — AI/fields (5 credits)")

    fields = {
        "current_price" : "Current selling price in bold (Indian Rupees format e.g. Rs.13,902)",
        "original_price": "Original MRP with strikethrough (e.g. Rs.19,999). Empty string if no discount.",
        "discount"      : "Discount percentage in green (e.g. 70%). Empty string if no discount.",
        "rating"        : "Star rating number only (e.g. 4.1). Empty string if not shown.",
        "reviews"       : "Number of reviews Indian format (e.g. 34,452). Empty string if not shown.",
        "ratings_count" : "Number of ratings if shown separately (e.g. 1,01,973). Empty string if not shown.",
    }

    params = {
        "api_key" : WS_KEY,
        "url"     : url,
        "fields"  : json.dumps(fields),
        "country" : "in",
        "js"      : "true",
    }

    try:
        resp = requests.get(WS_AI_URL, params=params, timeout=60)
        logger.info(f"    [AI/fields] HTTP {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                logger.info(f"    [AI/fields] Data: {data}")
                return data
            logger.warning(f"    [AI/fields] Unexpected format: {data}")
            return {}

        logger.error(f"    [AI/fields] Failed: {resp.text[:200]}")
        return {}

    except Exception as e:
        logger.error(f"    [AI/fields] Error: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# FORMAT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def parse_int(text) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def indian_price(val: int) -> str:
    """Format integer as Rs.X,XX,XXX"""
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
    """Format as X,XX,XXX (no Rs. prefix)"""
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
# HTML EXTRACTION — L1 to L4 Approach
# ═══════════════════════════════════════════════════════════════════════════

_PRICE_CSS = [
    ("div", "_30jeq3 _16Jk6d"),
    ("div", "_30jeq3"),
    ("span", "_30jeq3"),
    ("div", "CEmiEU"),
    ("span", "CEmiEU"),
]
_DISC_CSS = ["UkUFwK", "VGWI6a", "pPAw9j", "_3Ay6Sb", "Bs5uzZ", "_2Tpdn3", "_1psv1zeb9"]
_MRP_CSS  = ["yRaY8j", "_3I9_wc", "_3auQ3N", "CAWmgp", "_2p6lqe"]
_DISC_RE  = re.compile(r"(\d{1,2})%")
_DISC_OFF = re.compile(r"(\d{1,2})%\s*off", re.IGNORECASE)


def extract_current_price(soup: BeautifulSoup) -> int | None:
    # Known CSS classes
    for tag, cls in _PRICE_CSS:
        el = soup.find(tag, class_=cls.split())
        if el:
            v = parse_int(el.get_text())
            if v and 50 <= v <= 50_00_000:
                return v
    # Fallback: first Rs. in page
    for string in soup.strings:
        m = re.search(r"Rs\.\s*([\d,]+)", string)
        if m:
            v = parse_int(m.group(1))
            if v and 50 <= v <= 50_00_000:
                return v
    return None


def _valid_disc(val: int, context: str) -> bool:
    return 1 <= val <= 95 and not has_bank_kw(context)


def extract_discount(soup: BeautifulSoup) -> str:
    # L1: Structural — walk up 6 levels from price tag
    for string in soup.strings:
        if re.search(r"Rs\.\s*[\d,]+", string):
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

    # L2: Known CSS badge classes
    for cls in _DISC_CSS:
        for tag in soup.find_all(["div", "span"], class_=cls):
            text = tag.get_text(strip=True)
            m = _DISC_RE.search(text)
            if m:
                v = int(m.group(1))
                if _valid_disc(v, text):
                    return f"{v}%"

    # L3: Short tag scan (<=8 chars)
    for tag in soup.find_all(True):
        text = tag.get_text(strip=True)
        if 2 <= len(text) <= 8:
            m = re.match(r"^(\d{1,2})%", text)
            if m:
                v = int(m.group(1))
                parent_text = tag.parent.get_text() if tag.parent else ""
                if _valid_disc(v, parent_text):
                    return f"{v}%"

    # L4: Full text "X% off"
    full = soup.get_text()
    for m in _DISC_OFF.finditer(full):
        v = int(m.group(1))
        if 1 <= v <= 95:
            ctx = full[max(0, m.start() - 150): m.end() + 50]
            if not has_bank_kw(ctx):
                return f"{v}%"

    return ""


def extract_original_price(
    soup: BeautifulSoup,
    cur: int,
    disc_str: str,
    iphone_mode: bool = False,
) -> str:
    if not disc_str or not cur:
        return ""
    disc = int(disc_str.replace("%", ""))
    if disc <= 0:
        return ""

    # Step 1: Calculated fallback
    calc = round(cur / (1 - disc / 100))
    candidates = []

    # Step 2: Collect strikethrough numbers
    for tag in soup.find_all(["s", "del", "strike"]):
        v = parse_int(tag.get_text())
        if v and v > cur and 100 <= v <= 50_00_000:
            candidates.append(v)

    if not iphone_mode:
        # CSS line-through (skip for iPhone — variant prices bleed in)
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

    # Step 3+4: Best match with tolerance
    if candidates:
        best = min(candidates, key=lambda x: abs(x - calc))
        diff = abs(best - calc)
        if diff <= 15 or diff <= calc * 0.10:
            return indian_price(best)

    return indian_price(calc)


def get_iphone_discount(soup: BeautifulSoup) -> tuple[str, str]:
    """
    iPhone strict logic:
    - Only look before 'Protect Promise Fee' boundary
    - Only <s>/<del> tags = real MRP (CSS line-through ignored)
    - No <s>/<del> found → ("", "") — empty disc + orig
    """
    html = str(soup)
    boundary = html.find("Protect Promise Fee")
    limited  = BeautifulSoup(html[:boundary], "html.parser") if boundary != -1 else soup

    mrp = None
    for tag in limited.find_all(["s", "del"]):
        v = parse_int(tag.get_text())
        if v and 5_000 <= v <= 5_00_000:
            mrp = v
            break

    if mrp is None:
        return "", ""

    disc_str = ""
    for tag in limited.find_all(True):
        text = tag.get_text(strip=True)
        if 2 <= len(text) <= 8:
            m = re.match(r"^(\d{1,2})%", text)
            if m:
                v = int(m.group(1))
                parent_text = tag.parent.get_text() if tag.parent else ""
                if 1 <= v <= 50 and not has_bank_kw(parent_text):
                    disc_str = f"{v}%"
                    break

    return disc_str, indian_price(mrp)


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
# TABLE CONFIG — All 12 tables with exact Supabase column names
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
        "link_col"   : "ProductLink",         # NOTE: bina space
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
# BUILD UPDATE DICT FROM HTML
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
        ratings_cnt, reviews_cnt = extract_reviews_pair(soup)
        if reviews_cnt:
            update[cfg["reviews_col"]] = reviews_cnt
        if ratings_cnt:
            update[cfg.get("reviews2_col", "Number of Ratings")] = ratings_cnt
        return update

    if cfg["combined"]:
        cur = extract_current_price(soup)
        if cur:
            update[cfg["cur_col"]] = indian_price(cur)
        disc_str = extract_discount(soup)
        update[cfg["disc_col"]] = disc_str
        update[cfg["orig_col"]] = (
            extract_original_price(soup, cur, disc_str)
            if disc_str and cur else ""
        )
        cr = combined_rating_reviews(soup)
        if cr:
            update[cfg["combined_col"]] = cr
        return update

    # Standard table
    cur = extract_current_price(soup)
    if cur:
        update[cfg["cur_col"]] = indian_price(cur)
    disc_str = extract_discount(soup)
    update[cfg["disc_col"]] = disc_str
    update[cfg["orig_col"]] = (
        extract_original_price(soup, cur, disc_str)
        if disc_str and cur else ""
    )
    rating = extract_rating(soup)
    if rating:
        update[cfg["rating_col"]] = rating
    _, reviews = extract_reviews_pair(soup)
    if reviews:
        update[cfg["reviews_col"]] = reviews
    return update


# ═══════════════════════════════════════════════════════════════════════════
# BUILD UPDATE DICT FROM AI RESPONSE
# ═══════════════════════════════════════════════════════════════════════════

def build_from_ai(ai_data: dict, cfg: dict) -> dict:
    """
    AI response se update dict banao.
    NOTE: (X or "") pattern use karo — None.strip() crash rokne ke liye.
    """
    update = {}
    if not ai_data:
        return update

    # Safely extract — None values ko "" se replace karo
    cur_price  = str(ai_data.get("current_price")  or "").strip()
    orig_price = str(ai_data.get("original_price") or "").strip()
    discount   = str(ai_data.get("discount")       or "").strip()
    rating     = str(ai_data.get("rating")         or "").strip()
    reviews    = str(ai_data.get("reviews")        or "").strip()
    ratings_c  = str(ai_data.get("ratings_count")  or "").strip()

    if cur_price:
        update[cfg["cur_col"]] = cur_price
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
# SCRAPE ROW — Main logic
# ═══════════════════════════════════════════════════════════════════════════

def scrape_row(url: str, cfg: dict) -> dict:
    # Attempts 1-4: HTML fetch
    html = fetch_html(url)

    if html:
        soup   = BeautifulSoup(html, "html.parser")
        update = build_from_html(soup, cfg)

        if update.get(cfg["cur_col"]):
            logger.info("  ✓ HTML parser se data mila")
            return update
        else:
            logger.warning("  HTML mila lekin price extract nahi hua — AI try kar raha hoon")

    # Attempt 5: AI fields
    ai_data = ws_ai_fields(url)
    update  = build_from_ai(ai_data, cfg)

    if update:
        logger.info("  ✓ AI fields se data mila")
    else:
        logger.error("  ✗ Sab 5 attempts fail — koi data nahi")

    return update


# ═══════════════════════════════════════════════════════════════════════════
# TABLE PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════

def process_table(table_name: str, cfg: dict):
    logger.info(f"\n{'━' * 60}")
    logger.info(f"  TABLE: {table_name}")
    logger.info(f"{'━' * 60}")

    try:
        result = supabase.table(table_name).select("*").execute()
        rows   = result.data or []
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
            logger.error(f"  scrape_row exception: {e}")
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
    logger.info("  Flipkart Scraper — WebScraping.AI + 5-Attempt Agent")
    logger.info("=" * 60)

    for table_name, cfg in TABLE_CONFIG.items():
        try:
            process_table(table_name, cfg)
        except Exception as e:
            # Non-cancel policy: ek table fail ho toh baaki chalta rahe
            logger.error(f"FATAL error in table '{table_name}': {e}")
            continue

    logger.info("\n" + "=" * 60)
    logger.info("  ALL TABLES DONE")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

