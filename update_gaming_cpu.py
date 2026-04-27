"""
update_gaming_cpu.py  --  Visual Pattern Based Extraction
Table: gaming cpu

Visual patterns from Flipkart product page:
  Rating    : "4.1" (decimal near star)
  Reviews   : number after | separator near rating e.g. "| 56,770"
  Discount  : down-arrow + number + % e.g. "↓54%"
  Orig Price: strikethrough lighter number e.g. "74,999" (crossed out)
  Curr Price: bold large number near "Buy at" e.g. "₹13,260"

Strategy:
  - Attempt 1  : cheap fetch, basic extraction
  - Attempt 2  : cheap fetch, aggressive text scan
  - Attempt 3  : render fetch, full extraction
  - Attempt 4-5: render + wait, brute force all patterns

NO strict cross-validation that clears data.
Only soft check: current < original (if both present).
"""

import os
import re
import time
import logging
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SUPABASE_URL   = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY   = os.environ["SUPABASE_KEY"].strip()
SCRAPERAPI_KEY = os.environ["SCRAPERAPI_KEY"].strip()

SCRAPERAPI_ENDPOINT    = "https://api.scraperapi.com/"
TABLE_NAME             = "gaming cpu"
CRITICAL_FIELDS        = ["Current Price", "Original Price", "Rating", "Number of Reviews"]
TOTAL_ATTEMPTS         = 5
REQUEST_TIMEOUT        = 90
DELAY_BETWEEN_PRODUCTS = 1


def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_products(client: Client) -> list[dict]:
    log.info(f"Fetching rows from '{TABLE_NAME}'...")
    result = client.table(TABLE_NAME).select("*").execute()
    rows = [r for r in result.data if r.get("Product Link", "").strip()]
    log.info(f"   -> {len(rows)} products found.")
    return rows


def fetch_page(url: str, attempt: int) -> BeautifulSoup | None:
    params = {"api_key": SCRAPERAPI_KEY, "url": url, "country_code": "in"}
    if attempt >= 3:
        params["premium"] = "true"
        params["render"]  = "true"
    mode = "RENDER" if attempt >= 3 else "CHEAP"
    try:
        resp = requests.get(
            f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}",
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        log.info(f"   [{mode}] HTTP {resp.status_code}  bytes={len(resp.text)}")
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        log.error(f"   [{mode}] Failed: {exc}")
        return None


def safe(tag, d=""):
    return tag.get_text(strip=True) if tag else d


def to_num(text: str) -> str:
    """Remove all non-digit chars and return string."""
    return re.sub(r"[^\d]", "", text)


def valid_price(val: str) -> bool:
    return val.isdigit() and 100 <= int(val) <= 5000000


def parse_k(text: str) -> str:
    """'56k'->56000, '1.2k'->1200, '56,770'->56770"""
    t = text.strip().replace(",", "")
    m = re.match(r"([\d.]+)[kK]", t)
    if m:
        return str(int(float(m.group(1)) * 1000))
    m = re.match(r"(\d+)", t)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION FUNCTIONS — each focused on one visual pattern
# ─────────────────────────────────────────────────────────────────────────────

def extract_current_price(soup: BeautifulSoup, full_text: str) -> str:
    """
    Current price = bold large ₹ number near Buy button.
    Flipkart shows: ↓54%  ~~74,999~~  ₹13,260
    The rightmost / last price near buy button is current price.
    """
    # Method 1: known CSS selectors
    for sel in [
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
        "div.v1zwn21l.v1zwn24._1psv1zeb9._1psv1ze0",
        "div.Nx9bqj.CxhGGd",
        "div.Nx9bqj",
        "div._30jeq3._16Jk6d",
        "div._30jeq3",
        "div.CEmiEU",
        "div.dyC4hf",
    ]:
        tag = soup.select_one(sel)
        if tag:
            val = to_num(safe(tag))
            if val and valid_price(val):
                return val

    # Method 2: "Buy at ₹X,XXX" pattern
    m = re.search(r"Buy\s*at\s*₹\s*([\d,]+)", full_text)
    if m:
        val = m.group(1).replace(",", "")
        if valid_price(val):
            return val

    # Method 3: find all ₹ prices on page, pick the one near "Add to cart"
    cart_section = soup.find(string=re.compile(r"Add to cart", re.I))
    if cart_section:
        parent = cart_section.find_parent("div")
        for _ in range(5):
            if parent:
                prices = re.findall(r"₹\s*([\d,]+)", parent.get_text())
                valid_prices = sorted(
                    [int(p.replace(",", "")) for p in prices if valid_price(p.replace(",", ""))]
                )
                if valid_prices:
                    return str(valid_prices[0])  # smallest = current price
                parent = parent.find_parent("div")

    # Method 4: regex scan — find ₹X,XXX pattern, return smallest valid price
    all_prices = re.findall(r"₹\s*([\d,]{3,})", full_text)
    valid_list = sorted([
        int(p.replace(",", "")) for p in all_prices
        if valid_price(p.replace(",", ""))
    ])
    # Return smallest (current price is always less than MRP)
    # But filter out very small numbers
    for p in valid_list:
        if p >= 1000:
            return str(p)

    return ""


def extract_original_price(soup: BeautifulSoup, full_text: str, current_price: str) -> str:
    """
    Original price = strikethrough number (MRP).
    Flipkart shows it lighter with a line through it.
    It is ALWAYS greater than current price.
    """
    # Method 1: known CSS selectors for strikethrough price
    for sel in [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.v1zwn21m._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA",
        "div.yRaY8j",
        "div._3I9_wc._2p6lqe",
        "div._3I9_wc",
    ]:
        tag = soup.select_one(sel)
        if tag:
            val = to_num(safe(tag))
            if val and valid_price(val):
                if not current_price or int(val) > int(current_price):
                    return val

    # Method 2: tags with line-through style
    for tag in soup.find_all(True):
        style   = tag.get("style", "")
        classes = " ".join(tag.get("class", []))
        if "line-through" in style or "linethrough" in classes.lower() or "strike" in classes.lower():
            val = to_num(safe(tag))
            if val and valid_price(val):
                if not current_price or int(val) > int(current_price):
                    return val

    # Method 3: MRP keyword in text
    m = re.search(r"M\.?R\.?P\.?\s*:?\s*₹?\s*([\d,]+)", full_text, re.I)
    if m:
        val = m.group(1).replace(",", "")
        if valid_price(val):
            if not current_price or int(val) > int(current_price):
                return val

    # Method 4: find largest price on page (MRP is largest)
    all_prices = re.findall(r"₹\s*([\d,]{3,})", full_text)
    valid_list = sorted([
        int(p.replace(",", "")) for p in all_prices if valid_price(p.replace(",", ""))
    ], reverse=True)
    cur_int = int(current_price) if current_price else 0
    for p in valid_list:
        if p > cur_int:
            return str(p)

    return ""


def extract_discount(soup: BeautifulSoup, full_text: str, cur: str, orig: str) -> str:
    """
    Discount = down-arrow + number + % e.g. ↓54% or 54% off
    """
    # Method 1: down arrow patterns (↓ ↘ ▼)
    m = re.search(r"[↓↘▼⬇]\s*(\d{1,2})\s*%", full_text)
    if m and 1 <= int(m.group(1)) <= 99:
        return m.group(1) + "%"

    # Method 2: known selector
    tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if tag:
        m = re.search(r"(\d{1,2})%", safe(tag))
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"

    # Method 3: "X% off" pattern in short text
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if len(text) > 25:
            continue
        m = re.search(r"(\d{1,2})%\s*off", text, re.I)
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"

    # Method 4: calculate from prices
    if cur and orig and cur.isdigit() and orig.isdigit():
        c, o = int(cur), int(orig)
        if o > c > 0:
            disc = round((o - c) / o * 100)
            if 1 <= disc <= 99:
                log.info(f"   [AUTO] Discount = {disc}%")
                return str(disc) + "%"

    return ""


def extract_rating(soup: BeautifulSoup, full_text: str) -> str:
    """
    Rating = decimal like "4.1" near star symbol ★
    Always between 1.0 and 5.0
    """
    # Method 1: exact decimal in any tag
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.\d", text):
            return text

    # Method 2: near star symbol in full text
    m = re.search(r"([1-5]\.\d)\s*[★✩⭐|]|[★✩⭐]\s*([1-5]\.\d)", full_text)
    if m:
        return m.group(1) or m.group(2)

    # Method 3: "X.X out of 5" pattern
    m = re.search(r"([1-5]\.\d)\s*out\s*of\s*5", full_text, re.I)
    if m:
        return m.group(1)

    # Method 4: standalone decimal in rating-class tags
    for tag in soup.find_all(["div", "span"]):
        classes = " ".join(tag.get("class", []))
        if any(k in classes for k in ["XQDdHH", "_3LWZlK", "ipqd2A", "Y1HWO0", "rating"]):
            text = safe(tag).strip()
            m = re.search(r"([1-5]\.?\d?)", text)
            if m:
                return m.group(1)

    return ""


def extract_reviews(soup: BeautifulSoup, full_text: str, rating: str) -> str:
    """
    Reviews = number right after rating separated by | e.g. "4.1 ★ | 56,770"
    May have k suffix: "56k" = 56000
    """
    # Method 1: known selector
    for sel in [
        "div._1psv1zeb9._1psv1ze0._1psv1zegu",
        "span.Wphh3N",
        "span._2_R_DZ",
    ]:
        tag = soup.select_one(sel)
        if tag:
            text = safe(tag)
            nums = re.findall(r"[\d,]+[kK]?", text)
            if nums:
                return parse_k(nums[0])

    # Method 2: pattern right after rating "4.1 | 56,770"
    if rating:
        pattern = re.escape(rating) + r"\s*[★✩⭐]?\s*[|,]\s*([\d,]+[kK]?)"
        m = re.search(pattern, full_text)
        if m:
            return parse_k(m.group(1))

    # Method 3: "X Ratings" or "X Reviews" or "X verified"
    for pattern in [
        r"([\d,]+[kK]?)\s+[Rr]ating",
        r"([\d,]+[kK]?)\s+[Rr]eview",
        r"([\d,]+[kK]?)\s+[Vv]erified",
        r"based on\s+([\d,]+[kK]?)\s+rating",
    ]:
        m = re.search(pattern, full_text, re.I)
        if m:
            return parse_k(m.group(1))

    # Method 4: number immediately after rating in full text
    if rating:
        m = re.search(re.escape(rating) + r"[^\d]{1,15}([\d,]{2,}[kK]?)", full_text)
        if m:
            val = parse_k(m.group(1))
            if val.isdigit() and int(val) >= 10:
                return val

    return ""


def parse_all(soup: BeautifulSoup) -> dict:
    full_text = soup.get_text(" ", strip=True)

    cur   = extract_current_price(soup, full_text)
    orig  = extract_original_price(soup, full_text, cur)
    disc  = extract_discount(soup, full_text, cur, orig)
    rat   = extract_rating(soup, full_text)
    revs  = extract_reviews(soup, full_text, rat)

    # Soft sanity: if current >= original, clear original only
    if cur and orig and int(cur) >= int(orig):
        log.warning(f"   SOFT SANITY: cur({cur}) >= orig({orig}) -- clearing original only")
        orig = ""

    data = {
        "Current Price":     cur,
        "Original Price":    orig,
        "Discount":          disc,
        "Rating":            rat,
        "Number of Reviews": revs,
    }
    return data


def missing_fields(data: dict) -> list[str]:
    return [f for f in CRITICAL_FIELDS if not data.get(f)]


def update_row(client: Client, product_link: str, data: dict) -> bool:
    try:
        client.table(TABLE_NAME).update(data).eq("Product Link", product_link).execute()
        log.info(
            f"   [OK] Price:{data['Current Price']}  "
            f"MRP:{data['Original Price']}  "
            f"Disc:{data['Discount']}  "
            f"Rating:{data['Rating']}  "
            f"Reviews:{data['Number of Reviews']}"
        )
        return True
    except Exception as exc:
        log.error(f"   X DB: {exc}")
        return False


def main():
    log.info("=" * 70)
    log.info(f"  Flipkart Updater -- {TABLE_NAME} -- Visual Pattern Mode")
    log.info("=" * 70)

    client   = get_client()
    products = fetch_all_products(client)

    total     = len(products)
    updated   = 0
    partial   = 0
    db_failed = 0

    for idx, row in enumerate(products, start=1):
        url = row["Product Link"].strip()

        log.info(f"\n{'─'*60}")
        log.info(f"[{idx}/{total}]  {url[:85]}")
        log.info(f"{'─'*60}")

        # Merge best data across all attempts
        best = {}

        for attempt in range(1, TOTAL_ATTEMPTS + 1):
            log.info(f"   Attempt {attempt}/{TOTAL_ATTEMPTS}")

            soup = fetch_page(url, attempt)
            if not soup:
                time.sleep(3)
                continue

            data = parse_all(soup)
            log.info(f"   Got: {data}")

            # Merge: keep non-empty values from each attempt
            for field, val in data.items():
                if val and not best.get(field):
                    best[field] = val

            missing = missing_fields(best)
            if not missing:
                log.info(f"   All fields found on attempt {attempt}.")
                break

            log.warning(f"   Still missing: {missing}")
            time.sleep(2)

        log.info(f"   FINAL: {best}")
        missing = missing_fields(best)

        if not any(best.values()):
            log.warning("   Zero data -- skipping.")
            partial += 1
        else:
            ok = update_row(client, url, best)
            if ok and not missing:
                updated += 1
            elif ok:
                partial += 1
                log.warning(f"   Partial update. Missing: {missing}")
            else:
                db_failed += 1

        time.sleep(DELAY_BETWEEN_PRODUCTS)

    log.info("\n" + "=" * 70)
    log.info(f"  Done -- {TABLE_NAME}")
    log.info(f"  Fully updated   : {updated}")
    log.info(f"  Partial/skipped : {partial}")
    log.info(f"  DB errors       : {db_failed}")
    log.info(f"  Total           : {total}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()

