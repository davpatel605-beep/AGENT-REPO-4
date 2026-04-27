"""
update_gaming_cpu.py  --  Maximum Accuracy Mode
Table: gaming cpu

Attempt strategy:
  Attempt 1-2 : cheap request (1 credit each)
  Attempt 3   : premium + render (10-25 credits)
  Attempt 4-5 : special -- aggressive multi-strategy extraction

Cross-validation:
  If discount + current price are known, verify original price:
    expected_original = current / (1 - discount/100)
  If mismatch > 10%, re-fetch.

Environment Variables:
  SUPABASE_URL, SUPABASE_KEY, SCRAPERAPI_KEY
"""

import os
import re
import time
import logging
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY   = os.environ["SUPABASE_KEY"].strip()
SCRAPERAPI_KEY = os.environ["SCRAPERAPI_KEY"].strip()

SCRAPERAPI_ENDPOINT    = "https://api.scraperapi.com/"
TABLE_NAME             = "gaming cpu"
CRITICAL_FIELDS        = ["Current Price", "Original Price", "Rating", "Number of Reviews"]
NORMAL_ATTEMPTS        = 3
SPECIAL_ATTEMPTS       = 2
TOTAL_ATTEMPTS         = NORMAL_ATTEMPTS + SPECIAL_ATTEMPTS
REQUEST_TIMEOUT        = 90
DELAY_BETWEEN_PRODUCTS = 1
PRICE_MISMATCH_TOLERANCE = 0.15   # 15% tolerance in cross-validation


# ── Supabase ──────────────────────────────────────────────────────────────────
def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_products(client: Client) -> list[dict]:
    log.info(f"Fetching all rows from '{TABLE_NAME}'...")
    result = client.table(TABLE_NAME).select("*").execute()
    rows = [r for r in result.data if r.get("Product Link", "").strip()]
    log.info(f"   -> {len(rows)} product(s) found.")
    return rows


# ── ScraperAPI ────────────────────────────────────────────────────────────────
def fetch_page(url: str, mode: str = "cheap") -> BeautifulSoup | None:
    """
    mode = 'cheap'   -> 1 credit
    mode = 'render'  -> premium + render (10-25 credits)
    mode = 'special' -> render + extra wait headers
    """
    params = {
        "api_key":      SCRAPERAPI_KEY,
        "url":          url,
        "country_code": "in",
    }
    if mode in ("render", "special"):
        params["premium"] = "true"
        params["render"]  = "true"
    if mode == "special":
        params["wait_for_selector"] = "div._30jeq3,div.Nx9bqj,div.v1zwn21l"

    full = f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}"
    try:
        resp = requests.get(full, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        log.info(f"   [MODE:{mode.upper()}] HTTP {resp.status_code}  len={len(resp.text)}")
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.RequestException as exc:
        log.error(f"   [MODE:{mode.upper()}] Failed: {exc}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def safe(tag, default=""):
    return tag.get_text(strip=True) if tag else default


def digits_only(text: str) -> str:
    return re.sub(r"[^\d]", "", text).strip()


def valid_price(val: str, min_p=50, max_p=2000000) -> bool:
    return val.isdigit() and min_p <= int(val) <= max_p


def parse_k_number(text: str) -> str:
    """56k -> 56000, 1.2k -> 1200, 56,770 -> 56770"""
    text = text.strip().replace(",", "")
    m = re.match(r"([\d\.]+)\s*[kK]", text)
    if m:
        return str(int(float(m.group(1)) * 1000))
    m = re.match(r"(\d+)", text)
    return m.group(1) if m else ""


# ── Cross-validation ──────────────────────────────────────────────────────────
def cross_validate(data: dict) -> tuple[bool, str]:
    """
    Check if Current Price, Original Price, Discount are consistent.
    Returns (is_valid, reason)
    """
    cur  = data.get("Current Price", "")
    orig = data.get("Original Price", "")
    disc = data.get("Discount", "").replace("%", "").strip()

    if not cur or not orig:
        return True, "skip"   # can't validate without both prices

    cur_val  = int(cur)
    orig_val = int(orig)

    # Current must be less than original
    if cur_val >= orig_val:
        return False, f"Current({cur_val}) >= Original({orig_val})"

    # If discount available, verify prices match
    if disc.isdigit():
        disc_val = int(disc)
        # expected current = orig * (1 - disc/100)
        expected_cur = orig_val * (1 - disc_val / 100)
        tolerance    = expected_cur * PRICE_MISMATCH_TOLERANCE
        if abs(cur_val - expected_cur) > tolerance:
            return False, (
                f"Price mismatch: orig={orig_val} disc={disc_val}% "
                f"expected_cur≈{int(expected_cur)} got_cur={cur_val}"
            )

    return True, "ok"


def auto_fill_missing(data: dict) -> dict:
    """
    If 2 of 3 (current, original, discount) are known, calculate the third.
    """
    cur  = data.get("Current Price", "")
    orig = data.get("Original Price", "")
    disc = data.get("Discount", "").replace("%", "").strip()

    if cur and orig and not disc:
        cur_v  = int(cur)
        orig_v = int(orig)
        if orig_v > cur_v:
            d = round((orig_v - cur_v) / orig_v * 100)
            data["Discount"] = str(d) + "%"
            log.info(f"   [AUTO] Discount calculated: {data['Discount']}")

    elif cur and disc and not orig:
        cur_v  = int(cur)
        disc_v = int(disc)
        if 0 < disc_v < 100:
            orig_v = round(cur_v / (1 - disc_v / 100))
            data["Original Price"] = str(orig_v)
            log.info(f"   [AUTO] Original Price calculated: {data['Original Price']}")

    elif orig and disc and not cur:
        orig_v = int(orig)
        disc_v = int(disc)
        if 0 < disc_v < 100:
            cur_v = round(orig_v * (1 - disc_v / 100))
            data["Current Price"] = str(cur_v)
            log.info(f"   [AUTO] Current Price calculated: {data['Current Price']}")

    return data


# ── Parser ────────────────────────────────────────────────────────────────────
def parse_product_page(soup: BeautifulSoup, aggressive: bool = False) -> dict:
    data = {
        "Current Price":     "",
        "Original Price":    "",
        "Discount":          "",
        "Rating":            "",
        "Number of Reviews": "",
    }

    all_text  = soup.get_text(" ", strip=True)
    all_tags  = soup.find_all(["div", "span"])

    # ══════════════════════════════════════════════════════════════════════════
    # CURRENT PRICE
    # ══════════════════════════════════════════════════════════════════════════
    cur_selectors = [
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
        "div.Nx9bqj.CxhGGd",
        "div.Nx9bqj",
        "div._30jeq3._16Jk6d",
        "div._30jeq3",
        "div.CEmiEU",
    ]
    for sel in cur_selectors:
        tag = soup.select_one(sel)
        if tag:
            val = digits_only(safe(tag))
            if val and valid_price(val):
                data["Current Price"] = val
                break

    # Fallback A: "Buy at ₹X"
    if not data["Current Price"]:
        m = re.search(r"Buy\s+at\s+₹\s*([\d,]+)", all_text)
        if m:
            val = m.group(1).replace(",", "")
            if valid_price(val):
                data["Current Price"] = val

    # Fallback B: largest ₹X near Add to cart
    if not data["Current Price"] and aggressive:
        cart_tag = soup.find(string=re.compile(r"Add to cart", re.IGNORECASE))
        if cart_tag:
            parent = cart_tag.find_parent("div")
            if parent:
                prices = re.findall(r"₹\s*([\d,]+)", parent.get_text())
                candidates = [p.replace(",", "") for p in prices if valid_price(p.replace(",", ""))]
                if candidates:
                    data["Current Price"] = min(candidates, key=int)  # lowest = current

    # ══════════════════════════════════════════════════════════════════════════
    # ORIGINAL PRICE (strikethrough MRP)
    # ══════════════════════════════════════════════════════════════════════════
    mrp_selectors = [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA",
        "div.yRaY8j",
        "div._3I9_wc._2p6lqe",
        "div._3I9_wc",
        "div.strikethrough",
    ]
    for sel in mrp_selectors:
        tag = soup.select_one(sel)
        if tag:
            val = digits_only(safe(tag))
            if val and valid_price(val):
                data["Original Price"] = val
                break

    # Fallback: tags with line-through style
    if not data["Original Price"]:
        for tag in all_tags:
            style   = tag.get("style", "")
            classes = " ".join(tag.get("class", []))
            if "line-through" in style or "lineThrough" in classes or "strike" in classes.lower():
                val = digits_only(safe(tag))
                if val and valid_price(val):
                    data["Original Price"] = val
                    break

    # Fallback aggressive: MRP pattern in text
    if not data["Original Price"] and aggressive:
        m = re.search(r"M\.?R\.?P\.?\s*:?\s*₹?\s*([\d,]+)", all_text, re.IGNORECASE)
        if m:
            val = m.group(1).replace(",", "")
            if valid_price(val):
                data["Original Price"] = val

    # ══════════════════════════════════════════════════════════════════════════
    # DISCOUNT
    # ══════════════════════════════════════════════════════════════════════════
    disc_sel = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if disc_sel:
        m = re.search(r"(\d+)%", safe(disc_sel))
        if m and 1 <= int(m.group(1)) <= 99:
            data["Discount"] = m.group(1) + "%"

    # Down arrow + % pattern
    if not data["Discount"]:
        m = re.search(r"[↓↘▼⬇]\s*(\d+)\s*%", all_text)
        if m and 1 <= int(m.group(1)) <= 99:
            data["Discount"] = m.group(1) + "%"

    # Short tag with X% off
    if not data["Discount"]:
        for tag in all_tags:
            text = safe(tag).strip()
            if len(text) > 20:
                continue
            m = re.search(r"(\d+)%\s*(off)?$", text, re.IGNORECASE)
            if m and 1 <= int(m.group(1)) <= 99:
                data["Discount"] = m.group(1) + "%"
                break

    # ══════════════════════════════════════════════════════════════════════════
    # RATING
    # ══════════════════════════════════════════════════════════════════════════
    # Exact decimal like "4.1"
    for tag in all_tags:
        text = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.\d", text):
            data["Rating"] = text
            break

    # Near star symbol
    if not data["Rating"]:
        m = re.search(r"([1-5]\.\d)\s*[★✩⭐|]|[★✩⭐]\s*([1-5]\.\d)", all_text)
        if m:
            data["Rating"] = m.group(1) or m.group(2)

    # Whole number rating with class hint
    if not data["Rating"] and aggressive:
        for tag in all_tags:
            text    = safe(tag).strip()
            classes = " ".join(tag.get("class", []))
            if re.fullmatch(r"[1-5]", text) and any(
                k in classes for k in ["XQDdHH", "_3LWZlK", "ipqd2A", "Y1HWO0", "rating"]
            ):
                data["Rating"] = text
                break

    # ══════════════════════════════════════════════════════════════════════════
    # NUMBER OF REVIEWS
    # ══════════════════════════════════════════════════════════════════════════

    # Selector 1: known class
    rev_tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zegu")
    if rev_tag:
        nums = re.findall(r"[\d,]+", safe(rev_tag))
        if nums:
            data["Number of Reviews"] = nums[0].replace(",", "")

    # Selector 2: span with Wphh3N
    if not data["Number of Reviews"]:
        rev_tag = soup.select_one("span.Wphh3N")
        if rev_tag:
            nums = re.findall(r"[\d,]+", safe(rev_tag))
            if nums:
                data["Number of Reviews"] = nums[0].replace(",", "")

    # Pattern: "X Ratings" or "X Reviews"
    if not data["Number of Reviews"]:
        for pattern in [
            r"([\d,]+[kK]?)\s+[Rr]ating",
            r"([\d,]+[kK]?)\s+[Rr]eview",
            r"([\d,]+[kK]?)\s+verified",
        ]:
            m = re.search(pattern, all_text)
            if m:
                data["Number of Reviews"] = parse_k_number(m.group(1))
                break

    # Pattern near rating: "4.1 ★ | 56,770"
    if not data["Number of Reviews"] and data["Rating"]:
        pattern = re.escape(data["Rating"]) + r"[^\d]{1,10}([\d,]+[kK]?)"
        m = re.search(pattern, all_text)
        if m:
            data["Number of Reviews"] = parse_k_number(m.group(1))

    # Aggressive: look for any large number (>100) near "rating" word
    if not data["Number of Reviews"] and aggressive:
        for tag in all_tags:
            text = safe(tag).strip()
            m = re.search(r"([\d,]{3,})\s*[Rr]ating", text)
            if m:
                data["Number of Reviews"] = m.group(1).replace(",", "")
                break

    # ══════════════════════════════════════════════════════════════════════════
    # AUTO-FILL & VALIDATE
    # ══════════════════════════════════════════════════════════════════════════
    data = auto_fill_missing(data)

    return data


def missing_fields(data: dict) -> list[str]:
    return [f for f in CRITICAL_FIELDS if not data.get(f)]


# ── Update DB ─────────────────────────────────────────────────────────────────
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
        log.error(f"   X DB error: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 70)
    log.info(f"  Flipkart Updater  --  Table: {TABLE_NAME}  (Max Accuracy Mode)")
    log.info("=" * 70)

    client   = get_client()
    products = fetch_all_products(client)

    total     = len(products)
    updated   = 0
    partial   = 0
    db_failed = 0

    for idx, row in enumerate(products, start=1):
        product_link = row["Product Link"].strip()

        log.info(f"\n{'─'*60}")
        log.info(f"[{idx}/{total}]  {product_link[:85]}")
        log.info(f"{'─'*60}")

        data    = {}
        success = False

        for attempt in range(1, TOTAL_ATTEMPTS + 1):
            is_special  = attempt > NORMAL_ATTEMPTS
            aggressive  = is_special

            if attempt <= 2:
                mode = "cheap"
            elif attempt == 3:
                mode = "render"
            else:
                mode = "special"

            log.info(f"   --- Attempt {attempt}/{TOTAL_ATTEMPTS}  mode={mode}  aggressive={aggressive}")

            soup = fetch_page(product_link, mode=mode)
            if soup is None:
                log.warning("   Fetch failed.")
                time.sleep(3)
                continue

            data    = parse_product_page(soup, aggressive=aggressive)
            missing = missing_fields(data)

            log.info(f"   Extracted: {data}")

            # Cross-validate prices
            valid, reason = cross_validate(data)
            if not valid:
                log.warning(f"   CROSS-VALIDATE FAIL: {reason} -- retrying")
                # Clear bad price fields and retry
                data["Current Price"]  = ""
                data["Original Price"] = ""
                data["Discount"]       = ""
                time.sleep(2)
                continue

            if not missing:
                log.info(f"   All fields OK on attempt {attempt}.")
                success = True
                break

            log.warning(f"   Missing after attempt {attempt}: {missing}")
            time.sleep(2)

        # Final result
        missing = missing_fields(data)
        if missing:
            log.warning(f"   GAVE UP. Still missing: {missing}")

        if not any(data.values()):
            log.warning("   Zero data -- skipping DB update.")
            partial += 1
        else:
            ok = update_row(client, product_link, data)
            if ok and not missing:
                updated += 1
            elif ok and missing:
                partial += 1
            else:
                db_failed += 1

        time.sleep(DELAY_BETWEEN_PRODUCTS)

    log.info("\n" + "=" * 70)
    log.info(f"  Done  --  Table: {TABLE_NAME}")
    log.info(f"  Fully updated   : {updated}")
    log.info(f"  Partial/skipped : {partial}")
    log.info(f"  DB errors       : {db_failed}")
    log.info(f"  Total           : {total}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
