"""
master_updater.py — Universal Flipkart Price Updater
=====================================================
Handles all 11 tables in one run.
- Auto-rotates ScraperAPI keys when limit is hit
- Math-based fallback for missing price fields
- ₹ symbol added to price fields
- Reviews: ★ | number pattern
- GitHub Actions compatible (no local machine needed)

Environment Variables (GitHub Secrets):
  SUPABASE_URL, SUPABASE_KEY
  SCRAPERAPI_KEY, SCRAPERAPI_KEY_2 ... SCRAPERAPI_KEY_6
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

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_KEY"].strip()

# ── ScraperAPI key rotation ───────────────────────────────────────────────────
SCRAPERAPI_KEYS = []
for i in ["", "_2", "_3", "_4", "_5", "_6"]:
    k = os.environ.get(f"SCRAPERAPI_KEY{i}", "").strip()
    if k:
        SCRAPERAPI_KEYS.append(k)

current_key_index = 0

def get_active_key() -> str:
    return SCRAPERAPI_KEYS[current_key_index] if SCRAPERAPI_KEYS else ""

def rotate_key():
    global current_key_index
    if current_key_index < len(SCRAPERAPI_KEYS) - 1:
        current_key_index += 1
        log.warning(f"   [KEY ROTATE] Switched to key #{current_key_index + 1}")
        return True
    log.error("   [KEY ROTATE] All API keys exhausted!")
    return False

SCRAPERAPI_ENDPOINT    = "https://api.scraperapi.com/"
REQUEST_TIMEOUT        = 90
DELAY_BETWEEN_PRODUCTS = 1


# ══════════════════════════════════════════════════════════════════════════════
# TABLE CONFIG
# Each table defines:
#   - name         : Supabase table name
#   - link_col     : column that holds Product URL
#   - columns      : mapping of semantic field -> actual DB column name
#   - combined_col : if rating+reviews are in one column (format: "4.1 ★ | 239")
# ══════════════════════════════════════════════════════════════════════════════
TABLES = [
    {
        "name":     "gaming cpu",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "Current Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Number of Reviews",
        },
    },
    {
        "name":     "gaming pc",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "price",
            "original_price": "Original Price-2",
            "discount":       "Discount-2",
            "rating":         "Product Rating",
            "reviews":        "product review",
        },
    },
    {
        "name":     "induction",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "Price",
            "original_price": "Discount Price",
            "discount":       "Discount percentage",
            "rating":         "Rating",
            "reviews":        "Number of reviews",
        },
    },
    {
        "name":     "iphone",
        "link_col": "Product URL",
        "columns": {
            "current_price":  "Price",
            "original_price": "Discounted Price",
            "discount":       "Discount Percentage",
            "rating":         "Product Rating",
            "reviews":        "Number of Reviews",
            "extra_reviews":  "Number of Rating",   # iphone has two review cols
        },
    },
    {
        "name":     "keybord",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Number of Reviews",
        },
    },
    {
        "name":     "laptop",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "combined":       "Rating and Reviews",  # "4.1 ★ | 239"
        },
    },
    {
        "name":     "monitar",
        "link_col": "Product URL",
        "columns": {
            "current_price":  "Current Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Number of Reviews",
        },
    },
    {
        "name":     "mouse",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "Current Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Number of Reviews",
        },
    },
    {
        "name":     "smart phone",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "combined":       "Rating and Reviews",  # "4.1 ★ | 239"
        },
    },
    {
        "name":     "smart+tv",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "combined":       "Ratings and Reviews",  # "4.1 ★ | 239"
        },
    },
    {
        "name":     "smartwatch",
        "link_col": "Product Link",
        "columns": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Review",
        },
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPER
# ══════════════════════════════════════════════════════════════════════════════
def fetch_page(url: str, attempt: int) -> BeautifulSoup | None:
    key  = get_active_key()
    if not key:
        log.error("No API key available.")
        return None

    params = {"api_key": key, "url": url, "country_code": "in"}
    if attempt >= 3:
        params["premium"] = "true"
        params["render"]  = "true"

    mode = "RENDER" if attempt >= 3 else "CHEAP"
    try:
        resp = requests.get(
            f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}",
            timeout=REQUEST_TIMEOUT,
        )
        # 403/401 = key exhausted → rotate
        if resp.status_code in (401, 403):
            log.warning(f"   [{mode}] Key limit hit (HTTP {resp.status_code}). Rotating key...")
            if rotate_key():
                return fetch_page(url, attempt)
            return None
        resp.raise_for_status()
        log.info(f"   [{mode}] HTTP {resp.status_code}  bytes={len(resp.text)}")
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        log.error(f"   [{mode}] Failed: {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def safe(tag, d=""):
    return tag.get_text(strip=True) if tag else d

def to_num(text: str) -> str:
    return re.sub(r"[^\d]", "", str(text)).strip()

def valid_price(val: str) -> bool:
    return val.isdigit() and 100 <= int(val) <= 5000000

def parse_k(text: str) -> str:
    """56k->56000, 1.2k->1200, 56,770->56770"""
    t = text.strip().replace(",", "")
    m = re.match(r"([\d.]+)[kK]", t)
    if m:
        return str(int(float(m.group(1)) * 1000))
    m = re.match(r"(\d+)", t)
    return m.group(1) if m else ""

def fmt_price(val: str) -> str:
    """Add ₹ symbol: '13902' -> '₹13,902'"""
    if not val or not val.isdigit():
        return val
    n = int(val)
    # Indian number format
    s = str(n)
    if len(s) <= 3:
        return f"₹{s}"
    # Last 3 digits, then groups of 2
    result = s[-3:]
    s = s[:-3]
    while s:
        result = s[-2:] + "," + result
        s = s[:-2]
    return f"₹{result.lstrip(',')}"

def fmt_discount(val: str) -> str:
    """Ensure discount has % symbol"""
    val = val.replace("%", "").strip()
    if val.isdigit() and 1 <= int(val) <= 99:
        return val + "%"
    return val + "%" if val else ""


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def extract_current_price(soup: BeautifulSoup, full_text: str) -> str:
    for sel in [
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
        "div.Nx9bqj.CxhGGd", "div.Nx9bqj",
        "div._30jeq3._16Jk6d", "div._30jeq3",
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
            if not parent:
                break
            prices = re.findall(r"[₹Rs\.]+\s*([\d,]+)", parent.get_text())
            valid_list = sorted([
                int(p.replace(",", "")) for p in prices
                if valid_price(p.replace(",", ""))
            ])
            if valid_list:
                return str(valid_list[0])
            parent = parent.find_parent("div")
    return ""


def extract_original_price(soup: BeautifulSoup, full_text: str,
                           current_price: str, discount: str) -> str:
    def is_valid(val: str) -> bool:
        if not val or not valid_price(val):
            return False
        if current_price and current_price.isdigit():
            return int(val) > int(current_price)
        return True

    # ── MATH FIRST: calculate expected MRP from current + discount ────────────
    if current_price and current_price.isdigit() and discount:
        disc_clean = discount.replace("%", "").strip()
        if disc_clean.isdigit():
            disc_val     = int(disc_clean)
            cur_val      = int(current_price)
            if 1 <= disc_val <= 99 and cur_val > 0:
                expected_mrp = cur_val / (1 - disc_val / 100)
                all_numbers  = re.findall(r"[\d,]{3,}", full_text)
                best_match   = ""
                best_diff    = float("inf")
                for n in all_numbers:
                    v = n.replace(",", "")
                    if not v.isdigit() or not valid_price(v):
                        continue
                    if int(v) <= cur_val:
                        continue
                    pct_diff = abs(int(v) - expected_mrp) / expected_mrp
                    if pct_diff < best_diff and pct_diff <= 0.20:
                        best_diff  = pct_diff
                        best_match = v
                if best_match:
                    log.info(f"   [MATH] orig={best_match}  diff={best_diff:.1%}")
                    return best_match

    # ── JSON-LD ───────────────────────────────────────────────────────────────
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

    # ── <s> strikethrough tag ─────────────────────────────────────────────────
    for s_tag in soup.find_all("s"):
        val = to_num(safe(s_tag))
        if is_valid(val):
            log.info(f"   [<s>] orig={val}")
            return val

    # ── CSS selectors ─────────────────────────────────────────────────────────
    for sel in [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA", "div.yRaY8j",
        "div._3I9_wc._2p6lqe", "div._3I9_wc",
    ]:
        tag = soup.select_one(sel)
        if tag:
            val = to_num(safe(tag))
            if is_valid(val):
                return val

    # ── line-through style ────────────────────────────────────────────────────
    for tag in soup.find_all(True):
        style = tag.get("style", "")
        if "line-through" in style:
            val = to_num(safe(tag))
            if is_valid(val):
                return val

    # ── Between pattern: number between X% and ₹current ──────────────────────
    if current_price:
        cur_pos = full_text.find(current_price)
        if cur_pos > 30:
            window     = full_text[max(0, cur_pos - 200): cur_pos]
            candidates = re.findall(r"[\d,]{3,}", window)
            for c in reversed(candidates):
                val = c.replace(",", "")
                if is_valid(val):
                    log.info(f"   [BETWEEN] orig={val}")
                    return val

    return ""


def extract_discount(soup: BeautifulSoup, full_text: str,
                     cur: str, orig: str) -> str:
    # Down arrow + number + %
    m = re.search(r"[\u2193\u2198\u25bc\u2b07]\s*(\d{1,2})\s*%", full_text)
    if m and 1 <= int(m.group(1)) <= 99:
        return m.group(1) + "%"

    tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if tag:
        m = re.search(r"(\d{1,2})%", safe(tag))
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"

    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if len(text) > 20:
            continue
        m = re.search(r"(\d{1,2})%\s*(off)?$", text, re.I)
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"

    # Math fallback: calculate from cur + orig
    if cur and orig and cur.isdigit() and orig.isdigit():
        c, o = int(cur), int(orig)
        if o > c > 0:
            disc = round((o - c) / o * 100)
            if 1 <= disc <= 99:
                log.info(f"   [MATH] disc={disc}%")
                return str(disc) + "%"

    return ""


def extract_rating(soup: BeautifulSoup, full_text: str) -> str:
    # Exact decimal like "4.1"
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


def extract_reviews(soup: BeautifulSoup, full_text: str, rating: str) -> str:
    """
    Reviews visual pattern: "1.5 ★ | 4"
    The number right after | pipe (next to star) = review count.

    HTML structure on Flipkart:
      <div>1.5 <span>★</span></div><span>|</span><span>4</span>
      OR all in one tag: "1.5 ★ | 4"
    
    Strategy: Find the | pipe symbol, take the number right after it
    that appears near the rating value.
    """

    # ── Method 1: Find pipe | and take number immediately after it ────────────
    # Scan every tag for pipe symbol, then get the number right after
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        # Pattern: "X.X ★ | NUMBER" or "X.X | NUMBER"
        m = re.search(r"[1-5]\.\d\s*[★✩⭐]?\s*\|\s*([\d,]+)", text)
        if m:
            val = m.group(1).replace(",", "")
            if val.isdigit() and int(val) >= 1:
                log.info(f"   [★|inline] reviews={val}")
                return val

    # ── Method 2: Find | pipe tag, then get the NEXT sibling tag's number ─────
    # Handles: <span>1.5 ★</span> <span>|</span> <span>4</span>
    for pipe_tag in soup.find_all(string=re.compile(r"^\s*\|\s*$")):
        next_sib = pipe_tag.find_next(["span", "div"])
        if next_sib:
            val = to_num(safe(next_sib))
            if val.isdigit() and int(val) >= 1:
                log.info(f"   [|sibling] reviews={val}")
                return val

    # ── Method 3: Find rating tag, then look at next sibling for number ───────
    if rating:
        for tag in soup.find_all(["div", "span"]):
            if safe(tag).strip() == rating:
                # Look at siblings after this tag
                parent = tag.parent
                if parent:
                    siblings = list(parent.children)
                    found_rating = False
                    for sib in siblings:
                        if hasattr(sib, "get_text"):
                            sib_text = sib.get_text(strip=True)
                        else:
                            sib_text = str(sib).strip()
                        if rating in sib_text:
                            found_rating = True
                            continue
                        if found_rating and sib_text and sib_text != "|":
                            val = re.sub(r"[^\d]", "", sib_text)
                            if val.isdigit() and int(val) >= 1:
                                log.info(f"   [rating-sibling] reviews={val}")
                                return val

    # ── Method 4: Scan full text for "rating | number" pattern ───────────────
    if rating:
        for pat in [
            re.escape(rating) + r"\s*[★✩⭐]\s*\|\s*([\d,]+)",
            re.escape(rating) + r"\s*\|\s*([\d,]+)",
            re.escape(rating) + r"\s*[★✩⭐]\s*([\d,]+)",
        ]:
            m = re.search(pat, full_text)
            if m:
                val = m.group(1).replace(",", "")
                if val.isdigit() and int(val) >= 1:
                    log.info(f"   [fulltext-pat] reviews={val}")
                    return val

    # ── Method 5: CSS selectors ───────────────────────────────────────────────
    for sel in [
        "div._1psv1zeb9._1psv1ze0._1psv1zegu",
        "span.Wphh3N", "span._2_R_DZ", "span._13vcmD",
    ]:
        tag = soup.select_one(sel)
        if tag:
            nums = re.findall(r"[\d,]+", safe(tag))
            for n in nums:
                val = n.replace(",", "")
                if val.isdigit() and int(val) >= 1:
                    return val

    # ── Method 6: Text patterns ───────────────────────────────────────────────
    for pattern in [
        r"([\d,]+[kK]?)\s+[Rr]ating",
        r"([\d,]+[kK]?)\s+[Rr]eview",
        r"based on\s+([\d,]+[kK]?)\s+rating",
    ]:
        m = re.search(pattern, full_text, re.I)
        if m:
            val = parse_k(m.group(1))
            if val.isdigit() and int(val) >= 1:
                return val

    # ── Method 7: number right after rating in text ───────────────────────────
    if rating:
        m = re.search(re.escape(rating) + r"[^\d]{1,15}([\d,]{1,})", full_text)
        if m:
            val = m.group(1).replace(",", "")
            if val.isdigit() and int(val) >= 1:
                return val

    return ""


# ══════════════════════════════════════════════════════════════════════════════
# PARSE ALL FIELDS
# ══════════════════════════════════════════════════════════════════════════════
def parse_page(soup: BeautifulSoup) -> dict:
    full_text = soup.get_text(" ", strip=True)

    cur  = extract_current_price(soup, full_text)
    disc = extract_discount(soup, full_text, cur, "")
    orig = extract_original_price(soup, full_text, cur, disc)
    if not disc:
        disc = extract_discount(soup, full_text, cur, orig)
    # If current price missing but orig+disc available, calculate it
    if not cur and orig and disc:
        disc_v = disc.replace("%", "").strip()
        if orig.isdigit() and disc_v.isdigit():
            cur = str(round(int(orig) * (1 - int(disc_v) / 100)))
            log.info(f"   [MATH] cur={cur}")

    rat  = extract_rating(soup, full_text)
    revs = extract_reviews(soup, full_text, rat)

    # Soft sanity: current must be less than original
    if cur and orig and cur.isdigit() and orig.isdigit():
        if int(cur) >= int(orig):
            log.warning(f"   SANITY: cur({cur}) >= orig({orig}) -- clearing orig")
            orig = ""

    return {
        "current_price":  cur,
        "original_price": orig,
        "discount":       disc,
        "rating":         rat,
        "reviews":        revs,
    }


# ══════════════════════════════════════════════════════════════════════════════
# BUILD DB PAYLOAD for each table's specific column names
# ══════════════════════════════════════════════════════════════════════════════
def build_payload(table_cfg: dict, extracted: dict) -> dict:
    cols    = table_cfg.get("columns", {})
    payload = {}

    cur  = extracted.get("current_price", "")
    orig = extracted.get("original_price", "")
    disc = extracted.get("discount", "")
    rat  = extracted.get("rating", "")
    revs = extracted.get("reviews", "")

    # Current Price — with ₹ symbol
    if cur and "current_price" in cols:
        payload[cols["current_price"]] = fmt_price(cur)

    # Original Price — with ₹ symbol
    if orig and "original_price" in cols:
        payload[cols["original_price"]] = fmt_price(orig)

    # Discount
    if disc and "discount" in cols:
        payload[cols["discount"]] = fmt_discount(disc)

    # Rating (separate column)
    if rat and "rating" in cols:
        payload[cols["rating"]] = rat

    # Reviews (separate column)
    if revs and "reviews" in cols:
        payload[cols["reviews"]] = revs

    # Extra reviews column (iphone has Number of Rating too)
    if revs and "extra_reviews" in cols:
        payload[cols["extra_reviews"]] = revs

    # Combined Rating and Reviews column
    # Format: "4.1 ★ | 239"
    if "combined" in cols:
        parts = []
        if rat:
            parts.append(rat)
        if revs:
            combined_val = f"{rat} ★ | {revs}" if rat else revs
        else:
            combined_val = rat
        if combined_val:
            payload[cols["combined"]] = combined_val

    return payload


# ══════════════════════════════════════════════════════════════════════════════
# PROCESS ONE TABLE
# ══════════════════════════════════════════════════════════════════════════════
def process_table(client: Client, table_cfg: dict):
    name     = table_cfg["name"]
    link_col = table_cfg["link_col"]

    log.info(f"\n{'═'*70}")
    log.info(f"  TABLE: {name.upper()}")
    log.info(f"{'═'*70}")

    result = client.table(name).select("*").execute()
    rows   = [r for r in result.data if r.get(link_col, "").strip()]
    log.info(f"  {len(rows)} products found.")

    total = len(rows)
    done  = 0
    fail  = 0

    for idx, row in enumerate(rows, 1):
        url = row[link_col].strip()
        log.info(f"\n  [{idx}/{total}] {url[:80]}")

        best = {}

        for attempt in range(1, 6):
            log.info(f"   Attempt {attempt}/5")
            soup = fetch_page(url, attempt)
            if not soup:
                time.sleep(3)
                continue

            data = parse_page(soup)
            log.info(f"   Got: {data}")

            # Merge non-empty values
            for k, v in data.items():
                if v and not best.get(k):
                    best[k] = v

            # Check if all important fields present
            missing = [f for f in ["current_price", "reviews"] if not best.get(f)]
            if not missing:
                break

            log.warning(f"   Missing: {missing}")
            time.sleep(2)

        # Build and write payload
        payload = build_payload(table_cfg, best)
        log.info(f"   Payload: {payload}")

        if not payload:
            log.warning("   Empty payload — skipping.")
            fail += 1
        else:
            try:
                client.table(name).update(payload).eq(link_col, url).execute()
                log.info(f"   [OK] Updated.")
                done += 1
            except Exception as exc:
                log.error(f"   [DB ERROR] {exc}")
                fail += 1

        time.sleep(DELAY_BETWEEN_PRODUCTS)

    log.info(f"\n  {name}: Updated={done}  Failed={fail}  Total={total}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log.info("=" * 70)
    log.info("  MASTER FLIPKART UPDATER — All Tables")
    log.info(f"  API Keys loaded: {len(SCRAPERAPI_KEYS)}")
    log.info("=" * 70)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    for table_cfg in TABLES:
        try:
            process_table(client, table_cfg)
        except Exception as exc:
            log.error(f"  ERROR processing table '{table_cfg['name']}': {exc}")
            continue

    log.info("\n" + "=" * 70)
    log.info("  ALL TABLES COMPLETE")
    log.info("=" * 70)


if __name__ == "__main__":
    main()

