"""
update_gaming_cpu.py  --  Visual Pattern Based Extraction
Table: gaming cpu

Fixed: Original Price uses JSON-LD + <s> tag
Fixed: Discount uses down-arrow unicode pattern
Cross-validation restored (soft only).
"""

import os
import re
import json
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
    return re.sub(r"[^\d]", "", text).strip()


def valid_price(val: str) -> bool:
    return val.isdigit() and 100 <= int(val) <= 5000000


def parse_k(text: str) -> str:
    t = text.strip().replace(",", "")
    m = re.match(r"([\d.]+)[kK]", t)
    if m:
        return str(int(float(m.group(1)) * 1000))
    m = re.match(r"(\d+)", t)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
def extract_current_price(soup: BeautifulSoup, full_text: str) -> str:
    for sel in [
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
        "div.v1zwn21l.v1zwn24._1psv1zeb9._1psv1ze0",
        "div.Nx9bqj.CxhGGd",
        "div.Nx9bqj",
        "div._30jeq3._16Jk6d",
        "div._30jeq3",
        "div.CEmiEU",
    ]:
        tag = soup.select_one(sel)
        if tag:
            val = to_num(safe(tag))
            if val and valid_price(val):
                return val

    m = re.search(r"Buy\s*at\s*[₹Rs\.]+\s*([\d,]+)", full_text, re.I)
    if m:
        val = m.group(1).replace(",", "")
        if valid_price(val):
            return val

    cart_tag = soup.find(string=re.compile(r"Add to cart", re.I))
    if cart_tag:
        parent = cart_tag.find_parent("div")
        for _ in range(6):
            if parent:
                prices = re.findall(r"[₹Rs\.]+\s*([\d,]+)", parent.get_text())
                valid_list = sorted([
                    int(p.replace(",", "")) for p in prices
                    if valid_price(p.replace(",", ""))
                ])
                if valid_list:
                    return str(valid_list[0])
                parent = parent.find_parent("div")

    return ""


# ─────────────────────────────────────────────────────────────────────────────
def extract_original_price(soup: BeautifulSoup, full_text: str, current_price: str, discount: str = "") -> str:
    """
    Original Price (MRP) Extraction — Maximum Accuracy

    CORE STRATEGY:
      We already have Current Price and Discount correctly.
      Math: MRP = Current / (1 - Discount/100)
      e.g.  13902 / (1 - 0.54) = ~30,221  → closest on page = 29,999

      So we calculate expected MRP, then VERIFY by finding
      the closest matching number on the actual page.
      This is immune to CSS class changes and HTML structure changes.

    FALLBACK CHAIN:
      1. Math-first: calculate expected MRP → find closest on page
      2. Between-pattern: number between X% and ₹current in text
      3. <s> HTML tag (strikethrough element)
      4. JSON-LD structured data
      5. CSS class selectors
      6. style=line-through tags
    """

    def is_valid(val: str) -> bool:
        if not val or not valid_price(val):
            return False
        if current_price and current_price.isdigit():
            return int(val) > int(current_price)
        return True

    # ── METHOD 1: MATH-FIRST (most powerful — uses correct current+discount) ──
    # If we know current price AND discount, we know approximately what MRP is.
    # Then find that number on the page. This works regardless of HTML structure.
    if current_price and current_price.isdigit() and discount:
        disc_clean = discount.replace("%", "").strip()
        if disc_clean.isdigit():
            disc_val = int(disc_clean)
            cur_val  = int(current_price)
            if 1 <= disc_val <= 99 and cur_val > 0:
                expected_mrp = cur_val / (1 - disc_val / 100)

                # Collect ALL numbers from the page
                all_numbers = re.findall(r"[\d,]{3,}", full_text)

                best_match   = ""
                best_pct_diff = float("inf")

                for n in all_numbers:
                    val_str = n.replace(",", "")
                    if not val_str.isdigit():
                        continue
                    val_int = int(val_str)
                    if not valid_price(val_str):
                        continue
                    if val_int <= cur_val:
                        continue  # MRP must be greater than current
                    pct_diff = abs(val_int - expected_mrp) / expected_mrp
                    if pct_diff < best_pct_diff and pct_diff <= 0.20:  # within 20%
                        best_pct_diff = pct_diff
                        best_match    = val_str

                if best_match:
                    log.info(f"   [MATH] expected_mrp≈{int(expected_mrp)}  found={best_match}  diff={best_pct_diff:.1%}")
                    return best_match

    # ── METHOD 2: BETWEEN-PATTERN ─────────────────────────────────────────────
    # On Flipkart page text sequence is: "54% 29,999 ₹13,902"
    # MRP sits between % sign and ₹ current price
    if current_price:
        cur_pos = full_text.find(current_price)
        if cur_pos > 30:
            window = full_text[max(0, cur_pos - 200): cur_pos]
            candidates = re.findall(r"[\d,]{3,}", window)
            for c in reversed(candidates):
                val = c.replace(",", "")
                if is_valid(val):
                    log.info(f"   [BETWEEN] orig={val}")
                    return val

    # ── METHOD 3: <s> HTML TAG (strikethrough) ────────────────────────────────
    for s_tag in soup.find_all("s"):
        val = to_num(safe(s_tag))
        if is_valid(val):
            log.info(f"   [<s>] orig={val}")
            return val

    # ── METHOD 4: JSON-LD STRUCTURED DATA ────────────────────────────────────
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            obj   = json.loads(script.string or "")
            items = obj if isinstance(obj, list) else [obj]
            for item in items:
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                for key in ["highPrice", "originalPrice", "listPrice"]:
                    val = to_num(str(offers.get(key, "")))
                    if is_valid(val):
                        log.info(f"   [JSON-LD:{key}] orig={val}")
                        return val
        except Exception:
            pass

    # ── METHOD 5: CSS CLASS SELECTORS ────────────────────────────────────────
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
            if is_valid(val):
                return val

    # ── METHOD 6: STYLE LINE-THROUGH ─────────────────────────────────────────
    for tag in soup.find_all(True):
        style   = tag.get("style", "")
        classes = " ".join(tag.get("class", []))
        if "line-through" in style or "strike" in classes.lower():
            val = to_num(safe(tag))
            if is_valid(val):
                return val

    # ── METHOD 7: MRP KEYWORD ────────────────────────────────────────────────
    m = re.search(r"M\.?R\.?P\.?\s*:?\s*[₹]*\s*([\d,]+)", full_text, re.I)
    if m:
        val = m.group(1).replace(",", "")
        if is_valid(val):
            return val

    return ""


def extract_discount(soup: BeautifulSoup, full_text: str, cur: str, orig: str) -> str:
    """
    Discount = down-arrow(↓) + number(1-99) + percent(%)
    This is directly visible on page: ↓54%
    """
    # 1. Down arrow unicode + number + % (exact visual pattern)
    m = re.search(r"[\u2193\u2198\u25bc\u2b07\u21a1]\s*(\d{1,2})\s*%", full_text)
    if m and 1 <= int(m.group(1)) <= 99:
        log.info(f"   [arrow] disc={m.group(1)}%")
        return m.group(1) + "%"

    # 2. Known CSS selector
    tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if tag:
        m = re.search(r"(\d{1,2})%", safe(tag))
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"

    # 3. Short element with "X% off"
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if len(text) > 20:
            continue
        m = re.search(r"(\d{1,2})%\s*(off)?$", text, re.I)
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"

    # 4. Scan full text for standalone X% near price context
    for m in re.finditer(r"\b(\d{1,2})%\b", full_text):
        val = int(m.group(1))
        if 5 <= val <= 99:
            # Check surrounding context has price-related words
            start = max(0, m.start() - 30)
            end   = min(len(full_text), m.end() + 30)
            context = full_text[start:end]
            if any(k in context for k in ["₹", "off", "discount", "save", "price"]):
                return str(val) + "%"

    # 5. Auto-calculate from prices
    if cur and orig and cur.isdigit() and orig.isdigit():
        c, o = int(cur), int(orig)
        if o > c > 0:
            disc = round((o - c) / o * 100)
            if 1 <= disc <= 99:
                log.info(f"   [AUTO] disc={disc}%")
                return str(disc) + "%"

    return ""


# ─────────────────────────────────────────────────────────────────────────────
def extract_rating(soup: BeautifulSoup, full_text: str) -> str:
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.\d", text):
            return text

    m = re.search(r"([1-5]\.\d)\s*[★✩⭐|]|[★✩⭐]\s*([1-5]\.\d)", full_text)
    if m:
        return m.group(1) or m.group(2)

    m = re.search(r"([1-5]\.\d)\s*out\s*of\s*5", full_text, re.I)
    if m:
        return m.group(1)

    return ""


# ─────────────────────────────────────────────────────────────────────────────
def extract_reviews(soup: BeautifulSoup, full_text: str, rating: str) -> str:
    """
    Reviews extraction.
    Visual pattern on Flipkart: "3.7 ★ | 356"
    Number after | separator next to rating = review count.
    May be plain number or have k suffix (56k = 56000).
    """

    # Method 1: rating | number pattern (exact visual match from page)
    # Handles: "3.7 ★ | 356"  or  "4.1 | 1,821"
    if rating:
        patterns = [
            re.escape(rating) + r"\s*[★✩⭐]\s*[|]\s*([\d,]+[kK]?)",
            re.escape(rating) + r"\s*[|]\s*([\d,]+[kK]?)",
            re.escape(rating) + r"\s*[★✩⭐]\s*([\d,]+[kK]?)",
            re.escape(rating) + r"[^\d]{1,5}([\d,]{2,}[kK]?)",
        ]
        for pat in patterns:
            m = re.search(pat, full_text)
            if m:
                val = parse_k(m.group(1))
                if val.isdigit() and int(val) >= 2:
                    log.info(f"   [RATING|] reviews={val}")
                    return val

    # Method 2: known CSS selectors
    for sel in [
        "div._1psv1zeb9._1psv1ze0._1psv1zegu",
        "span.Wphh3N",
        "span._2_R_DZ",
        "span._13vcmD",
    ]:
        tag = soup.select_one(sel)
        if tag:
            text = safe(tag)
            nums = re.findall(r"[\d,]+[kK]?", text)
            if nums:
                val = parse_k(nums[0])
                if val.isdigit() and int(val) >= 2:
                    return val

    # Method 3: text patterns
    for pattern in [
        r"([\d,]+[kK]?)\s+[Rr]ating",
        r"([\d,]+[kK]?)\s+[Rr]eview",
        r"based on\s+([\d,]+[kK]?)\s+rating",
        r"([\d,]+[kK]?)\s+verified",
    ]:
        m = re.search(pattern, full_text, re.I)
        if m:
            val = parse_k(m.group(1))
            if val.isdigit() and int(val) >= 2:
                return val

    # Method 4: find number inside rating-block tag
    for tag in soup.find_all(["div", "span"]):
        text    = safe(tag).strip()
        classes = " ".join(tag.get("class", []))
        # Tag that contains both rating and review count
        if rating and rating in text and "|" in text:
            parts = text.split("|")
            for part in parts:
                part = part.strip()
                if part != rating and re.match(r"[\d,]+[kK]?$", part):
                    val = parse_k(part)
                    if val.isdigit() and int(val) >= 2:
                        log.info(f"   [PIPE-SPLIT] reviews={val}")
                        return val

    return ""


# ─────────────────────────────────────────────────────────────────────────────
def parse_all(soup: BeautifulSoup) -> dict:
    full_text = soup.get_text(" ", strip=True)

    cur  = extract_current_price(soup, full_text)
    disc = extract_discount(soup, full_text, cur, "")
    orig = extract_original_price(soup, full_text, cur, disc)
    if not disc:
        disc = extract_discount(soup, full_text, cur, orig)
    rat  = extract_rating(soup, full_text)
    revs = extract_reviews(soup, full_text, rat)

    # Soft validation: current must be less than original
    if cur and orig and cur.isdigit() and orig.isdigit():
        if int(cur) >= int(orig):
            log.warning(f"   SOFT SANITY: cur({cur}) >= orig({orig}) -- clearing orig")
            orig = ""

    return {
        "Current Price":     cur,
        "Original Price":    orig,
        "Discount":          disc,
        "Rating":            rat,
        "Number of Reviews": revs,
    }


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
    log.info(f"  Flipkart Updater -- {TABLE_NAME}")
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

        best = {}

        for attempt in range(1, TOTAL_ATTEMPTS + 1):
            log.info(f"   Attempt {attempt}/{TOTAL_ATTEMPTS}")

            soup = fetch_page(url, attempt)
            if not soup:
                time.sleep(3)
                continue

            data = parse_all(soup)
            log.info(f"   Got: {data}")

            # Merge -- keep non-empty values
            for field, val in data.items():
                if val and not best.get(field):
                    best[field] = val

            if not missing_fields(best):
                log.info(f"   All fields found on attempt {attempt}.")
                break

            log.warning(f"   Still missing: {missing_fields(best)}")
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
                log.warning(f"   Partial. Missing: {missing}")
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
