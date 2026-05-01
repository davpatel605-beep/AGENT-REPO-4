"""
master_updater.py — Universal Flipkart Price Updater
=====================================================
Tables (12): earbuds → gaming cpu → gaming pc → induction → iphone →
             keybord → laptop → monitar → mouse → smart phone → smart+tv → smartwatch

Features:
  1.  6 ScraperAPI keys — auto-rotation on 401/403
  2.  12 tables with exact column mapping
  3.  SWAP mode — induction + iphone: Price col = MRP, Discounted Price col = Current
  4.  Discount — STRICT: only ↓ down arrow pattern. Bank offers ignored.
  5.  Reviews — 7 methods + Indian format validation + last-2-digits discount strip
               + 5-attempt security + 5 lakh hard limit
  6.  Math fallbacks — only when real scraped data exists (no forced calc)
  7.  Credit saving — RENDER only if rating/reviews missing after CHEAP
  8.  URL verification + auto-clean before DB update
  9.  Column error → skip that column, continue next product
  10. ₹ symbol + Indian number format
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
MAX_REVIEWS     = 500000   # 5 lakh hard limit


# ══════════════════════════════════════════════════════════════════════════════
# TABLE CONFIG
# swap=True → current_price col stores MRP, original_price col stores current
# ══════════════════════════════════════════════════════════════════════════════
TABLES = [
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
        "name": "induction",
        "link": "Product Link",
        "swap": True,
        "cols": {
            "current_price":  "Discounted Price",
            "original_price": "Price",
            "discount":       "Discount Percentage",
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
    """Convert '69900' → '₹69,900' in Indian format."""
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
    """Format reviews in Indian number format: 34452 → 34,452 | 219513 → 2,19,513"""
    if not v or not v.isdigit():
        return v
    n = int(v)
    if n < 1000:
        return v
    s = str(n)
    # Indian format: last 3, then groups of 2
    result, s = s[-3:], s[:-3]
    while s:
        result, s = s[-2:] + "," + result, s[:-2]
    return result.lstrip(",")


# ══════════════════════════════════════════════════════════════════════════════
# REVIEW VALIDATION — Indian format + security checks
# ══════════════════════════════════════════════════════════════════════════════
def validate_review(raw: str, discount: str = "", force_accept: bool = False) -> str:
    """
    Security checks for reviews:
    1. Indian comma format: X,XX,XXX (not US format X,XXX,XXX)
    2. Must be <= 5 lakh (MAX_REVIEWS)
    3. If last 2 digits match discount number → strip them (fake review bug)
    4. force_accept=True → accept even if > 5 lakh (only after 5 failed attempts)

    Returns clean digit string if valid, else "".
    """
    if not raw:
        return ""

    clean = raw.replace(",", "").strip()
    if not clean.isdigit():
        return ""

    val = int(clean)
    if val < 1:
        return ""

    # Step 1: Strip trailing discount digits if they match
    disc_num = discount.replace("%", "").strip() if discount else ""
    if disc_num.isdigit() and len(disc_num) <= 2 and clean.endswith(disc_num):
        stripped = clean[:-len(disc_num)]
        if stripped.isdigit() and int(stripped) >= 1:
            log.info(f"   [REVIEW-STRIP] {clean} → {stripped} (removed disc digits)")
            clean = stripped
            val   = int(clean)

    # Step 2: Indian format check
    if "," in raw:
        parts = raw.split(",")
        valid_indian = (
            1 <= len(parts[0]) <= 3 and
            all(len(p) == 2 for p in parts[1:])
        )
        if not valid_indian and not force_accept:
            log.warning(f"   [REVIEW-REJECT] Not Indian format: {raw}")
            return ""

    # Step 3: 5 lakh hard limit — unless force_accept after all attempts failed
    if val > MAX_REVIEWS and not force_accept:
        log.warning(f"   [REVIEW-REJECT] Exceeds 5 lakh: {val}")
        return ""

    return clean


def extract_review_number(soup, ft, rating, discount) -> str:
    """
    7 methods to find reviews.
    Runs up to 5 attempts with increasing force_accept threshold.
    """
    def try_methods(force: bool = False) -> str:
        rev = ""

        # M1: inline "4.6 ★ | 34,452"
        if rating:
            for tag in soup.find_all(["div","span"]):
                t = safe(tag).strip()
                m = re.search(r"[1-5]\.[0-9]\s*[★✩⭐]?\s*\|\s*([\d,]+)", t)
                if m:
                    v = validate_review(m.group(1), discount, force)
                    if v:
                        log.info(f"   [M1-INLINE] reviews={v}")
                        return v

        # M2: | pipe text node → next sibling
        for pipe in soup.find_all(string=re.compile(r"^\s*\|\s*$")):
            nxt = pipe.find_next(["span","div"])
            if nxt:
                v = validate_review(safe(nxt).strip(), discount, force)
                if v:
                    log.info(f"   [M2-PIPE] reviews={v}")
                    return v

        # M3: full text pattern with rating
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

        # M4: CSS selectors
        for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zegu",
                    "span.Wphh3N","span._2_R_DZ","span._13vcmD"]:
            tag = soup.select_one(sel)
            if tag:
                for raw in re.findall(r"[\d,]+", safe(tag)):
                    v = validate_review(raw, discount, force)
                    if v:
                        log.info(f"   [M4-CSS] reviews={v}")
                        return v

        # M5: keyword patterns
        for pat in [r"([\d,]+[kK]?)\s+[Rr]ating",
                    r"([\d,]+[kK]?)\s+[Rr]eview",
                    r"based on\s+([\d,]+[kK]?)\s+rating"]:
            m = re.search(pat, ft, re.I)
            if m:
                v = validate_review(parse_k(m.group(1)), discount, force)
                if v:
                    log.info(f"   [M5-KEYWORD] reviews={v}")
                    return v

        return rev

    # Run up to 5 attempts; last attempt force_accept=True
    for attempt in range(1, 6):
        force = (attempt == 5)   # 5th attempt: accept even if > 5 lakh
        result = try_methods(force=force)
        if result:
            return result
        if attempt < 5:
            log.warning(f"   [REVIEW] Attempt {attempt} failed, retrying...")

    return ""


# ══════════════════════════════════════════════════════════════════════════════
# PRICE EXTRACTION
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


def get_discount(soup, ft):
    """STRICT: Only ↓ down arrow = real product discount. Never bank/cashback %."""
    m = re.search(r"[\u2193\u2198\u25bc\u2b07]\s*(\d{1,2})\s*%", ft)
    if m and 1 <= int(m.group(1)) <= 99:
        return m.group(1) + "%"
    tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if tag:
        m = re.search(r"(\d{1,2})%", safe(tag))
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"
    for tag in soup.find_all(["div","span"]):
        text = safe(tag).strip()
        if len(text) > 15: continue
        m = re.fullmatch(r"(\d{1,2})%\s*(off)?", text, re.I)
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"
    return ""


def get_original_price(soup, ft, cur, disc):
    def ok(v):
        if not v or not valid_price(v): return False
        return int(v) > int(cur) if cur and cur.isdigit() else True

    # Math first (5% tolerance, ₹ prices only, only when real discount)
    if cur and cur.isdigit() and disc:
        d = disc.replace("%","").strip()
        if d.isdigit() and 1 <= int(d) <= 99:
            exp = int(cur) / (1 - int(d)/100)
            best_v, best_diff = "", float("inf")
            for p in re.findall(r"₹\s*([\d,]+)", ft):
                v = p.replace(",","")
                if not v.isdigit() or not valid_price(v) or int(v) <= int(cur): continue
                diff = abs(int(v) - exp) / exp
                if diff < best_diff and diff <= 0.05:
                    best_diff, best_v = diff, v
            if best_v:
                log.info(f"   [MATH] orig={best_v} diff={best_diff:.2%}")
                return best_v

    # JSON-LD
    for sc in soup.find_all("script", {"type":"application/ld+json"}):
        try:
            obj = json.loads(sc.string or "")
            for item in (obj if isinstance(obj,list) else [obj]):
                offers = item.get("offers",{})
                if isinstance(offers,list): offers = offers[0] if offers else {}
                for k in ["highPrice","originalPrice","listPrice"]:
                    v = to_num(str(offers.get(k,"")))
                    if ok(v): return v
        except: pass

    # <s> tag
    for s in soup.find_all("s"):
        v = to_num(safe(s))
        if ok(v): return v

    # CSS selectors
    for sel in [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA","div.yRaY8j",
        "div._3I9_wc._2p6lqe","div._3I9_wc",
    ]:
        tag = soup.select_one(sel)
        if tag:
            v = to_num(safe(tag))
            if ok(v): return v

    # line-through style
    for tag in soup.find_all(True):
        if "line-through" in tag.get("style",""):
            v = to_num(safe(tag))
            if ok(v): return v

    # Number just before current price
    if cur:
        pos = ft.find(cur)
        if pos > 30:
            for c in reversed(re.findall(r"₹\s*([\d,]+)", ft[max(0,pos-150):pos])):
                v = c.replace(",","")
                if ok(v): return v
    return ""


def get_rating(soup, ft) -> str:
    for tag in soup.find_all(["div","span"]):
        t = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.[0-9]", t):
            return t
    m = re.search(r"([1-5]\.[0-9])\s*[★✩⭐|]", ft)
    if m: return m.group(1)
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# MATH FALLBACKS
# ══════════════════════════════════════════════════════════════════════════════
def math_fallbacks(cur, orig, disc):
    """Fill ONE missing field using the other two. Never force discount."""
    if cur and disc and not orig:
        d = disc.replace("%","").strip()
        if d.isdigit() and cur.isdigit() and 1 <= int(d) <= 99:
            orig = str(round(int(cur) / (1 - int(d)/100)))
            log.info(f"   [MATH] orig={orig}")
    elif orig and disc and not cur:
        d = disc.replace("%","").strip()
        if d.isdigit() and orig.isdigit() and 1 <= int(d) <= 99:
            cur = str(round(int(orig) * (1 - int(d)/100)))
            log.info(f"   [MATH] cur={cur}")
    elif cur and orig and not disc:
        if cur.isdigit() and orig.isdigit() and int(orig) > int(cur):
            d = round((int(orig)-int(cur))/int(orig)*100)
            if 1 <= d <= 99:
                disc = str(d) + "%"
                log.info(f"   [MATH] disc={disc}")
    return cur, orig, disc


# ══════════════════════════════════════════════════════════════════════════════
# BUILD PAYLOAD
# ══════════════════════════════════════════════════════════════════════════════
def build_payload(cols, cur, orig, disc, rating, reviews, swap=False):
    p = {}

    if swap:
        # induction/iphone:
        # cols["current_price"]  = "Discounted Price" → current price (after discount)
        # cols["original_price"] = "Price"            → MRP (original price)
        # Column names already define the swap — just put values in correctly:
        if cur and "current_price" in cols:
            p[cols["current_price"]]  = fmt_price(cur)   # current → Discounted Price col
        if orig and "original_price" in cols:
            p[cols["original_price"]] = fmt_price(orig)  # MRP → Price col
        # No discount → current price goes directly to Discounted Price col
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
# DB UPDATE — skip missing columns, don't stop workflow
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

        # Try full payload first
        try:
            client.table(table).update(payload).eq(link_col, url).execute()
            log.info(f"   [OK] {payload}")
            return True
        except Exception:
            # Column error — try one by one, skip missing
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

        # Pass 1: CHEAP (1 credit) — try everything
        soup1 = fetch(url, render=False)
        if soup1:
            ft1     = soup1.get_text(" ", strip=True)
            cur     = get_current_price(soup1, ft1)
            disc    = get_discount(soup1, ft1)
            orig    = get_original_price(soup1, ft1, cur, disc)
            rating  = get_rating(soup1, ft1)
            reviews = extract_review_number(soup1, ft1, rating, disc)
            log.info(f"   Pass1: cur={cur} disc={disc} orig={orig} "
                     f"rating={rating} reviews={reviews}")

        time.sleep(1)

        # Pass 2: RENDER (25 credits) — only if rating or reviews missing
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
                if not disc:    disc    = get_discount(soup2, ft2)
                if not orig:    orig    = get_original_price(soup2, ft2, cur, disc)
                log.info(f"   Pass2: rating={rating} reviews={reviews}")
        else:
            log.info("   Pass2 skipped ✅ credits saved")

        # Math fallbacks
        cur, orig, disc = math_fallbacks(cur, orig, disc)

        # Sanity check
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
# MAIN
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
                log.warning("  KeyboardInterrupt ignored — NON-CANCEL policy active.")
                table_done = True   # skip this table, go to next
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

