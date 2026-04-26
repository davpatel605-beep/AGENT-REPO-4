"""
update_earbuds.py  --  Per-Product URL Mode
Fetches each Product Link from Supabase, scrapes the Flipkart product page
via ScraperAPI, extracts price/rating, and updates the row.

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
REQUEST_DELAY   = 3
REQUEST_TIMEOUT = 90


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


# ── Parse product page ────────────────────────────────────────────────────────
def safe(tag, default=""):
    return tag.get_text(strip=True) if tag else default


def clean_price(text: str) -> str:
    return text.replace("₹", "").replace(",", "").strip()


def parse_product_page(soup: BeautifulSoup) -> dict:
    data = {
        "Current Price":     "",
        "Original Price":    "",
        "Discount":          "",
        "Rating":            "",
        "Number of Reviews": "",
    }

    # ── Current Price ─────────────────────────────────────────────────────────
    # First match = main product price (not related products)
    cur = soup.select_one(
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0"
    )
    if cur:
        data["Current Price"] = clean_price(safe(cur))

    # ── Original / MRP Price ──────────────────────────────────────────────────
    mrp = soup.select_one(
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu"
    )
    if mrp:
        data["Original Price"] = clean_price(safe(mrp))

    # ── Discount ──────────────────────────────────────────────────────────────
    # Format: "78%3,199₹699" -- extract leading number before %
    disc_tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if disc_tag:
        disc_text = safe(disc_tag)
        match = re.match(r"(\d+)%", disc_text)
        if match:
            data["Discount"] = match.group(1) + "%"

    # ── Rating ────────────────────────────────────────────────────────────────
    # Try multiple selectors for rating number
    for sel in [
        "div.v1zwn21l.v1zwn2b._1psv1zeb9._1psv1ze0",
        "div.XQDdHH",
        "div._3LWZlK",
        "div.ipqd2A",
        "span.Y1HWO0",
    ]:
        tag = soup.select_one(sel)
        if tag:
            text = safe(tag)
            # Rating should be like "4.3" or "4"
            if re.match(r"^\d(\.\d)?$", text):
                data["Rating"] = text
                break

    # ── Number of Reviews ─────────────────────────────────────────────────────
    # "based on 265 ratings byVerified Buyers" -- extract number
    rev_tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zegu")
    if rev_tag:
        rev_text = safe(rev_tag)
        nums = re.findall(r"[\d,]+", rev_text)
        if nums:
            data["Number of Reviews"] = nums[0].replace(",", "")

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
        log.info(f"   Extracted -> {data}")

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
