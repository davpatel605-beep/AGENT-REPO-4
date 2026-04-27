"""
update_gaming_cpu.py  --  Per-Product URL Mode
Table: gaming_cpu
Columns: Rating, Number of Reviews, Current Price, Original Price, Discount, Product Link

Scraping logic based on Flipkart visual patterns:
  - Rating    : number 1-5 (with .digit) near star symbol
  - Reviews   : number near rating (may have 'k' suffix = thousands)
  - Discount  : down-arrow symbol followed by X% 
  - Orig Price: strikethrough number (light color, line through it)
  - Curr Price: bold large price near Buy button

Environment Variables (GitHub Secrets):
  SUPABASE_URL   -- https://xxxx.supabase.co
  SUPABASE_KEY   -- service-role key
  SCRAPERAPI_KEY -- ScraperAPI key
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

SCRAPERAPI_ENDPOINT  = "https://api.scraperapi.com/"
TABLE_NAME           = "gaming cpu"
CRITICAL_FIELDS      = ["Current Price", "Original Price"]
MAX_RETRIES          = 3
REQUEST_TIMEOUT      = 60
DELAY_BETWEEN_PRODUCTS = 1


# ── Supabase ──────────────────────────────────────────────────────────────────
def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_products(client: Client) -> list[dict]:
    log.info(f"Fetching all rows from '{TABLE_NAME}' table...")
    result = client.table(TABLE_NAME).select("*").execute()
    rows = [r for r in result.data if r.get("Product Link", "").strip()]
    log.info(f"   -> {len(rows)} product(s) found.")
    return rows


# ── ScraperAPI ────────────────────────────────────────────────────────────────
def fetch_page(url: str, use_render: bool = False) -> BeautifulSoup | None:
    params = {
        "api_key":      SCRAPERAPI_KEY,
        "url":          url,
        "country_code": "in",
    }
    if use_render:
        params["premium"] = "true"
        params["render"]  = "true"

    full = f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}"
    mode = "RENDER" if use_render else "CHEAP"
    try:
        resp = requests.get(full, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        log.info(f"   [{mode}] HTTP {resp.status_code}")
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.RequestException as exc:
        log.error(f"   [{mode}] Failed: {exc}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def safe(tag, default=""):
    return tag.get_text(strip=True) if tag else default


def parse_k_number(text: str) -> str:
    """
    Convert '56k', '56,770', '1.2k' -> plain integer string.
    """
    text = text.strip().replace(",", "")
    m = re.match(r"([\d\.]+)\s*[kK]", text)
    if m:
        return str(int(float(m.group(1)) * 1000))
    m = re.match(r"(\d+)", text)
    if m:
        return m.group(1)
    return ""


def valid_price(val: str) -> bool:
    return val.isdigit() and 100 <= int(val) <= 1000000


# ── Main parser ───────────────────────────────────────────────────────────────
def parse_product_page(soup: BeautifulSoup) -> dict:
    data = {
        "Current Price":     "",
        "Original Price":    "",
        "Discount":          "",
        "Rating":            "",
        "Number of Reviews": "",
    }

    all_tags = soup.find_all(["div", "span"])

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. CURRENT PRICE
    # Pattern: bold large ₹ number near "Buy at" or "Add to cart"
    # ═══════════════════════════════════════════════════════════════════════════
    cur_selectors = [
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
        "div.Nx9bqj.CxhGGd",
        "div.Nx9bqj",
        "div._30jeq3._16Jk6d",
        "div._30jeq3",
    ]
    for sel in cur_selectors:
        tag = soup.select_one(sel)
        if tag:
            val = re.sub(r"[^\d]", "", safe(tag))
            if val and valid_price(val):
                data["Current Price"] = val
                break

    # Fallback: find price near "Buy at ₹XXX" text
    if not data["Current Price"]:
        buy_match = soup.find(string=re.compile(r"Buy at\s*₹", re.IGNORECASE))
        if buy_match:
            nums = re.findall(r"[\d,]+", buy_match)
            for n in nums:
                val = n.replace(",", "")
                if valid_price(val):
                    data["Current Price"] = val
                    break

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. ORIGINAL PRICE (strikethrough = MRP)
    # Flipkart marks strikethrough price with specific classes OR inline style
    # ═══════════════════════════════════════════════════════════════════════════
    mrp_selectors = [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA",
        "div.yRaY8j",
        "div._3I9_wc._2p6lqe",
        "div._3I9_wc",
    ]
    for sel in mrp_selectors:
        tag = soup.select_one(sel)
        if tag:
            val = re.sub(r"[^\d]", "", safe(tag))
            if val and valid_price(val):
                data["Original Price"] = val
                break

    # Fallback: look for tags with strikethrough style
    if not data["Original Price"]:
        for tag in all_tags:
            style = tag.get("style", "")
            classes = " ".join(tag.get("class", []))
            text = safe(tag).strip()
            is_strikethrough = (
                "line-through" in style
                or "strike" in classes.lower()
                or "linethrough" in classes.lower()
            )
            if is_strikethrough:
                val = re.sub(r"[^\d]", "", text)
                if val and valid_price(val):
                    data["Original Price"] = val
                    break

    # Sanity: current must be less than original
    if data["Current Price"] and data["Original Price"]:
        if int(data["Current Price"]) >= int(data["Original Price"]):
            log.warning(
                f"   SANITY FAIL: cur={data['Current Price']} >= orig={data['Original Price']} -- clearing"
            )
            data["Current Price"]  = ""
            data["Original Price"] = ""

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. DISCOUNT
    # Pattern: down-arrow symbol (↓) + number + % e.g. "↓77%" or "77% off"
    # ═══════════════════════════════════════════════════════════════════════════
    disc_sel = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if disc_sel:
        m = re.search(r"(\d+)%", safe(disc_sel))
        if m and 1 <= int(m.group(1)) <= 99:
            data["Discount"] = m.group(1) + "%"

    if not data["Discount"]:
        # Look for down-arrow unicode + % pattern anywhere
        full_text = soup.get_text()
        # ↓77% or ↘77% patterns
        m = re.search(r"[↓↘▼]\s*(\d+)\s*%", full_text)
        if m and 1 <= int(m.group(1)) <= 99:
            data["Discount"] = m.group(1) + "%"

    if not data["Discount"]:
        for tag in all_tags:
            text = safe(tag).strip()
            if len(text) > 20:
                continue
            m = re.search(r"(\d+)%\s*(off)?$", text, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 99:
                    data["Discount"] = str(val) + "%"
                    break

    # Auto-calculate discount if still missing but we have both prices
    if not data["Discount"] and data["Current Price"] and data["Original Price"]:
        cur  = int(data["Current Price"])
        orig = int(data["Original Price"])
        if orig > cur:
            disc = round((orig - cur) / orig * 100)
            data["Discount"] = str(disc) + "%"
            log.info(f"   Discount auto-calculated: {data['Discount']}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. RATING
    # Pattern: number like "4.1" near a star ★ symbol
    # ═══════════════════════════════════════════════════════════════════════════
    for tag in all_tags:
        text = safe(tag).strip()
        # Exact decimal rating: "4.1" "3.9" "5.0"
        if re.fullmatch(r"[1-5]\.\d", text):
            data["Rating"] = text
            break

    # Fallback: find "4.1 ★" or "★ 4.1" pattern in text
    if not data["Rating"]:
        full_text = soup.get_text()
        m = re.search(r"([1-5]\.\d)\s*[★✩⭐]|[★✩⭐]\s*([1-5]\.\d)", full_text)
        if m:
            data["Rating"] = m.group(1) or m.group(2)

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. NUMBER OF REVIEWS
    # Pattern: number (may have k suffix) right after rating, like "| 56,770"
    # ═══════════════════════════════════════════════════════════════════════════
    rev_sel = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zegu")
    if rev_sel:
        text = safe(rev_sel)
        nums = re.findall(r"[\d,]+", text)
        if nums:
            data["Number of Reviews"] = nums[0].replace(",", "")

    # Fallback: look for "X Ratings" or "X ratings" pattern
    if not data["Number of Reviews"]:
        for tag in all_tags:
            text = safe(tag).strip()
            m = re.search(r"([\d,]+[\dk]*)\s+[Rr]ating", text)
            if m:
                data["Number of Reviews"] = parse_k_number(m.group(1))
                break

    # Fallback: look for "X Reviews"
    if not data["Number of Reviews"]:
        for tag in all_tags:
            text = safe(tag).strip()
            m = re.search(r"([\d,]+[\dk]*)\s+[Rr]eview", text)
            if m:
                data["Number of Reviews"] = parse_k_number(m.group(1))
                break

    # Fallback: "| 56,770" pattern near rating
    if not data["Number of Reviews"] and data["Rating"]:
        full_text = soup.get_text()
        pattern = re.escape(data["Rating"]) + r"\s*[★✩⭐|,\s]+([\d,]+[kK]?)"
        m = re.search(pattern, full_text)
        if m:
            data["Number of Reviews"] = parse_k_number(m.group(1))

    return data


def missing_fields(data: dict) -> list[str]:
    return [f for f in CRITICAL_FIELDS if not data.get(f)]


# ── Update Supabase ───────────────────────────────────────────────────────────
def update_row(client: Client, product_link: str, data: dict) -> bool:
    try:
        client.table(TABLE_NAME).update(data).eq("Product Link", product_link).execute()
        log.info(
            f"   [OK] Price: {data['Current Price']}  |  "
            f"MRP: {data['Original Price']}  |  "
            f"Disc: {data['Discount']}  |  "
            f"Rating: {data['Rating']}  |  "
            f"Reviews: {data['Number of Reviews']}"
        )
        return True
    except Exception as exc:
        log.error(f"   X DB error: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 70)
    log.info(f"  Flipkart Updater  --  Table: {TABLE_NAME}")
    log.info("=" * 70)

    client   = get_client()
    products = fetch_all_products(client)

    total      = len(products)
    updated    = 0
    partial    = 0
    db_failed  = 0

    for idx, row in enumerate(products, start=1):
        product_link = row["Product Link"].strip()

        log.info(f"\n{'-'*60}")
        log.info(f"[{idx}/{total}]  {product_link[:90]}")
        log.info(f"{'-'*60}")

        data       = {}
        use_render = False

        for attempt in range(1, MAX_RETRIES + 1):
            log.info(f"   Attempt {attempt}/{MAX_RETRIES}  render={use_render}")

            soup = fetch_page(product_link, use_render=use_render)
            if soup is None:
                log.warning("   Fetch failed. Switching to render mode.")
                use_render = True
                time.sleep(3)
                continue

            data    = parse_product_page(soup)
            missing = missing_fields(data)

            if not missing:
                log.info(f"   All critical fields found on attempt {attempt}.")
                break

            log.warning(f"   Missing: {missing}. Retrying with render=True...")
            use_render = True
            time.sleep(2)

        # Done with retries
        missing = missing_fields(data)
        log.info(f"   Final data: {data}")

        if not any(data.values()):
            log.warning("   No data at all -- skipping DB update.")
            partial += 1
        else:
            if missing:
                log.warning(f"   Partial update (missing: {missing})")
                partial += 1
            success = update_row(client, product_link, data)
            if success and not missing:
                updated += 1
            elif not success:
                db_failed += 1

        time.sleep(DELAY_BETWEEN_PRODUCTS)

    log.info("\n" + "=" * 70)
    log.info(f"  Run complete  --  Table: {TABLE_NAME}")
    log.info(f"  Fully updated   : {updated}")
    log.info(f"  Partial/skipped : {partial}")
    log.info(f"  DB errors       : {db_failed}")
    log.info(f"  Total           : {total}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
