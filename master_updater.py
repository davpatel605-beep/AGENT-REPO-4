"""
Flipkart Price Scraper — master_updater.py
AlterLab SDK (AlterLabSync) + Supabase + GitHub Actions

VERIFIED against official docs at:
  https://alterlab.io/docs/sdk/python
  https://alterlab.io/docs/api/rest

CORRECT endpoint : https://api.alterlab.io/api/v1/scrape
CORRECT auth     : X-API-Key header
CORRECT SDK class: AlterLabSync (synchronous, no await needed)
CORRECT env var  : ALTERLAB_API_KEY (SDK reads this automatically)
CORRECT response : result["content"] — string or dict with "html" key
"""

import os
import re
import time
import logging
from bs4 import BeautifulSoup
from supabase import create_client, Client

# AlterLab SDK
from alterlab import AlterLabSync, AlterLabAPIError, AlterLabTimeoutError

# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Supabase ──────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── AlterLab client ───────────────────────────────────────────────────────
# SDK reads ALTERLAB_API_KEY env var automatically — no need to pass api_key
# GitHub Secret name MUST be: ALTERLAB_API_KEY
alterlab = AlterLabSync(api_key=os.environ["ALTERLAB_API_KEY"])


# ═══════════════════════════════════════════════════════════════════════════
# ALTERLAB FETCH — Using official SDK (handles 202 polling automatically)
# ═══════════════════════════════════════════════════════════════════════════

def _extract_html_from_result(result: dict) -> str:
    """
    AlterLab response 'content' field can be:
      - A plain string (simple sync HTML request)
      - A dict with keys: html, text, json, markdown (when formats param used)
    """
    content = result.get("content", "")
    if isinstance(content, dict):
        return content.get("html") or content.get("text") or ""
    return content or ""


def fetch_page(url: str, render: bool = False, retries: int = 3) -> str | None:
    """
    Fetch a Flipkart page using AlterLab SDK.
    - render=False → mode="html"  (Tier 1/2, cheapest, fast)
    - render=True  → mode="js"   (Tier 4, headless browser, JS rendered)
    India geo-targeting included for correct pricing/data.
    """
    mode = "js" if render else "html"
    label = "RENDER(js)" if render else "CHEAP(html)"

    for attempt in range(1, retries + 1):
        try:
            result = alterlab.scrape(
                url=url,
                mode=mode,
                location={"country": "IN"},   # India geo-targeting
                formats=["html"],              # Always get HTML in content.html
                timeout=120,
            )

            html = _extract_html_from_result(result)

            if len(html) > 500:
                tier = result.get("billing", {}).get("tier_used", "?")
                credits = result.get("billing", {}).get("total_credits", "?")
                logger.info(f"    [{label}] OK — tier={tier}, credits={credits}, len={len(html)}")
                return html

            logger.warning(f"    [{label}] Attempt {attempt}: HTML too small ({len(html)} chars)")

        except AlterLabAPIError as e:
            code = e.status_code if hasattr(e, "status_code") else "?"
            if code == 401:
                logger.error("    AUTH ERROR 401 — Check ALTERLAB_API_KEY secret in GitHub!")
                return None  # Retry is useless for auth errors
            if code == 402:
                logger.error("    BALANCE ERROR 402 — Top up AlterLab account!")
                return None
            if code == 429:
                wait = getattr(e, "retry_after", 15)
                logger.warning(f"    Rate limited — waiting {wait}s")
                time.sleep(wait)
                continue
            logger.error(f"    [{label}] Attempt {attempt}: AlterLabAPIError {code} — {e}")

        except AlterLabTimeoutError:
            logger.warning(f"    [{label}] Attempt {attempt}: Timeout")

        except Exception as e:
            logger.error(f"    [{label}] Attempt {attempt}: Unexpected error — {e}")

        time.sleep(5)

    return None


def smart_fetch(url: str) -> str | None:
    """
    Try CHEAP (html) mode first.
    If HTML too small or empty → try RENDER (js) mode.
    """
    html = fetch_page(url, render=False)
    if html and len(html) > 5000:
        return html
    logger.info("    Cheap mode insufficient → switching to RENDER mode")
    return fetch_page(url, render=True)


# ═══════════════════════════════════════════════════════════════════════════
# PRICE / FORMAT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def parse_int(text: str) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def indian_price(val: int) -> str:
    """Format as Rs.X,XX,XXX"""
    if val is None:
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
    t = text.lower()
    return any(k in t for k in _BANK_KW)


# ═══════════════════════════════════════════════════════════════════════════
# CURRENT PRICE EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

_PRICE_CLASSES = [
    ("div", "_30jeq3 _16Jk6d"),
    ("div", "_30jeq3"),
    ("span", "_30jeq3"),
    ("div", "CEmiEU"),
    ("span", "CEmiEU"),
]


def extract_current_price(soup: BeautifulSoup) -> int | None:
    for tag, cls in _PRICE_CLASSES:
        el = soup.find(tag, class_=cls.split())
        if el:
            val = parse_int(el.get_text())
            if val and 50 <= val <= 50_00_000:
                return val
    # Fallback: first Rs. in page
    for string in soup.strings:
        m = re.search(r"Rs\.\s*([\d,]+)", string)
        if m:
            val = parse_int(m.group(1))
            if val and 50 <= val <= 50_00_000:
                return val
    return None


# ═══════════════════════════════════════════════════════════════════════════
# DISCOUNT EXTRACTION — 4-LAYER
# ═══════════════════════════════════════════════════════════════════════════

_DISC_RE = re.compile(r"(\d{1,2})%")
_DISC_OFF_RE = re.compile(r"(\d{1,2})%\s*off", re.IGNORECASE)
_DISC_CSS = ["UkUFwK", "VGWI6a", "pPAw9j", "_3Ay6Sb", "Bs5uzZ", "_2Tpdn3", "_1psv1zeb9"]


def _valid_disc(val: int, ctx: str) -> bool:
    return 1 <= val <= 95 and not has_bank_kw(ctx)


def extract_discount(soup: BeautifulSoup) -> str:
    # L1: Structural — walk 6 levels up from price tag
    for string in soup.strings:
        if re.search(r"Rs\.\s*[\d,]+", string):
            container = string.parent
            for _ in range(6):
                if container is None or container.name in ("body", "html", "[document]"):
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
                pt = tag.parent.get_text() if tag.parent else ""
                if _valid_disc(v, pt):
                    return f"{v}%"

    # L4: Full text "X% off"
    full = soup.get_text()
    for m in _DISC_OFF_RE.finditer(full):
        v = int(m.group(1))
        if 1 <= v <= 95:
            ctx = full[max(0, m.start() - 150): m.end() + 50]
            if not has_bank_kw(ctx):
                return f"{v}%"

    return ""


# ═══════════════════════════════════════════════════════════════════════════
# ORIGINAL PRICE ALGORITHM
# ═══════════════════════════════════════════════════════════════════════════

_MRP_CSS = ["yRaY8j", "_3I9_wc", "_3auQ3N", "CAWmgp", "_2p6lqe"]


def extract_original_price(
    soup: BeautifulSoup, cur: int, disc_str: str, iphone_mode: bool = False
) -> str:
    if not disc_str or not cur:
        return ""
    disc = int(disc_str.replace("%", ""))
    if disc <= 0:
        return ""

    # Step 1: Calculated fallback
    calc = round(cur / (1 - disc / 100))
    candidates: list[int] = []

    # Step 2: Collect strikethrough numbers
    for tag in soup.find_all(["s", "del", "strike"]):
        v = parse_int(tag.get_text())
        if v and v > cur and 100 <= v <= 50_00_000:
            candidates.append(v)

    if not iphone_mode:
        for tag in soup.find_all(style=re.compile(r"line-through", re.I)):
            v = parse_int(tag.get_text())
            if v and v > cur and 100 <= v <= 50_00_000:
                candidates.append(v)
        for cls in _MRP_CSS:
            for tag in soup.find_all(["div", "span"], class_=cls):
                v = parse_int(tag.get_text())
                if v and v > cur and 100 <= v <= 50_00_000:
                    candidates.append(v)

    # Steps 3+4: Match
    if candidates:
        best = min(candidates, key=lambda x: abs(x - calc))
        diff = abs(best - calc)
        if diff <= 15 or diff <= calc * 0.10:
            return indian_price(best)

    return indian_price(calc)


# ═══════════════════════════════════════════════════════════════════════════
# iPHONE SPECIAL FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

def get_iphone_discount(soup: BeautifulSoup) -> tuple[str, str]:
    """
    Strict iPhone discount extraction.
    - Only looks before 'Protect Promise Fee' boundary
    - Only <s> / <del> HTML tags = real MRP strikethrough
    - CSS line-through = completely ignored (variant prices bleed in)
    - No <s>/<del> found → ("", "") — empty disc + orig
    """
    html = str(soup)
    bi = html.find("Protect Promise Fee")
    limited_soup = BeautifulSoup(html[:bi], "html.parser") if bi != -1 else soup

    mrp: int | None = None
    for tag in limited_soup.find_all(["s", "del"]):
        v = parse_int(tag.get_text())
        if v and 5_000 <= v <= 5_00_000:
            mrp = v
            break

    if mrp is None:
        return "", ""

    disc_str = ""
    for tag in limited_soup.find_all(True):
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
        text = tag.get_text(strip=True)
        m = re.match(r"^(\d\.\d)\s*★?$", text)
        if m:
            return m.group(1)
    m = re.search(r"(\d\.\d)\s*★", soup.get_text())
    return m.group(1) if m else ""


def extract_reviews_pair(soup: BeautifulSoup) -> tuple[str, str]:
    full = soup.get_text()
    m = re.search(r"([\d,]+)\s+Ratings?\s*[&|]\s*([\d,]+)\s+Reviews?", full, re.IGNORECASE)
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
    """Format: '4.1 | 34,452'"""
    rating = extract_rating(soup)
    _, reviews = extract_reviews_pair(soup)
    if rating and reviews:
        return f"{rating} | {reviews}"
    return rating or ""


# ═══════════════════════════════════════════════════════════════════════════
# TABLE CONFIG
# ═══════════════════════════════════════════════════════════════════════════

TABLE_CONFIG: dict[str, dict] = {
    "earbuds": {
        "link_col": "Product Link",
        "cur_col": "Current Price", "orig_col": "Original Price",
        "disc_col": "Discount", "rating_col": "Rating",
        "reviews_col": "Number of Reviews",
        "swap": False, "combined": False, "iphone": False,
    },
    "gaming cpu": {
        "link_col": "Product Link",
        "cur_col": "Current Price", "orig_col": "Original Price",
        "disc_col": "Discount", "rating_col": "Rating",
        "reviews_col": "Number of Reviews",
        "swap": False, "combined": False, "iphone": False,
    },
    "gaming pc": {
        "link_col": "Product Link",
        "cur_col": "Price", "orig_col": "Original Price-2",
        "disc_col": "Discount-2", "rating_col": "Product Rating",
        "reviews_col": "product review",
        "swap": False, "combined": False, "iphone": False,
    },
    "induction": {
        "link_col": "ProductLink",          # NOTE: bina space
        "cur_col": "Discounted Price", "orig_col": "Price",
        "disc_col": "Discount Percentage", "rating_col": "Rating",
        "reviews_col": "Number of Reviews",
        "swap": True, "combined": False, "iphone": False,
    },
    "iphone": {
        "link_col": "Product URL",
        "cur_col": "Discounted Price", "orig_col": "Price",
        "disc_col": "Discount Percentage", "rating_col": "Product Rating",
        "reviews_col": "Number of Reviews", "reviews2_col": "Number of Ratings",
        "swap": True, "combined": False, "iphone": True,
    },
    "keybord": {
        "link_col": "Product Link",
        "cur_col": "Price", "orig_col": "Original Price",
        "disc_col": "Discount", "rating_col": "Rating",
        "reviews_col": "Number of Reviews",
        "swap": False, "combined": False, "iphone": False,
    },
    "laptop": {
        "link_col": "Product Link",
        "cur_col": "Price", "orig_col": "Original Price",
        "disc_col": "Discount", "combined_col": "Rating and Reviews",
        "swap": False, "combined": True, "iphone": False,
    },
    "monitar": {
        "link_col": "Product URL",
        "cur_col": "Current Price", "orig_col": "Original Price",
        "disc_col": "Discount", "rating_col": "Rating",
        "reviews_col": "Number of Reviews",
        "swap": False, "combined": False, "iphone": False,
    },
    "mouse": {
        "link_col": "Product Link",
        "cur_col": "Current Price", "orig_col": "Original Price",
        "disc_col": "Discount", "rating_col": "Rating",
        "reviews_col": "Number of Reviews",
        "swap": False, "combined": False, "iphone": False,
    },
    "smart phone": {
        "link_col": "Product Link",
        "cur_col": "Price", "orig_col": "Original Price",
        "disc_col": "Discount", "combined_col": "Ratings and Reviews",
        "swap": False, "combined": True, "iphone": False,
    },
    "smart+tv": {
        "link_col": "Product Link",
        "cur_col": "Price", "orig_col": "Original Price",
        "disc_col": "Discount", "combined_col": "Ratings and Reviews",
        "swap": False, "combined": True, "iphone": False,
    },
    "smartwatch": {
        "link_col": "Product Link",
        "cur_col": "Price", "orig_col": "Original Price",
        "disc_col": "Discount", "rating_col": "Rating",
        "reviews_col": "Review",
        "swap": False, "combined": False, "iphone": False,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# PER-ROW SCRAPE
# ═══════════════════════════════════════════════════════════════════════════

def scrape_row(url: str, cfg: dict) -> dict:
    html = smart_fetch(url)
    if not html:
        logger.error("    ✗ Page fetch failed")
        return {}

    soup = BeautifulSoup(html, "html.parser")
    update: dict = {}

    # ── iPhone mode ───────────────────────────────────────────────────────
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

    # ── Combined column ───────────────────────────────────────────────────
    if cfg["combined"]:
        cur = extract_current_price(soup)
        if cur:
            update[cfg["cur_col"]] = indian_price(cur)
        disc_str = extract_discount(soup)
        update[cfg["disc_col"]] = disc_str
        update[cfg["orig_col"]] = extract_original_price(soup, cur, disc_str) if (disc_str and cur) else ""
        cr = combined_rating_reviews(soup)
        if cr:
            update[cfg["combined_col"]] = cr
        return update

    # ── Standard table ────────────────────────────────────────────────────
    cur = extract_current_price(soup)
    if cur:
        update[cfg["cur_col"]] = indian_price(cur)
    disc_str = extract_discount(soup)
    update[cfg["disc_col"]] = disc_str
    update[cfg["orig_col"]] = extract_original_price(soup, cur, disc_str) if (disc_str and cur) else ""
    rating = extract_rating(soup)
    if rating:
        update[cfg["rating_col"]] = rating
    _, reviews = extract_reviews_pair(soup)
    if reviews:
        update[cfg["reviews_col"]] = reviews
    return update


# ═══════════════════════════════════════════════════════════════════════════
# TABLE PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════

def process_table(table_name: str, cfg: dict):
    logger.info(f"\n{'━'*55}")
    logger.info(f"  TABLE: {table_name}")
    logger.info(f"{'━'*55}")

    try:
        result = supabase.table(table_name).select("*").execute()
        rows = result.data or []
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

        logger.info(f"  [{i}/{len(rows)}] {url[:75]}")

        try:
            update = scrape_row(url, cfg)
        except Exception as e:
            logger.error(f"    Exception: {e}")
            fail += 1
            time.sleep(3)
            continue

        if not update:
            fail += 1
            time.sleep(3)
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

    logger.info(f"  Done — success={success}  fail={fail}  skip={skip}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 55)
    logger.info("  Flipkart Scraper — AlterLab SDK Edition")
    logger.info("=" * 55)

    for table_name, cfg in TABLE_CONFIG.items():
        try:
            process_table(table_name, cfg)
        except Exception as e:
            logger.error(f"FATAL in '{table_name}': {e}")
            continue  # Non-cancel policy: baaki tables continue karo

    logger.info("\n" + "=" * 55)
    logger.info("  ALL TABLES DONE")
    logger.info("=" * 55)


if __name__ == "__main__":
    main()

