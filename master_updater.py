"""
update_gaming_cpu.py
Table: gaming cpu

Two-pass strategy per product:
  Pass 1 (CHEAP)  -> Current Price + Discount  (static HTML)
  Pass 2 (RENDER) -> Rating + Reviews           (JavaScript rendered)
  Original Price  -> Math from cur+disc, then page scan, then <s> tag

Math fallbacks (last resort after all attempts):
  cur + disc  -> orig = cur / (1 - disc/100)
  orig + disc -> cur  = orig * (1 - disc/100)
  cur + orig  -> disc = (orig - cur) / orig * 100
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

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"
TABLE_NAME          = "gaming cpu"
REQUEST_TIMEOUT     = 90
DELAY               = 1


# ── Supabase ──────────────────────────────────────────────────────────────────
def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_products(client: Client) -> list[dict]:
    log.info(f"Fetching rows from '{TABLE_NAME}'...")
    result = client.table(TABLE_NAME).select("*").execute()
    rows = [r for r in result.data if r.get("Product Link", "").strip()]
    log.info(f"   -> {len(rows)} products found.")
    return rows


# ── Fetch ─────────────────────────────────────────────────────────────────────
def fetch_cheap(url: str) -> BeautifulSoup | None:
    """1 credit — static HTML only. Good for prices."""
    params = {"api_key": SCRAPERAPI_KEY, "url": url, "country_code": "in"}
    try:
        resp = requests.get(f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        log.info(f"   [CHEAP] HTTP {resp.status_code}")
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        log.error(f"   [CHEAP] {exc}")
        return None


def fetch_render(url: str) -> BeautifulSoup | None:
    """premium + render — JavaScript runs. Good for reviews/ratings."""
    params = {
        "api_key":      SCRAPERAPI_KEY,
        "url":          url,
        "country_code": "in",
        "premium":      "true",
        "render":       "true",
    }
    try:
        resp = requests.get(f"{SCRAPERAPI_ENDPOINT}?{urlencode(params)}", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        log.info(f"   [RENDER] HTTP {resp.status_code}")
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        log.error(f"   [RENDER] {exc}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def safe(tag, d=""):
    return tag.get_text(strip=True) if tag else d

def to_num(text: str) -> str:
    return re.sub(r"[^\d]", "", str(text)).strip()

def valid_price(val: str) -> bool:
    return val.isdigit() and 100 <= int(val) <= 5000000

def parse_k(text: str) -> str:
    t = text.strip().replace(",", "")
    m = re.match(r"([\d.]+)[kK]", t)
    if m:
        return str(int(float(m.group(1)) * 1000))
    m = re.match(r"(\d+)", t)
    return m.group(1) if m else ""

def fmt_price(val: str) -> str:
    if not val or not val.isdigit():
        return val
    n = int(val)
    s = str(n)
    if len(s) <= 3:
        return f"₹{s}"
    result = s[-3:]
    s = s[:-3]
    while s:
        result = s[-2:] + "," + result
        s = s[:-2]
    return f"₹{result.lstrip(',')}"


# ── PASS 1: Extract price data from cheap (static) HTML ──────────────────────
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

    m = re.search(r"Buy\s*at\s*₹\s*([\d,]+)", full_text, re.I)
    if m:
        val = m.group(1).replace(",", "")
        if valid_price(val):
            return val

    # Find price near Add to cart
    cart = soup.find(string=re.compile(r"Add to cart", re.I))
    if cart:
        parent = cart.find_parent("div")
        for _ in range(6):
            if not parent:
                break
            # Only look at ₹ symbol prices
            prices = re.findall(r"₹\s*([\d,]+)", parent.get_text())
            valid_list = sorted([
                int(p.replace(",", "")) for p in prices
                if valid_price(p.replace(",", ""))
            ])
            if valid_list:
                return str(valid_list[0])
            parent = parent.find_parent("div")
    return ""


def extract_discount(soup: BeautifulSoup, full_text: str) -> str:
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
    return ""


def extract_original_price(soup: BeautifulSoup, full_text: str,
                           current_price: str, discount: str) -> str:
    def is_valid(val: str) -> bool:
        if not val or not valid_price(val):
            return False
        if current_price and current_price.isdigit():
            return int(val) > int(current_price)
        return True

    # ── MATH FIRST (5% tolerance, only ₹ symbol prices) ──────────────────────
    if current_price and current_price.isdigit() and discount:
        disc_clean = discount.replace("%", "").strip()
        if disc_clean.isdigit() and 1 <= int(disc_clean) <= 99:
            cur_val      = int(current_price)
            disc_val     = int(disc_clean)
            expected_mrp = cur_val / (1 - disc_val / 100)

            # ONLY scan ₹ symbol prices — avoids EMI/warranty amounts
            rupee_prices = re.findall(r"₹\s*([\d,]+)", full_text)
            best_match   = ""
            best_diff    = float("inf")
            for p in rupee_prices:
                v = p.replace(",", "")
                if not v.isdigit() or not valid_price(v):
                    continue
                if int(v) <= cur_val:
                    continue
                pct_diff = abs(int(v) - expected_mrp) / expected_mrp
                if pct_diff < best_diff and pct_diff <= 0.05:  # strict 5%
                    best_diff  = pct_diff
                    best_match = v
            if best_match:
                log.info(f"   [MATH] orig={best_match}  diff={best_diff:.2%}")
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
                        log.info(f"   [JSON-LD] orig={val}")
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
        if "line-through" in tag.get("style", ""):
            val = to_num(safe(tag))
            if is_valid(val):
                return val

    # ── Number just before current price in text ──────────────────────────────
    if current_price:
        cur_pos = full_text.find(current_price)
        if cur_pos > 30:
            window = full_text[max(0, cur_pos - 150): cur_pos]
            # Only ₹ prices in window
            candidates = re.findall(r"₹\s*([\d,]+)", window)
            for c in reversed(candidates):
                val = c.replace(",", "")
                if is_valid(val):
                    log.info(f"   [BEFORE-CUR] orig={val}")
                    return val
    return ""


# ── PASS 2: Extract reviews+rating from RENDERED HTML ────────────────────────
def extract_rating_and_reviews(soup: BeautifulSoup, full_text: str) -> tuple[str, str]:
    """
    Extract rating and reviews from JS-rendered page.
    Visual pattern: "1.5 ★ | 4"
    Rating = decimal like 1.5, Reviews = number after | pipe.
    """
    rating  = ""
    reviews = ""

    # ── Rating ────────────────────────────────────────────────────────────────
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.\d", text):
            rating = text
            break

    if not rating:
        m = re.search(r"([1-5]\.\d)\s*[★✩⭐|]", full_text)
        if m:
            rating = m.group(1)

    # ── Reviews: ★ | NUMBER pattern ──────────────────────────────────────────
    # Method A: any tag with "X.X ★ | NUMBER" all inline
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        m = re.search(r"[1-5]\.\d\s*[★✩⭐]?\s*\|\s*([\d,]+)", text)
        if m:
            val = m.group(1).replace(",", "")
            if val.isdigit() and int(val) >= 1:
                reviews = val
                log.info(f"   [★|inline] reviews={val}")
                break

    # Method B: Find | pipe as standalone text node, take next sibling number
    if not reviews:
        for pipe in soup.find_all(string=re.compile(r"^\s*\|\s*$")):
            nxt = pipe.find_next(["span", "div"])
            if nxt:
                val = to_num(safe(nxt))
                if val.isdigit() and int(val) >= 1:
                    reviews = val
                    log.info(f"   [|next-sibling] reviews={val}")
                    break

    # Method C: using rating, find number right after in text
    if not reviews and rating:
        for pat in [
            re.escape(rating) + r"\s*[★✩⭐]\s*\|\s*([\d,]+)",
            re.escape(rating) + r"\s*\|\s*([\d,]+)",
            re.escape(rating) + r"\s*[★✩⭐]\s*([\d,]+)",
            re.escape(rating) + r"[^\d]{1,10}([\d,]+)",
        ]:
            m = re.search(pat, full_text)
            if m:
                val = m.group(1).replace(",", "")
                if val.isdigit() and int(val) >= 1:
                    reviews = val
                    log.info(f"   [rating-pat] reviews={val}")
                    break

    # Method D: tag containing rating and | together, split by |
    if not reviews and rating:
        for tag in soup.find_all(["div", "span"]):
            text = safe(tag).strip()
            if rating in text and "|" in text:
                parts = text.split("|")
                for part in parts:
                    p = part.strip().replace(",", "")
                    if p.isdigit() and int(p) >= 1 and p != to_num(rating):
                        reviews = p
                        log.info(f"   [pipe-split] reviews={reviews}")
                        break
                if reviews:
                    break

    # Method E: CSS selectors
    if not reviews:
        for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zegu",
                    "span.Wphh3N", "span._2_R_DZ", "span._13vcmD"]:
            tag = soup.select_one(sel)
            if tag:
                nums = re.findall(r"[\d,]+", safe(tag))
                for n in nums:
                    val = n.replace(",", "")
                    if val.isdigit() and int(val) >= 1:
                        reviews = val
                        break
            if reviews:
                break

    # Method F: text pattern scan
    if not reviews:
        for pat in [r"([\d,]+[kK]?)\s+[Rr]ating",
                    r"([\d,]+[kK]?)\s+[Rr]eview",
                    r"based on\s+([\d,]+[kK]?)\s+rating"]:
            m = re.search(pat, full_text, re.I)
            if m:
                val = parse_k(m.group(1))
                if val.isdigit() and int(val) >= 1:
                    reviews = val
                    break

    return rating, reviews


# ── Math fallbacks ────────────────────────────────────────────────────────────
def math_fallbacks(cur: str, orig: str, disc: str) -> tuple[str, str, str]:
    """If 2 of 3 are known, calculate the third."""
    if cur and disc and not orig:
        d = disc.replace("%", "").strip()
        if d.isdigit() and cur.isdigit() and 1 <= int(d) <= 99:
            orig = str(round(int(cur) / (1 - int(d) / 100)))
            log.info(f"   [MATH-FALLBACK] orig={orig}")

    if orig and disc and not cur:
        d = disc.replace("%", "").strip()
        if d.isdigit() and orig.isdigit() and 1 <= int(d) <= 99:
            cur = str(round(int(orig) * (1 - int(d) / 100)))
            log.info(f"   [MATH-FALLBACK] cur={cur}")

    if cur and orig and not disc:
        if cur.isdigit() and orig.isdigit() and int(orig) > int(cur):
            d = round((int(orig) - int(cur)) / int(orig) * 100)
            if 1 <= d <= 99:
                disc = str(d) + "%"
                log.info(f"   [MATH-FALLBACK] disc={disc}")

    return cur, orig, disc


# ── Update DB ─────────────────────────────────────────────────────────────────
def update_row(client: Client, url: str, data: dict) -> bool:
    try:
        client.table(TABLE_NAME).update(data).eq("Product Link", url).execute()
        log.info(
            f"   [OK] Price:{data.get('Current Price','')}  "
            f"MRP:{data.get('Original Price','')}  "
            f"Disc:{data.get('Discount','')}  "
            f"Rating:{data.get('Rating','')}  "
            f"Reviews:{data.get('Number of Reviews','')}"
        )
        return True
    except Exception as exc:
        log.error(f"   [DB] {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 70)
    log.info(f"  Flipkart Updater -- {TABLE_NAME} -- Two-Pass Mode")
    log.info("=" * 70)

    client   = get_client()
    products = fetch_all_products(client)
    total    = len(products)
    updated  = 0
    failed   = 0

    for idx, row in enumerate(products, 1):
        url = row["Product Link"].strip()
        log.info(f"\n{'─'*60}")
        log.info(f"[{idx}/{total}]  {url[:85]}")
        log.info(f"{'─'*60}")

        cur = disc = orig = rating = reviews = ""

        # ── PASS 1: CHEAP (1 credit) — try everything ────────────────────────
        log.info("   PASS 1 (CHEAP) — all fields")
        soup1 = fetch_cheap(url)
        if soup1:
            ft1    = soup1.get_text(" ", strip=True)
            cur    = extract_current_price(soup1, ft1)
            disc   = extract_discount(soup1, ft1)
            orig   = extract_original_price(soup1, ft1, cur, disc)
            rating, reviews = extract_rating_and_reviews(soup1, ft1)
            log.info(f"   Pass1: cur={cur}  disc={disc}  orig={orig}  rating={rating}  reviews={reviews}")

        time.sleep(1)

        # ── PASS 2: RENDER (25 credits) — ONLY if reviews/rating missing ──────
        if not reviews or not rating:
            log.info("   PASS 2 (RENDER) — reviews/rating missing, fetching...")
            soup2 = fetch_render(url)
            if soup2:
                ft2 = soup2.get_text(" ", strip=True)
                rating2, reviews2 = extract_rating_and_reviews(soup2, ft2)
                if not rating:
                    rating = rating2
                if not reviews:
                    reviews = reviews2
                # Fill any missing price fields too
                if not cur:
                    cur = extract_current_price(soup2, ft2)
                if not disc:
                    disc = extract_discount(soup2, ft2)
                if not orig:
                    orig = extract_original_price(soup2, ft2, cur, disc)
                log.info(f"   Pass2: rating={rating}  reviews={reviews}")
        else:
            log.info("   PASS 2 skipped — all data found in cheap fetch ✅ (credits saved)")

        # ── Math fallbacks ────────────────────────────────────────────────────
        cur, orig, disc = math_fallbacks(cur, orig, disc)

        # ── Sanity check ──────────────────────────────────────────────────────
        if cur and orig and cur.isdigit() and orig.isdigit():
            if int(cur) >= int(orig):
                log.warning(f"   SANITY: cur({cur}) >= orig({orig}) -- clearing orig")
                orig = ""

        # ── Build payload ─────────────────────────────────────────────────────
        payload = {}
        if cur:
            payload["Current Price"]     = fmt_price(cur)
        if orig:
            payload["Original Price"]    = fmt_price(orig)
        if disc:
            payload["Discount"]          = disc if "%" in disc else disc + "%"
        if rating:
            payload["Rating"]            = rating
        if reviews:
            payload["Number of Reviews"] = reviews

        log.info(f"   FINAL: {payload}")

        if not payload:
            log.warning("   Empty payload — skipping.")
            failed += 1
        else:
            ok = update_row(client, url, payload)
            if ok:
                updated += 1
            else:
                failed += 1

        time.sleep(DELAY)

    log.info("\n" + "=" * 70)
    log.info(f"  Done -- {TABLE_NAME}")
    log.info(f"  Updated : {updated}")
    log.info(f"  Failed  : {failed}")
    log.info(f"  Total   : {total}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
