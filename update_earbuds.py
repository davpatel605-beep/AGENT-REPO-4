"""
update_earbuds.py
─────────────────────────────────────────────────────────────────────────────
Senior Data Engineer script: Scrapes Flipkart category/listing pages via
ScraperAPI and updates price, original price, discount, rating, and review
count in the Supabase `earbuds` table.

Smart-Stop System: exits as soon as every DB product in this category
has been refreshed — no wasted API credits.

Environment Variables Required (store in GitHub Secrets):
  SUPABASE_URL       – your project URL
  SUPABASE_KEY       – service-role / anon key
  SCRAPERAPI_KEY     – ScraperAPI key
  FLIPKART_CAT_URL   – base Flipkart category/listing URL
                       e.g. https://www.flipkart.com/audio/earphones/pr?sid=ckf,dkf
"""

import os
import time
import logging
import requests
from urllib.parse import urlencode, urlparse, parse_qs
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING SETUP
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
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]
SCRAPERAPI_KEY: str = os.environ["SCRAPERAPI_KEY"]
FLIPKART_CAT_URL: str = os.environ["FLIPKART_CAT_URL"]

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"
MAX_PAGES = 20          # Safety ceiling — avoids infinite loops
REQUEST_DELAY = 2       # Seconds between pages (be polite + avoid bans)
REQUEST_TIMEOUT = 60    # Seconds before giving up on a single request


# ─────────────────────────────────────────────────────────────────────────────
# SUPABASE CLIENT
# ─────────────────────────────────────────────────────────────────────────────
def get_supabase_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Fetch all existing product links from DB
# ─────────────────────────────────────────────────────────────────────────────
def fetch_db_links(client: Client) -> set[str]:
    """Return a set of clean product URLs already stored in the earbuds table."""
    log.info("📦 Fetching existing product links from Supabase...")
    result = client.table("earbuds").select("Product Link").execute()
    links = {row["Product Link"].strip() for row in result.data if row.get("Product Link")}
    log.info(f"   → {len(links)} product(s) found in DB.")
    return links


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — ScraperAPI fetch helper
# ─────────────────────────────────────────────────────────────────────────────
def scrape_page(url: str) -> BeautifulSoup | None:
    """Fetch a Flipkart page through ScraperAPI (premium + render)."""
    params = {
        "api_key": SCRAPERAPI_KEY,
        "url": url,
        "premium": "true",
        "render": "true",
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
# STEP 3 — Parse products from a listing page
# ─────────────────────────────────────────────────────────────────────────────
def clean_url(href: str) -> str:
    """Strip query parameters from a Flipkart product URL."""
    return "https://www.flipkart.com" + href.split("?")[0] if href.startswith("/") else href.split("?")[0]


def safe_text(tag, default: str = "") -> str:
    return tag.get_text(strip=True) if tag else default


def parse_price(text: str) -> str:
    """Remove ₹ sign and commas, return numeric string."""
    return text.replace("₹", "").replace(",", "").strip()


def parse_listing_page(soup: BeautifulSoup) -> list[dict]:
    """
    Extract product data from a Flipkart category/listing page.
    Returns a list of dicts with keys matching Supabase column names.

    Flipkart's markup changes often — we try multiple known selectors
    so the script stays resilient.
    """
    products = []

    # Each product card — Flipkart uses several wrapper classes
    # (CGtC98 for grid view, _1AtVbE for older layout, etc.)
    cards = (
        soup.select("div._1AtVbE")
        or soup.select("div.CGtC98")
        or soup.select("div.tUxRFH")
    )

    log.info(f"   → {len(cards)} card(s) detected on page.")

    for card in cards:
        # ── Product link ────────────────────────────────────────────────────
        link_tag = card.select_one("a._1fQZEK, a.IRpwTa, a.s1Q9rs, a.WKTcLC, a[href*='/p/']")
        if not link_tag:
            continue
        href = link_tag.get("href", "")
        if not href or "/p/" not in href:
            continue
        product_url = clean_url(href)

        # ── Current price ────────────────────────────────────────────────────
        cur_price_tag = (
            card.select_one("div._30jeq3._1_WHN1")
            or card.select_one("div.Nx9bqj._4b5DiR")
            or card.select_one("div.Nx9bqj")
        )
        current_price = parse_price(safe_text(cur_price_tag))

        # ── Original / MRP price ─────────────────────────────────────────────
        orig_price_tag = (
            card.select_one("div._3I9_wc._2p6lqe")
            or card.select_one("div.yRaY8j.ZYYwLA")
            or card.select_one("div.yRaY8j")
        )
        original_price = parse_price(safe_text(orig_price_tag))

        # ── Discount ─────────────────────────────────────────────────────────
        discount_tag = (
            card.select_one("div._3Ay6Sb._31Dcoz span")
            or card.select_one("div.UkUFwK span")
            or card.select_one("span._2Tpdn3")
        )
        discount = safe_text(discount_tag).replace("off", "").strip()

        # ── Rating ───────────────────────────────────────────────────────────
        rating_tag = (
            card.select_one("div._3LWZlK")
            or card.select_one("div.XQDdHH")
            or card.select_one("span.Y1HWO0")
        )
        rating = safe_text(rating_tag)

        # ── Number of reviews ────────────────────────────────────────────────
        reviews_tag = (
            card.select_one("span._2_R_DZ")
            or card.select_one("span.Wphh3N")
            or card.select_one("span._13vcmD")
        )
        reviews_text = safe_text(reviews_tag)
        # e.g. "(1,23,456 Ratings & 9,876 Reviews)" → extract first number block
        num_reviews = ""
        if reviews_text:
            import re
            numbers = re.findall(r"[\d,]+", reviews_text)
            num_reviews = numbers[0].replace(",", "") if numbers else ""

        products.append({
            "Product Link": product_url,
            "Current Price": current_price,
            "Original Price": original_price,
            "Discount": discount,
            "Rating": rating,
            "Number of Reviews": num_reviews,
        })

    return products


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Update a single row in Supabase
# ─────────────────────────────────────────────────────────────────────────────
def update_product(client: Client, product: dict) -> bool:
    """Upsert price/rating data for one product. Returns True on success."""
    url = product["Product Link"]
    payload = {
        "Current Price": product["Current Price"],
        "Original Price": product["Original Price"],
        "Discount": product["Discount"],
        "Rating": product["Rating"],
        "Number of Reviews": product["Number of Reviews"],
    }
    try:
        client.table("earbuds").update(payload).eq("Product Link", url).execute()
        log.info(f"   ✅ UPDATED  → {url}")
        log.info(f"              Price: {product['Current Price']}  |  "
                 f"MRP: {product['Original Price']}  |  "
                 f"Discount: {product['Discount']}  |  "
                 f"Rating: {product['Rating']}  |  "
                 f"Reviews: {product['Number of Reviews']}")
        return True
    except Exception as exc:
        log.error(f"   ✗ DB update failed for {url}: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR — Smart-Stop pagination loop
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("═" * 70)
    log.info("  Flipkart Earbuds Price/Rating Updater  —  Starting run")
    log.info("═" * 70)

    client = get_supabase_client()
    db_links: set[str] = fetch_db_links(client)

    if not db_links:
        log.warning("No products in DB. Nothing to update. Exiting.")
        return

    total_in_db = len(db_links)
    pending_links = set(db_links)          # shrinks as we update
    updated_count = 0
    skipped_count = 0

    for page_num in range(1, MAX_PAGES + 1):
        # ── Build paginated URL ──────────────────────────────────────────────
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
            log.warning(f"   No products parsed on page {page_num}. "
                        "Possibly last page or selector mismatch — stopping.")
            break

        for product in products_on_page:
            url = product["Product Link"]

            if url in pending_links:
                success = update_product(client, product)
                if success:
                    pending_links.discard(url)
                    updated_count += 1
            else:
                log.debug(f"   ⏭  SKIPPED (not in DB or already updated): {url}")
                skipped_count += 1

        # ── Smart-Stop check ─────────────────────────────────────────────────
        if not pending_links:
            log.info("\n🎯 Smart-Stop triggered: all DB products updated!")
            break

        log.info(f"   Remaining to update: {len(pending_links)}")
        time.sleep(REQUEST_DELAY)

    # ── Final summary ────────────────────────────────────────────────────────
    log.info("\n" + "═" * 70)
    log.info(f"  Run complete.")
    log.info(f"  ✅ Updated  : {updated_count}")
    log.info(f"  ⏭  Skipped  : {skipped_count}")
    log.info(f"  ❌ Not found: {len(pending_links)}")
    if pending_links:
        log.warning("  Products NOT found/updated this run:")
        for link in sorted(pending_links):
            log.warning(f"    • {link}")
    log.info("═" * 70)


if __name__ == "__main__":
    main()
