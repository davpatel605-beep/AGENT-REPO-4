"""
update_earbuds.py  --  Per-Product URL Mode
Fetches each Product Link from Supabase, scrapes the individual Flipkart
product page via ScraperAPI, and updates price/rating data.

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

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"
REQUEST_DELAY   = 2
REQUEST_TIMEOUT = 90
MAX_RETRIES     = 2   # retry once if page returns empty data


# ── Supabase ──────────────────────────────────────────────────────────────────
def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_products(client: Client) -> list[dict]:
    log.info("Fetching all product rows from Supabase...")
    result = client.table("earbuds").select("*").execute()
    rows = [r for r in result.data if r.get("Product Link", "").strip()]
    log.info(f"   -> {len(rows)} product(s) found.")
    return rows


# ── ScraperAPI fetch ──────────────────────────────────────────────────────────
def fetch_page(url: str) -> BeautifulSoup | None:
    params = {
        "api_key":      SCRAPERAPI_KEY,
        "url":          url,
        "country_code": "in",
        "premium":      "true",
        "render":       "true",
    }
    full = f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}"
    try:
        resp = requests.get(full, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.RequestException as exc:
        log.error(f"   X Fetch failed: {exc}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def safe(tag, default=""):
    return tag.get_text(strip=True) if tag else default


def clean_price(text: str) -> str:
    """Remove Rs symbol, commas, spaces. Return digits only."""
    return re.sub(r"[^\d]", "", text).strip()


def find_main_product_container(soup: BeautifulSoup):
    """
    Find the main product info container (right side panel on Flipkart).
    This prevents picking up data from recommended/related product widgets.
    We identify it by the presence of 'Add to cart' or 'Buy now' button nearby.
    """
    # Strategy: find the div that contains BOTH a price AND an Add to cart button
    # Flipkart product page right panel usually has class like DOjaWF, _3qQ9m1 etc.
    candidates = [
        soup.select_one("div._3qQ9m1"),       # product right panel
        soup.select_one("div.DOjaWF"),
        soup.select_one("div._2kHMtA"),
        soup.select_one("div.F8fM3C"),
        soup.select_one("div._2B_Rop"),
    ]
    for c in candidates:
        if c:
            return c

    # Fallback: find the section containing "Add to cart" text
    for tag in soup.find_all(["div", "section"]):
        text = tag.get_text()
        if "Add to cart" in text and "₹" in text:
            # Make sure it's not the entire body
            if len(text) < 5000:
                return tag

    return soup   # last resort: use full page (less accurate)


# ── Parse product page ────────────────────────────────────────────────────────
def parse_product_page(soup: BeautifulSoup) -> dict:
    data = {
        "Current Price":     "",
        "Original Price":    "",
        "Discount":          "",
        "Rating":            "",
        "Number of Reviews": "",
    }

    # Always search full page for rating/reviews (these are unique enough)
    # But use main container for price/discount to avoid wrong product data

    main = find_main_product_container(soup)

    # ── Current Price ─────────────────────────────────────────────────────────
    # Look for price in main container first
    cur = (
        main.select_one("div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0")
        or main.select_one("div.Nx9bqj.CxhGGd")
        or main.select_one("div.Nx9bqj")
        or main.select_one("div._30jeq3._16Jk6d")
        or main.select_one("div._30jeq3")
    )
    if cur:
        price_text = clean_price(safe(cur))
        # Sanity check: price should be between 100 and 200000
        if price_text.isdigit() and 100 <= int(price_text) <= 200000:
            data["Current Price"] = price_text
        else:
            # Try to find price near "Buy at" text
            buy_tag = soup.find(string=re.compile(r"Buy at", re.IGNORECASE))
            if buy_tag:
                parent = buy_tag.parent
                nums = re.findall(r"[\d,]+", safe(parent))
                for n in nums:
                    val = n.replace(",", "")
                    if val.isdigit() and 100 <= int(val) <= 200000:
                        data["Current Price"] = val
                        break

    # ── Original / MRP ────────────────────────────────────────────────────────
    mrp = (
        main.select_one("div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu")
        or main.select_one("div.yRaY8j")
        or main.select_one("div._3I9_wc")
    )
    if mrp:
        mrp_text = clean_price(safe(mrp))
        if mrp_text.isdigit() and 100 <= int(mrp_text) <= 200000:
            data["Original Price"] = mrp_text

    # ── Discount ──────────────────────────────────────────────────────────────
    # Search in main container AND full page for discount
    for search_area in [main, soup]:
        disc_tag = search_area.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
        if disc_tag:
            m = re.search(r"(\d+)%", safe(disc_tag))
            if m:
                data["Discount"] = m.group(1) + "%"
                break

    # Fallback: find any standalone "X% off" or "X%" near price
    if not data["Discount"]:
        for tag in soup.find_all(["div", "span"]):
            text = safe(tag).strip()
            # Must be short — avoid matching long paragraphs
            if len(text) > 20:
                continue
            m = re.search(r"(\d+)%\s*(off)?", text, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 99:   # valid discount range
                    data["Discount"] = str(val) + "%"
                    break

    # ── Rating ────────────────────────────────────────────────────────────────
    # Rating is a unique decimal like "4.1" on the page
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        # Exact match: single digit optionally followed by .digit
        if re.fullmatch(r"[1-5]\.\d", text):
            data["Rating"] = text
            break
    # Fallback: whole number rating like "4"
    if not data["Rating"]:
        for tag in soup.find_all(["div", "span"]):
            text = safe(tag).strip()
            if re.fullmatch(r"[1-5]", text):
                classes = " ".join(tag.get("class", []))
                # Avoid matching prices or other single digits
                if any(k in classes for k in ["rating", "Rating", "XQDdHH", "_3LWZlK", "ipqd2A", "Y1HWO0"]):
                    data["Rating"] = text
                    break

    # ── Number of Reviews ─────────────────────────────────────────────────────
    # Pattern: "based on 265 ratings" or "1,821 Ratings"
    rev_tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zegu")
    if rev_tag:
        nums = re.findall(r"[\d,]+", safe(rev_tag))
        if nums:
            data["Number of Reviews"] = nums[0].replace(",", "")

    if not data["Number of Reviews"]:
        # Search for "X Ratings" pattern anywhere on page
        for tag in soup.find_all(["div", "span"]):
            text = safe(tag).strip()
            m = re.search(r"([\d,]+)\s+[Rr]ating", text)
            if m:
                data["Number of Reviews"] = m.group(1).replace(",", "")
                break

    # ── Sanity check: if current > original, something is wrong ───────────────
    if data["Current Price"] and data["Original Price"]:
        cur_val = int(data["Current Price"])
        orig_val = int(data["Original Price"])
        if cur_val > orig_val:
            log.warning(f"   SANITY FAIL: Current ({cur_val}) > Original ({orig_val}) -- clearing prices")
            data["Current Price"]  = ""
            data["Original Price"] = ""
            data["Discount"]       = ""

    return data


# ── Update Supabase row ───────────────────────────────────────────────────────
def update_row(client: Client, product_link: str, data: dict) -> bool:
    try:
        client.table("earbuds").update(data).eq("Product Link", product_link).execute()
        log.info(f"   [OK] UPDATED")
        log.info(
            f"        Price: {data['Current Price']}  |  "
            f"MRP: {data['Original Price']}  |  "
            f"Discount: {data['Discount']}  |  "
            f"Rating: {data['Rating']}  |  "
            f"Reviews: {data['Number of Reviews']}"
        )
        return True
    except Exception as exc:
        log.error(f"   X DB update failed: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 70)
    log.info("  Flipkart Earbuds Updater  --  Per-Product URL Mode")
    log.info("=" * 70)

    client   = get_client()
    products = fetch_all_products(client)

    total   = len(products)
    updated = 0
    failed  = 0
    skipped = 0

    for idx, row in enumerate(products, start=1):
        product_link = row["Product Link"].strip()

        log.info(f"\n{'-'*60}")
        log.info(f"[{idx}/{total}]  {product_link[:90]}")
        log.info(f"{'-'*60}")

        soup = None
        for attempt in range(1, MAX_RETRIES + 1):
            soup = fetch_page(product_link)
            if soup:
                break
            log.warning(f"   Attempt {attempt} failed. Retrying...")
            time.sleep(5)

        if soup is None:
            log.warning("   Skipping -- all fetch attempts failed.")
            skipped += 1
            time.sleep(REQUEST_DELAY)
            continue

        data = parse_product_page(soup)
        log.info(f"   Extracted -> {data}")

        # Skip only if ALL price fields are empty (rating alone is not enough)
        if not data["Current Price"] and not data["Original Price"]:
            log.warning("   WARNING: No price data extracted -- skipping update.")
            skipped += 1
            time.sleep(REQUEST_DELAY)
            continue

        success = update_row(client, product_link, data)
        if success:
            updated += 1
        else:
            failed += 1

        time.sleep(REQUEST_DELAY)

    log.info("\n" + "=" * 70)
    log.info(f"  Run complete.")
    log.info(f"  Updated : {updated}")
    log.info(f"  Skipped : {skipped}")
    log.info(f"  Failed  : {failed}")
    log.info(f"  Total   : {total}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()

