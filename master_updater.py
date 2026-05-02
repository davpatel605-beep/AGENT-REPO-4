"""
master_updater.py — Universal Flipkart Price Updater
=====================================================
Tables: earbuds → iphone → keybord → laptop →
        monitar → mouse → smart phone → smart+tv → smartwatch

RULES:
  1. Discount FIRST — if no ↓ discount found → disc="" → no orig price calc
  2. Current Price — accurate as-is (not changed)
  3. Original Price — calc from cur+disc → find <s>/<del>/line-through near page
  4. Reviews + Rating — NOT TOUCHED
  5. iphone: NO auto-discount ever
  6. math_fallbacks: NEVER auto-generate discount
  7. Credit saving: RENDER only if rating/reviews missing
  8. Non-cancel policy

Strikethrough in HTML (research confirmed):
  <s>29,999</s>          ← HTML5 s tag (most common on Flipkart)
  <del>29,999</del>      ← semantic deleted text
  <strike>29,999</strike> ← deprecated but still used
  style="text-decoration: line-through"  ← CSS style
"""

import os, re, json, time, logging, requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_KEY"].strip()

SCRAPERAPI_KEYS = []
for _i in ["", "_2", "_3", "_4", "_5", "_6", "_7", "_8"]:
    _k = os.environ.get(f"SCRAPERAPI_KEY{_i}", "").strip()
    if _k:
        SCRAPERAPI_KEYS.append(_k)

_key_idx = 0

def get_key():
    return SCRAPERAPI_KEYS[_key_idx] if SCRAPERAPI_KEYS else ""

def rotate_key():
    global _key_idx
    if _key_idx < len(SCRAPERAPI_KEYS) - 1:
        _key_idx += 1
        log.warning(f"   [KEY] Rotated to key #{_key_idx + 1}")
        return True
    log.error("   [KEY] All keys exhausted!")
    return False

ENDPOINT        = "https://api.scraperapi.com/"
REQUEST_TIMEOUT = 90
DELAY           = 1
MAX_REVIEWS     = 500000


# ══════════════════════════════════════════════════════════════════════════════
# TABLE CONFIG
# ══════════════════════════════════════════════════════════════════════════════
TABLES = [
    {
        "name": "earbuds",
        "link": "Product Link",
        "swap": False,
        "cols": {
            "current_price":  "Current Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Number of Reviews",
        },
    },
    {
        "name": "iphone",
        "link": "Product URL",
        "swap": True,
        "cols": {
            "current_price":  "Discounted Price",
            "original_price": "Price",
            "discount":       "Discount Percentage",
            "rating":         "Product Rating",
            "reviews":        "Number of Reviews",
            "reviews2":       "Number of Ratings",
        },
    },
    {
        "name": "keybord",
        "link": "Product Link",
        "swap": False,
        "cols": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Number of Reviews",
        },
    },
    {
        "name": "laptop",
        "link": "Product Link",
        "swap": False,
        "cols": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "combined":       "Rating and Reviews",
        },
    },
    {
        "name": "monitar",
        "link": "Product URL",
        "swap": False,
        "cols": {
            "current_price":  "Current Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Number of Reviews",
        },
    },
    {
        "name": "mouse",
        "link": "Product Link",
        "swap": False,
        "cols": {
            "current_price":  "Current Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Number of Reviews",
        },
    },
    {
        "name": "smart phone",
        "link": "Product Link",
        "swap": False,
        "cols": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "combined":       "Ratings and Reviews",
        },
    },
    {
        "name": "smart+tv",
        "link": "Product Link",
        "swap": False,
        "cols": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "combined":       "Ratings and Reviews",
        },
    },
    {
        "name": "smartwatch",
        "link": "Product Link",
        "swap": False,
        "cols": {
            "current_price":  "Price",
            "original_price": "Original Price",
            "discount":       "Discount",
            "rating":         "Rating",
            "reviews":        "Review",
        },
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# FETCH
# ══════════════════════════════════════════════════════════════════════════════
def fetch(url: str, render: bool = False) -> BeautifulSoup | None:
    key = get_key()
    if not key:
        return None
    params = {"api_key": key, "url": url, "country_code": "in"}
    if render:
        params["premium"] = "true"
        params["render"]  = "true"
    mode = "RENDER" if render else "CHEAP"
    try:
        resp = requests.get(f"{ENDPOINT}?{urlencode(params)}", timeout=REQUEST_TIMEOUT)
        if resp.status_code in (401, 403):
            if rotate_key():
                return fetch(url, render)
            return None
        resp.raise_for_status()
        log.info(f"   [{mode}] HTTP {resp.status_code}")
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        log.error(f"   [{mode}] {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def safe(tag, d=""):
    return tag.get_text(strip=True) if tag else d

def to_num(t):
    return re.sub(r"[^\d]", "", str(t)).strip()

def valid_price(v):
    return v.isdigit() and 100 <= int(v) <= 5000000

def parse_k(t):
    t = t.strip().replace(",", "")
    m = re.match(r"([\d.]+)[kK]", t)
    if m:
        return str(int(float(m.group(1)) * 1000))
    m = re.match(r"(\d+)", t)
    return m.group(1) if m else ""

def fmt_price(v):
    if not v or not v.isdigit():
        return v
    s = str(int(v))
    if len(s) <= 3:
        return f"₹{s}"
    result, s = s[-3:], s[:-3]
    while s:
        result, s = s[-2:] + "," + result, s[:-2]
    return f"₹{result.lstrip(',')}"

def fmt_disc(v):
    v = v.replace("%", "").strip()
    return (v + "%") if v.isdigit() and 1 <= int(v) <= 99 else ""

def fmt_reviews(v):
    if not v or not v.isdigit():
        return v
    n = int(v)
    if n < 1000:
        return v
    s = str(n)
    result, s = s[-3:], s[:-3]
    while s:
        result, s = s[-2:] + "," + result, s[:-2]
    return result.lstrip(",")


# ══════════════════════════════════════════════════════════════════════════════
# REVIEWS — NOT TOUCHED
# ══════════════════════════════════════════════════════════════════════════════
def validate_review(raw: str, discount: str = "", force_accept: bool = False) -> str:
    if not raw:
        return ""
    clean = raw.replace(",", "").strip()
    if not clean.isdigit():
        return ""
    val = int(clean)
    if val < 1:
        return ""
    disc_num = discount.replace("%", "").strip() if discount else ""
    if disc_num.isdigit() and len(disc_num) <= 2 and clean.endswith(disc_num):
        stripped = clean[:-len(disc_num)]
        if stripped.isdigit() and int(stripped) >= 1:
            log.info(f"   [REVIEW-STRIP] {clean} → {stripped}")
            clean = stripped
            val   = int(clean)
    if "," in raw:
        parts = raw.split(",")
        valid_indian = (1 <= len(parts[0]) <= 3 and all(len(p) == 2 for p in parts[1:]))
        if not valid_indian and not force_accept:
            return ""
    if val > MAX_REVIEWS and not force_accept:
        return ""
    return clean


def extract_review_number(soup, ft, rating, discount) -> str:
    def try_methods(force: bool = False) -> str:
        if rating:
            for tag in soup.find_all(["div","span"]):
                t = safe(tag).strip()
                m = re.search(r"[1-5]\.[0-9]\s*[★✩⭐]?\s*\|\s*([\d,]+)", t)
                if m:
                    v = validate_review(m.group(1), discount, force)
                    if v:
                        log.info(f"   [M1-INLINE] reviews={v}")
                        return v
        for pipe in soup.find_all(string=re.compile(r"^\s*\|\s*$")):
            nxt = pipe.find_next(["span","div"])
            if nxt:
                v = validate_review(safe(nxt).strip(), discount, force)
                if v:
                    log.info(f"   [M2-PIPE] reviews={v}")
                    return v
        if rating:
            for pat in [
                re.escape(rating) + r"\s*[★✩⭐]\s*\|\s*([\d,]+)",
                re.escape(rating) + r"\s*\|\s*([\d,]+)",
            ]:
                m = re.search(pat, ft)
                if m:
                    v = validate_review(m.group(1), discount, force)
                    if v:
                        log.info(f"   [M3-TEXT] reviews={v}")
                        return v
        for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zegu",
                    "span.Wphh3N","span._2_R_DZ","span._13vcmD"]:
            tag = soup.select_one(sel)
            if tag:
                for raw in re.findall(r"[\d,]+", safe(tag)):
                    v = validate_review(raw, discount, force)
                    if v:
                        log.info(f"   [M4-CSS] reviews={v}")
                        return v
        for pat in [r"([\d,]+[kK]?)\s+[Rr]ating",
                    r"([\d,]+[kK]?)\s+[Rr]eview",
                    r"based on\s+([\d,]+[kK]?)\s+rating"]:
            m = re.search(pat, ft, re.I)
            if m:
                v = validate_review(parse_k(m.group(1)), discount, force)
                if v:
                    log.info(f"   [M5-KEYWORD] reviews={v}")
                    return v
        return ""

    for attempt in range(1, 6):
        force  = (attempt == 5)
        result = try_methods(force=force)
        if result:
            return result
        if attempt < 5:
            log.warning(f"   [REVIEW] Attempt {attempt} failed, retrying...")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# RATING — NOT TOUCHED
# ══════════════════════════════════════════════════════════════════════════════
def get_rating(soup, ft) -> str:
    for tag in soup.find_all(["div","span"]):
        t = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.[0-9]", t):
            return t
    m = re.search(r"([1-5]\.[0-9])\s*[★✩⭐|]", ft)
    if m: return m.group(1)
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# CURRENT PRICE — NOT CHANGED (working perfectly)
# ══════════════════════════════════════════════════════════════════════════════
def get_current_price(soup, ft):
    for sel in [
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
        "div.Nx9bqj.CxhGGd", "div.Nx9bqj",
        "div._30jeq3._16Jk6d", "div._30jeq3", "div.CEmiEU",
    ]:
        tag = soup.select_one(sel)
        if tag:
            v = to_num(safe(tag))
            if v and valid_price(v):
                return v
    m = re.search(r"Buy\s*at\s*₹\s*([\d,]+)", ft, re.I)
    if m:
        v = m.group(1).replace(",", "")
        if valid_price(v): return v
    cart = soup.find(string=re.compile(r"Add to cart", re.I))
    if cart:
        p = cart.find_parent("div")
        for _ in range(6):
            if not p: break
            prices = re.findall(r"₹\s*([\d,]+)", p.get_text())
            vlist  = sorted([int(x.replace(",","")) for x in prices
                             if valid_price(x.replace(",",""))])
            if vlist: return str(vlist[0])
            p = p.find_parent("div")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# DISCOUNT — 100% ACCURATE
# Only picks REAL product discount (↓ arrow + %).
# NO auto-generation of discount. If not found → return ""
# ══════════════════════════════════════════════════════════════════════════════
def get_discount(soup, ft):
    """
    Flipkart shows discount as: ↓54%  or  54% off
    We look for down arrow ↓ (most reliable) then fallbacks.
    NEVER auto-generate. If not on page → return "".
    """
    # M1: Down arrow unicode variants + number + %
    m = re.search(r"[\u2193\u2198\u25bc\u2b07\u21e9\u21a1]\s*(\d{1,2})\s*%", ft)
    if m and 1 <= int(m.group(1)) <= 99:
        log.info(f"   [DISC-ARROW] {m.group(1)}%")
        return m.group(1) + "%"

    # M2: Flipkart CSS class for discount
    tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if tag:
        m = re.search(r"(\d{1,2})%", safe(tag))
        if m and 1 <= int(m.group(1)) <= 99:
            log.info(f"   [DISC-CSS] {m.group(1)}%")
            return m.group(1) + "%"

    # M3: Short standalone tag ≤15 chars — "54% off" or "54%"
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if len(text) > 15:
            continue
        m = re.fullmatch(r"(\d{1,2})%\s*(off)?", text, re.I)
        if m and 1 <= int(m.group(1)) <= 99:
            log.info(f"   [DISC-SHORT] {m.group(1)}%")
            return m.group(1) + "%"

    # M4: Tag ≤30 chars containing "X% off" — must have "off" keyword
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if len(text) > 30:
            continue
        m = re.search(r"(\d{1,2})%\s+off", text, re.I)
        if m and 1 <= int(m.group(1)) <= 99:
            log.info(f"   [DISC-OFF] {m.group(1)}%")
            return m.group(1) + "%"

    # M5: Full text "X% off" — filter out bank offer context
    for m in re.finditer(r"\b(\d{1,2})%\s+off\b", ft, re.I):
        val = int(m.group(1))
        if not (1 <= val <= 99):
            continue
        ctx = ft[max(0, m.start()-50): m.end()+30].lower()
        bank_kw = ["bank", "credit", "debit", "hdfc", "sbi", "axis",
                   "icici", "cashback", "upi", "emi", "kotak", "rbl"]
        if not any(kw in ctx for kw in bank_kw):
            log.info(f"   [DISC-TEXT] {val}%")
            return str(val) + "%"

    # No discount found on page
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# ORIGINAL PRICE
# Step 1: calc exact MRP = cur / (1 - disc/100)
# Step 2: collect ALL strikethrough numbers from page
#   - <s> tag (HTML5 standard)
#   - <del> tag (semantic deleted)
#   - <strike> tag (deprecated but used)
#   - CSS text-decoration: line-through
#   - Flipkart CSS classes
# Step 3: find strikethrough number closest to calculated MRP
#   - within ₹15 → use page number
#   - within 10% → use page number
#   - else → use exact calculation
# ONLY called when discount was found on page
# ══════════════════════════════════════════════════════════════════════════════
def get_original_price(soup, ft, cur, disc):
    """
    Called ONLY when discount is found.
    Uses calculation + strikethrough matching for maximum accuracy.
    """
    if not cur or not cur.isdigit() or not disc:
        return ""

    d = disc.replace("%","").strip()
    if not d.isdigit() or not (1 <= int(d) <= 99):
        return ""

    # Step 1: Calculate exact MRP
    calc_mrp = round(int(cur) / (1 - int(d) / 100))
    log.info(f"   [MRP-CALC] cur={cur} disc={disc} → calc_mrp={calc_mrp}")

    # Step 2: Collect strikethrough numbers from all sources
    strikethrough = []

    # A. <s> tag — HTML5 standard strikethrough (most common on Flipkart)
    for tag in soup.find_all("s"):
        v = to_num(safe(tag))
        if v and valid_price(v) and int(v) > int(cur):
            strikethrough.append(int(v))

    # B. <del> tag — semantic deleted text
    for tag in soup.find_all("del"):
        v = to_num(safe(tag))
        if v and valid_price(v) and int(v) > int(cur):
            strikethrough.append(int(v))

    # C. <strike> tag — deprecated but still used
    for tag in soup.find_all("strike"):
        v = to_num(safe(tag))
        if v and valid_price(v) and int(v) > int(cur):
            strikethrough.append(int(v))

    # D. CSS line-through style attribute
    for tag in soup.find_all(True):
        style = tag.get("style", "")
        if "line-through" in style:
            v = to_num(safe(tag))
            if v and valid_price(v) and int(v) > int(cur):
                strikethrough.append(int(v))

    # E. Flipkart CSS classes for MRP display
    for sel in [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.v1zwn21m._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA", "div.yRaY8j",
        "div._3I9_wc._2p6lqe", "div._3I9_wc",
    ]:
        tag = soup.select_one(sel)
        if tag:
            v = to_num(safe(tag))
            if v and valid_price(v) and int(v) > int(cur):
                strikethrough.append(int(v))

    # Step 3: Match strikethrough numbers to calculated MRP
    if strikethrough:
        # Find closest number to calculated MRP
        closest = min(strikethrough, key=lambda x: abs(x - calc_mrp))
        diff_rs  = abs(closest - calc_mrp)
        diff_pct = diff_rs / calc_mrp

        if diff_rs <= 15:
            # Within ₹15 → use page number (most accurate)
            log.info(f"   [MRP-PAGE-EXACT] {closest} (calc={calc_mrp}, diff=₹{diff_rs})")
            return str(closest)
        elif diff_pct <= 0.10:
            # Within 10% → use page number
            log.info(f"   [MRP-PAGE-NEAR] {closest} (calc={calc_mrp}, diff={diff_pct:.1%})")
            return str(closest)
        else:
            # Too far → use exact calculation
            log.info(f"   [MRP-CALC-EXACT] {calc_mrp} (closest={closest}, diff=₹{diff_rs})")
            return str(calc_mrp)

    # No strikethrough found → use exact calculation
    log.info(f"   [MRP-CALC-ONLY] {calc_mrp}")
    return str(calc_mrp)


# ══════════════════════════════════════════════════════════════════════════════
# MATH FALLBACKS — only cur+orig → disc (NEVER auto-add discount)
# ══════════════════════════════════════════════════════════════════════════════
def math_fallbacks(cur, orig, disc):
    """
    Only one fallback allowed:
    - If cur AND orig found but no disc → calculate disc from them
    NEVER auto-generate orig or disc from scratch.
    """
    if cur and orig and not disc:
        if cur.isdigit() and orig.isdigit() and int(orig) > int(cur):
            d = round((int(orig) - int(cur)) / int(orig) * 100)
            if 1 <= d <= 99:
                disc = str(d) + "%"
                log.info(f"   [MATH] disc={disc} (from cur+orig)")
    return cur, orig, disc


# ══════════════════════════════════════════════════════════════════════════════
# BUILD PAYLOAD
# ══════════════════════════════════════════════════════════════════════════════
def build_payload(cols, cur, orig, disc, rating, reviews, swap=False):
    p = {}

    if swap:
        # iphone: Discounted Price col = current, Price col = original
        if cur and "current_price" in cols:
            p[cols["current_price"]]  = fmt_price(cur)
        if orig and "original_price" in cols:
            p[cols["original_price"]] = fmt_price(orig)
        # No discount = no original price. Current price in Discounted Price col.
        if not orig and cur and "current_price" in cols:
            p[cols["current_price"]]  = fmt_price(cur)
    else:
        if cur and "current_price" in cols:
            p[cols["current_price"]]  = fmt_price(cur)
        if orig and "original_price" in cols:
            p[cols["original_price"]] = fmt_price(orig)

    if disc and "discount" in cols:
        p[cols["discount"]] = fmt_disc(disc)

    if "combined" in cols:
        if rating and reviews:
            p[cols["combined"]] = f"{rating} ★ | {fmt_reviews(reviews)}"
        elif rating:
            p[cols["combined"]] = rating
    else:
        if rating and "rating" in cols:
            p[cols["rating"]] = rating
        if reviews:
            if "reviews" in cols:
                p[cols["reviews"]] = fmt_reviews(reviews)
            if "reviews2" in cols:
                p[cols["reviews2"]] = fmt_reviews(reviews)
    return p


# ══════════════════════════════════════════════════════════════════════════════
# DB UPDATE
# ══════════════════════════════════════════════════════════════════════════════
def update_db(client, table, link_col, url, payload):
    if not payload:
        log.warning("   Empty payload — skipping.")
        return False
    try:
        check = client.table(table).select(link_col).eq(link_col, url).execute()
        if not check.data:
            clean = url.strip().rstrip("/")
            check2 = client.table(table).select(link_col).eq(link_col, clean).execute()
            if check2.data:
                url = clean
                log.info("   [URL-FIX] Matched cleaned URL")
            else:
                log.error(f"   [URL-NOT-FOUND] {url[:70]}")
                return False
        try:
            client.table(table).update(payload).eq(link_col, url).execute()
            log.info(f"   [OK] {payload}")
            return True
        except Exception:
            log.warning("   Bulk update failed — trying column by column...")
            success = False
            for col, val in payload.items():
                try:
                    client.table(table).update({col: val}).eq(link_col, url).execute()
                    log.info(f"   [OK-COL] {col}={val}")
                    success = True
                except Exception as ce:
                    log.warning(f"   [COL-SKIP] '{col}' → {ce}")
            return success
    except Exception as exc:
        log.error(f"   [DB] {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# PROCESS ONE TABLE
# ══════════════════════════════════════════════════════════════════════════════
def process_table(client, cfg):
    name     = cfg["name"]
    link_col = cfg["link"]
    cols     = cfg["cols"]
    swap     = cfg.get("swap", False)

    log.info(f"\n{'═'*70}")
    log.info(f"  TABLE: {name.upper()}  {'[SWAPPED]' if swap else ''}")
    log.info(f"{'═'*70}")

    rows = [r for r in client.table(name).select("*").execute().data
            if r.get(link_col,"").strip()]
    log.info(f"  {len(rows)} products.")

    done = fail = 0
    for idx, row in enumerate(rows, 1):
        url = row[link_col].strip()
        log.info(f"\n  [{idx}/{len(rows)}] {url[:80]}")

        cur = orig = disc = rating = reviews = ""

        # Pass 1: CHEAP (1 credit) — try all fields
        soup1 = fetch(url, render=False)
        if soup1:
            ft1     = soup1.get_text(" ", strip=True)
            cur     = get_current_price(soup1, ft1)
            disc    = get_discount(soup1, ft1)          # FIND DISCOUNT FIRST
            orig    = get_original_price(soup1, ft1, cur, disc)  # only if disc found
            rating  = get_rating(soup1, ft1)
            reviews = extract_review_number(soup1, ft1, rating, disc)
            log.info(f"   Pass1: cur={cur} disc={disc} orig={orig} "
                     f"rating={rating} reviews={reviews}")

        time.sleep(1)

        # Pass 2: RENDER — only if rating or reviews missing
        if not reviews or not rating:
            log.info("   Pass2 (RENDER)...")
            soup2 = fetch(url, render=True)
            if soup2:
                ft2 = soup2.get_text(" ", strip=True)
                r2  = get_rating(soup2, ft2)
                rv2 = extract_review_number(soup2, ft2, r2, disc)
                if not rating:  rating  = r2
                if not reviews: reviews = rv2
                if not cur:     cur     = get_current_price(soup2, ft2)
                if not disc:
                    disc = get_discount(soup2, ft2)
                    if disc and not orig:
                        orig = get_original_price(soup2, ft2, cur, disc)
                if not orig and disc:
                    orig = get_original_price(soup2, ft2, cur, disc)
                log.info(f"   Pass2: rating={rating} reviews={reviews}")
        else:
            log.info("   Pass2 skipped ✅ credits saved")

        # Math fallback: only cur+orig → disc (no auto-discount)
        cur, orig, disc = math_fallbacks(cur, orig, disc)

        # Sanity: current must be less than original
        if cur and orig and cur.isdigit() and orig.isdigit():
            if int(cur) >= int(orig):
                log.warning(f"   SANITY: cur({cur})>=orig({orig}) — clearing orig")
                orig = ""

        payload = build_payload(cols, cur, orig, disc, rating, reviews, swap=swap)
        log.info(f"   PAYLOAD: {payload}")

        ok = update_db(client, name, link_col, url, payload)
        if ok: done += 1
        else:  fail += 1

        time.sleep(DELAY)

    log.info(f"\n  {name}: Done={done}  Fail={fail}  Total={len(rows)}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — NON-CANCEL POLICY
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log.info("=" * 70)
    log.info(f"  MASTER FLIPKART UPDATER — {len(TABLES)} tables | Keys: {len(SCRAPERAPI_KEYS)}")
    log.info("  NON-CANCEL POLICY — runs until all tables complete")
    log.info("=" * 70)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    for cfg in TABLES:
        table_done = False
        attempt    = 0
        while not table_done:
            attempt += 1
            try:
                process_table(client, cfg)
                table_done = True
            except KeyboardInterrupt:
                log.warning("  KeyboardInterrupt ignored — NON-CANCEL policy.")
                table_done = True
            except Exception as exc:
                log.error(f"  ERROR in '{cfg['name']}' attempt {attempt}: {exc}")
                if attempt >= 3:
                    log.error(f"  Skipping '{cfg['name']}' after 3 attempts.")
                    table_done = True
                else:
                    log.info(f"  Retrying '{cfg['name']}'...")
                    time.sleep(5)

    log.info("\n" + "="*70)
    log.info("  ALL TABLES COMPLETE")
    log.info("="*70)


if __name__ == "__main__":
    main()

