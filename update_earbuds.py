"""
update_earbuds.py  —  Per-Product URL Mode
─────────────────────────────────────────────────────────────────────────────
Fetches each Product Link from Supabase, scrapes the individual Flipkart
product page via ScraperAPI, extracts price/rating data, and updates the row.

Environment Variables (GitHub Secrets):
  SUPABASE_URL     – https://xxxx.supabase.co
  SUPABASE_KEY     – service-role key
  SCRAPERAPI_KEY   – ScraperAPI key
"""

import os
import re
import time
import logging
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY   = os.environ["SUPABASE_KEY"].strip()
SCRAPERAPI_KEY = os.environ["SCRAPERAPI_KEY"].strip()

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"
REQUEST_DELAY   = 3
REQUEST_TIMEOUT = 90


# ─────────────────────────────────────────────────────────────────────────────
# SUPABASE
# ─────────────────────────────────────────────────────────────────────────────
def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_products(client: Client) -> list[dict]:
    """Fetch all rows from the earbuds table that have a Product Link."""
    log.info("Fetching all product rows from Supabase...")
    result = client.table("earbuds").select("*").execute()
    rows = [r for r in result.data if r.get("Product Link", "").strip()]
    log.info(f"   -> {len(rows)} product(s) found.")
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPERAPI FETCH
# ─────────────────────────────────────────────────────────────────────────────
def fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch a Flipkart product page through ScraperAPI."""
    params = {
        "api_key":      SCRAPERAPI_KEY,
        "url":          url,
        "country_code": "in",
        "premium":      "true",
    }
    full = f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}"
    try:
        resp = requests.get(full, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.RequestException as exc:
        log.error(f"   X Fetch failed: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PARSE INDIVIDUAL PRODUCT PAGE
# ─────────────────────────────────────────────────────────────────────────────
def safe(tag, default=""):
    return tag.get_text(strip=True) if tag else default


def parse_product_page(soup: BeautifulSoup) -> dict:
    """Extract price, MRP, discount, rating, and reviews from a product page."""
    data = {
        "Current Price":     "",
        "Original Price":    "",
        "Discount":          "",
        "Rating":            "",
        "Number of Reviews": "",
    }

    # Current Price
    cur = (
        soup.select_one("div.Nx9bqj.CxhGGd")
        or soup.select_one("div.Nx9bqj")
        or soup.select_one("div._30jeq3._16Jk6d")
        or soup.select_one("div._30jeq3")
    )
    data["Current Price"] = safe(cur).replace("₹", "").replace(",", "").strip()

    # Original / MRP Price
    mrp = (
        soup.select_one("div.yRaY8j")
        or soup.select_one("div._3I9_wc")
    )
    data["Original Price"] = safe(mrp).replace("₹", "").replace(",", "").strip()

    # Discount
    disc = (
        soup.select_one("div.UkUFwK span")
        or soup.select_one("div.VGWC+T span")
        or soup.select_one("span._3Ay6Sb")
    )
    data["Discount"] = safe(disc).replace("off", "").strip()

    # Rating
    rat = (
        soup.select_one("div.XQDdHH")
        or soup.select_one("div._3LWZlK")
        or soup.select_one("div.ipqd2A")
        or soup.select_one("span.Y1HWO0")
    )
    data["Rating"] = safe(rat)

    # Number of Reviews
    rev = (
        soup.select_one("span.Wphh3N")
        or soup.select_one("span._2_R_DZ")
        or soup.select_one("span._13vcmD")
    )
    rev_text = safe(rev)
    if rev_text:
        nums = re.findall(r"[\d,]+", rev_text)
        data["Number of Reviews"] = nums[0].replace(",", "") if nums else ""

    return data


# ─────────────────────────────────────────────────────────────────────────────
# UPDATE SUPABASE ROW
# ─────────────────────────────────────────────────────────────────────────────
def update_row(client: Client, product_link: str, data: dict) -> bool:
    """Update a single row matched by Product Link."""
    try:
        client.table("earbuds").update(data).eq("Product Link", product_link).execute()
        log.info(f"   [OK] UPDATED")
        log.info(f"        Price: {data['Current Price']}  |  "
                 f"MRP: {data['Original Price']}  |  "
                 f"Discount: {data['Discount']}  |  "
                 f"Rating: {data['Rating']}  |  "
                 f"Reviews: {data['Number of Reviews']}")
        return True
    except Exception as exc:
        log.error(f"   X DB update failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 70)
    log.info("  Flipkart Earbuds Updater  --  Per-Product URL Mode")
    log.info("=" * 70)

    client   = get_client()
    products = fetch_all_products(client)

    total   = len(products)
    updated = 0
    failed  = 0

    for idx, row in enumerate(products, start=1):
        product_link = row["Product Link"].strip()

        log.info(f"\n{'-'*60}")
        log.info(f"[{idx}/{total}]  {product_link[:90]}")
        log.info(f"{'-'*60}")

        soup = fetch_page(product_link)
        if soup is None:
            log.warning("   Skipping -- could not fetch page.")
            failed += 1
            time.sleep(REQUEST_DELAY)
            continue

        data = parse_product_page(soup)

        if not any(data.values()):
            log.warning("   WARNING: No data extracted -- selector mismatch. Skipping.")
            failed += 1
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
    log.info(f"  Failed  : {failed}")
    log.info(f"  Total   : {total}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()

