"""
update_earbuds.py
─────────────────────────────────────────────────────────────────────────────
Scrapes Flipkart search/listing pages via ScraperAPI and updates
price, original price, discount, rating, and review count in the
Supabase `earbuds` table.

Smart-Stop System: exits as soon as every DB product has been updated.

Environment Variables (GitHub Secrets):
  SUPABASE_URL       – e.g. https://xxxx.supabase.co
  SUPABASE_KEY       – service-role key
  SCRAPERAPI_KEY     – ScraperAPI key
  FLIPKART_CAT_URL   – Flipkart search/category URL (without &page=N)
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
SUPABASE_URL: str     = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY: str     = os.environ["SUPABASE_KEY"].strip()
SCRAPERAPI_KEY: str   = os.environ["SCRAPERAPI_KEY"].strip()
FLIPKART_CAT_URL: str = os.environ["FLIPKART_CAT_URL"].strip()

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"
MAX_PAGES       = 20
REQUEST_DELAY   = 3
REQUEST_TIMEOUT = 90


# ─────────────────────────────────────────────────────────────────────────────
# SUPABASE CLIENT
# ─────────────────────────────────────────────────────────────────────────────
def get_supabase_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Fetch DB links
# Returns dict: { clean_url_without_params : full_original_db_url }
# ─────────────────────────────────────────────────────────────────────────────
def fetch_db_links(client: Client) -> dict:
    log.info("📦 Fetching existing product links from Supabase...")
    result = client.table("earbuds").select("*").execute()
    links = {}
    for row in result.data:
        original = row.get("Product Link", "").strip()
        if original:
            clean = original.split("?")[0].rstrip("/")
            links[clean] = original
    log.info(f"   → {len(links)} product(s) found in DB.")
    return links


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — ScraperAPI fetch
# ─────────────────────────────────────────────────────────────────────────────
def scrape_page(url: str) -> BeautifulSoup | None:
    params = {
        "api_key": SCRAPERAPI_KEY,
        "url": url,
        "country_code": "in",
        "premium": "true",
    }
    full_url = f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}"
    try:
        log.info(f"🌐 Fetching: {url}")
        resp = requests.get(full_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.RequestException as exc:
        log.error(f"   ✗ Request failed: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Parse listing page
# ─────────────────────────────────────────────────────────────────────────────
def safe_text(tag, default: str = "") -> str:
    return tag.get_text(strip=True) if tag else default


def parse_price(text: str) -> str:
    return text.replace("₹", "").replace(",", "").strip()


def extract_clean_url(href: str) -> str:
    """Build full URL and strip query params — keep only /p/itmXXX part."""
    if href.startswith("/"):
        full = "https://www.flipkart.com" + href
    else:
        full = href
    return full.split("?")[0].rstrip("/")


def parse_listing_page(soup: BeautifulSoup) -> list[dict]:
    products = []

    # ── Find all product cards (Flipkart search page selectors 2024-2025) ──
    cards = (
        soup.select("div.slAVV4")        # search page grid card
        or soup.select("div.DOjaWF")     # search page alternate
        or soup.select("div.tUxRFH")     # category page
        or soup.select("div.CGtC98")     # older category
        or soup.select("div._1AtVbE")    # legacy
    )

    # Fallback — find any div containing a product link
    if not cards:
        cards = [a.parent for a in soup.select("a[href*='/p/itm']")]

    log.info(f"   → {len(cards)} card(s) detected on page.")

    for card in cards:
        # ── Product link ────────────────────────────────────────────────────
        link_tag = card.select_one("a[href*='/p/itm']")
        if not link_tag:
            continue
        href = link_tag.get("href", "")
        if not href or "/p/" not in href:
            continue
        clean_url = extract_clean_url(href)

        # ── Current price ────────────────────────────────────────────────────
        cur_price_tag = (
            card.select_one("div.Nx9bqj._4b5DiR")
            or card.select_one("div.Nx9bqj")
            or card.select_one("div._30jeq3._1_WHN1")
            or card.select_one("div._30jeq3")
        )
        current_price = parse_price(safe_text(cur_price_tag))

        # ── Original / MRP price ─────────────────────────────────────────────
        orig_price_tag = (
            card.select_one("div.yRaY8j.ZYYwLA")
            or card.select_one("div.yRaY8j")
            or card.select_one("div._3I9_wc._2p6lqe")
            or card.select_one("div._3I9_wc")
        )
        original_price = parse_price(safe_text(orig_price_tag))

        # ── Discount ─────────────────────────────────────────────────────────
        discount_tag = (
            card.select_one("div.UkUFwK span")
            or card.select_one("div._3Ay6Sb._31Dcoz span")
            or card.select_one("span._2p6lqe")
        )
        discount = safe_text(discount_tag).replace("off", "").strip()

        # ── Rating ───────────────────────────────────────────────────────────
        rating_tag = (
            card.select_one("div.XQDdHH")
            or card.select_one("div._3LWZlK")
            or card.select_one("span.Y1HWO0")
        )
        rating = safe_text(rating_tag)

        # ── Number of reviews ────────────────────────────────────────────────
        reviews_tag = (
            card.select_one("span.Wphh3N")
            or card.select_one("span._2_R_DZ")
            or card.select_one("span._13vcmD")
        )
        reviews_text = safe_text(reviews_tag)
        num_reviews = ""
        if reviews_text:
            numbers = re.findall(r"[\d,]+", reviews_text)
            num_reviews = numbers[0].replace(",", "") if numbers else ""

        products.append({
            "clean_url":      clean_url,
            "Current Price":  current_price,
            "Original Price": original_price,
            "Discount":       discount,
            "Rating":         rating,
            "Number of Reviews": num_reviews,
        })

    return products


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Update Supabase row
# ─────────────────────────────────────────────────────────────────────────────
def update_product(client: Client, db_url: str, product: dict) -> bool:
    """Update using the ORIGINAL full DB URL (with query params) as key."""
    payload = {
        "Current Price":     product["Current Price"],
        "Original Price":    product["Original Price"],
        "Discount":          product["Discount"],
        "Rating":            product["Rating"],
        "Number of Reviews": product["Number of Reviews"],
    }
    try:
        client.table("earbuds").update(payload).eq("Product Link", db_url).execute()
        log.info(f"   ✅ UPDATED  → {db_url}")
        log.info(f"      Price: {product['Current Price']}  |  "
                 f"MRP: {product['Original Price']}  |  "
                 f"Discount: {product['Discount']}  |  "
                 f"Rating: {product['Rating']}  |  "
                 f"Reviews: {product['Number of Reviews']}")
        return True
    except Exception as exc:
        log.error(f"   ✗ DB update failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — Smart-Stop pagination loop
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("═" * 70)
    log.info("  Flipkart Earbuds Price/Rating Updater  —  Starting run")
    log.info("═" * 70)

    client       = get_supabase_client()
    db_links     = fetch_db_links(client)   # {clean_url: original_db_url}

    if not db_links:
        log.warning("No products in DB. Nothing to update. Exiting.")
        return

    total_in_db    = len(db_links)
    pending        = dict(db_links)         # shrinks as we update
    updated_count  = 0
    skipped_count  = 0

    for page_num in range(1, MAX_PAGES + 1):
        page_url = f"{FLIPKART_CAT_URL}&page={page_num}"
        log.info(f"\n{'─'*60}")
        log.info(f"📄 PAGE {page_num}  |  Updated so far: {updated_count}/{total_in_db}")
        log.info(f"{'─'*60}")

        soup = scrape_page(page_url)
        if soup is None:
            log.warning(f"   Skipping page {page_num} due to fetch error.")
            time.sleep(REQUEST_DELAY)
            continue

        products_on_page = parse_listing_page(soup)

        if not products_on_page:
            log.warning(f"   No products parsed on page {page_num}. Stopping.")
            break

        for product in products_on_page:
            clean = product["clean_url"]
            if clean in pending:
                db_url  = pending[clean]
                success = update_product(client, db_url, product)
                if success:
                    del pending[clean]
                    updated_count += 1
            else:
                log.debug(f"   ⏭  SKIPPED: {clean}")
                skipped_count += 1

        # Smart-Stop
        if not pending:
            log.info("\n🎯 Smart-Stop: all DB products updated!")
            break

        log.info(f"   Remaining to update: {len(pending)}")
        time.sleep(REQUEST_DELAY)

    # Summary
    log.info("\n" + "═" * 70)
    log.info(f"  Run complete.")
    log.info(f"  ✅ Updated  : {updated_count}")
    log.info(f"  ⏭  Skipped  : {skipped_count}")
    log.info(f"  ❌ Not found: {len(pending)}")
    if pending:
        log.warning("  Products NOT updated this run:")
        for link in sorted(pending.values()):
            log.warning(f"    • {link}")
    log.info("═" * 70)


if __name__ == "__main__":
    main()
