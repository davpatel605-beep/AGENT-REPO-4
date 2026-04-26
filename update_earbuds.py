"""
update_earbuds.py  --  Per-Product URL Mode
- Retries a product if any critical field is missing
- Tries cheap request first, uses premium+render only if needed (saves credits)
- Faster execution

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

# Critical fields that MUST be present — if missing, retry the product
CRITICAL_FIELDS = ["Current Price", "Original Price"]

# Max retries per product before giving up
MAX_RETRIES = 3

REQUEST_TIMEOUT = 60
DELAY_BETWEEN_PRODUCTS = 1   # seconds


# ── Supabase ──────────────────────────────────────────────────────────────────
def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_products(client: Client) -> list[dict]:
    log.info("Fetching all product rows from Supabase...")
    result = client.table("earbuds").select("*").execute()
    rows = [r for r in result.data if r.get("Product Link", "").strip()]
    log.info(f"   -> {len(rows)} product(s) found.")
    return rows


# ── ScraperAPI fetch — two modes ──────────────────────────────────────────────
def fetch_page(url: str, use_render: bool = False) -> BeautifulSoup | None:
    """
    use_render=False  -> cheap request (counts as 1 credit)
    use_render=True   -> premium + render (counts as 10-25 credits)
    Always try cheap first; only escalate if data is missing.
    """
    params = {
        "api_key":      SCRAPERAPI_KEY,
        "url":          url,
        "country_code": "in",
    }
    if use_render:
        params["premium"] = "true"
        params["render"]  = "true"

    full = f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}"
    mode = "RENDER+PREMIUM" if use_render else "CHEAP"
    try:
        resp = requests.get(full, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        log.info(f"   [{mode}] Status: {resp.status_code}")
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.RequestException as exc:
        log.error(f"   [{mode}] Fetch failed: {exc}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def safe(tag, default=""):
    return tag.get_text(strip=True) if tag else default


def clean_price(text: str) -> str:
    cleaned = re.sub(r"[^\d]", "", text).strip()
    # Sanity check: price between 50 and 500000
    if cleaned.isdigit() and 50 <= int(cleaned) <= 500000:
        return cleaned
    return ""


# ── Parse product page ────────────────────────────────────────────────────────
def parse_product_page(soup: BeautifulSoup) -> dict:
    data = {
        "Current Price":     "",
        "Original Price":    "",
        "Discount":          "",
        "Rating":            "",
        "Number of Reviews": "",
    }

    all_tags = soup.find_all(["div", "span"])

    # ── Current Price ─────────────────────────────────────────────────────────
    selectors_cur = [
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
        "div.Nx9bqj.CxhGGd",
        "div.Nx9bqj",
        "div._30jeq3._16Jk6d",
        "div._30jeq3",
    ]
    for sel in selectors_cur:
        tag = soup.select_one(sel)
        if tag:
            val = clean_price(safe(tag))
            if val:
                data["Current Price"] = val
                break

    # ── Original / MRP Price ──────────────────────────────────────────────────
    selectors_mrp = [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA",
        "div.yRaY8j",
        "div._3I9_wc._2p6lqe",
        "div._3I9_wc",
    ]
    for sel in selectors_mrp:
        tag = soup.select_one(sel)
        if tag:
            val = clean_price(safe(tag))
            if val:
                data["Original Price"] = val
                break

    # ── Sanity: current must be less than original ────────────────────────────
    if data["Current Price"] and data["Original Price"]:
        if int(data["Current Price"]) >= int(data["Original Price"]):
            log.warning(f"   SANITY FAIL: cur={data['Current Price']} >= orig={data['Original Price']} -- clearing")
            data["Current Price"]  = ""
            data["Original Price"] = ""

    # ── Discount ──────────────────────────────────────────────────────────────
    disc_tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if disc_tag:
        m = re.search(r"(\d+)%", safe(disc_tag))
        if m and 1 <= int(m.group(1)) <= 99:
            data["Discount"] = m.group(1) + "%"

    if not data["Discount"]:
        for tag in all_tags:
            text = safe(tag).strip()
            if len(text) > 15:
                continue
            m = re.search(r"(\d+)%\s*(off)?$", text, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 99:
                    data["Discount"] = str(val) + "%"
                    break

    # ── Rating ────────────────────────────────────────────────────────────────
    for tag in all_tags:
        text = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.\d", text):
            data["Rating"] = text
            break

    # ── Number of Reviews ─────────────────────────────────────────────────────
    rev_tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zegu")
    if rev_tag:
        nums = re.findall(r"[\d,]+", safe(rev_tag))
        if nums:
            data["Number of Reviews"] = nums[0].replace(",", "")

    if not data["Number of Reviews"]:
        for tag in all_tags:
            text = safe(tag).strip()
            m = re.search(r"([\d,]+)\s+[Rr]ating", text)
            if m:
                data["Number of Reviews"] = m.group(1).replace(",", "")
                break

    return data


def missing_fields(data: dict) -> list[str]:
    """Return list of CRITICAL_FIELDS that are empty."""
    return [f for f in CRITICAL_FIELDS if not data.get(f)]


# ── Update Supabase row ───────────────────────────────────────────────────────
def update_row(client: Client, product_link: str, data: dict) -> bool:
    try:
        client.table("earbuds").update(data).eq("Product Link", product_link).execute()
        log.info(
            f"   [OK] Price: {data['Current Price']}  |  "
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
    log.info("  Flipkart Earbuds Updater  --  Smart Retry Mode")
    log.info("=" * 70)

    client   = get_client()
    products = fetch_all_products(client)

    total      = len(products)
    updated    = 0
    failed     = 0
    gave_up    = 0

    for idx, row in enumerate(products, start=1):
        product_link = row["Product Link"].strip()

        log.info(f"\n{'-'*60}")
        log.info(f"[{idx}/{total}]  {product_link[:90]}")
        log.info(f"{'-'*60}")

        data          = {}
        attempt       = 0
        use_render    = False   # start cheap

        while attempt < MAX_RETRIES:
            attempt += 1
            log.info(f"   Attempt {attempt}/{MAX_RETRIES}  (render={use_render})")

            soup = fetch_page(product_link, use_render=use_render)
            if soup is None:
                log.warning("   Fetch failed. Escalating to render mode.")
                use_render = True
                time.sleep(2)
                continue

            data    = parse_product_page(soup)
            missing = missing_fields(data)

            if not missing:
                # All critical fields present — done
                log.info(f"   All fields found on attempt {attempt}.")
                break
            else:
                log.warning(f"   Missing fields: {missing}. Retrying with render=True...")
                use_render = True   # escalate for next attempt
                time.sleep(2)

        # After all attempts
        missing = missing_fields(data)
        if missing:
            log.warning(f"   GAVE UP after {attempt} attempts. Still missing: {missing}")
            gave_up += 1
            # Still update whatever we got (partial data is better than nothing)
            if any(data.values()):
                update_row(client, product_link, data)
        else:
            success = update_row(client, product_link, data)
            if success:
                updated += 1
            else:
                failed += 1

        time.sleep(DELAY_BETWEEN_PRODUCTS)

    log.info("\n" + "=" * 70)
    log.info(f"  Run complete.")
    log.info(f"  Fully updated : {updated}")
    log.info(f"  Partially done: {gave_up}")
    log.info(f"  DB error      : {failed}")
    log.info(f"  Total         : {total}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
