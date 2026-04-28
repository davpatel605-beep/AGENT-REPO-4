"""
master_updater.py — Universal Flipkart Price Updater
=====================================================
Features:
  1. 6 ScraperAPI keys auto-rotation
  2. 7 tables with exact column mapping
  3. Column verification before processing (stops workflow if missing)
  4. Current Price, Original Price, Discount extraction
  5. Strict discount — only down arrow ↓ pattern (ignores bank offers)
  6. Math fallbacks — calculates missing field from other two
  7. Rating + Reviews (7 methods, MAX 5 lakh, Indian format validation)
  8. Credit saving — RENDER only if reviews/rating missing
  9. URL verification before DB update
 10. ₹ symbol + Indian number format

Tables: keybord → laptop → monitar → mouse → smart phone → smart+tv → smartwatch
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

# ── Credentials ───────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_KEY"].strip()

SCRAPERAPI_KEYS = []
for _i in ["", "_2", "_3", "_4", "_5", "_6"]:
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
MAX_REVIEWS     = 500000   # 5 lakh max realistic reviews


# ══════════════════════════════════════════════════════════════════════════════
# TABLE CONFIG — exact Supabase column names
# ══════════════════════════════════════════════════════════════════════════════
TABLES = [
    {
        "name": "keybord",
        "link": "Product Link",
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
# COLUMN VERIFICATION — stops workflow if column missing
# ══════════════════════════════════════════════════════════════════════════════
class ColumnNotFoundError(Exception):
    pass


def verify_columns(client: Client, table: str, cols: dict):
    try:
        result = client.table(table).select("*").limit(1).execute()
        if not result.data:
            log.warning(f"   [COL-CHECK] Table '{table}' empty — skipping check.")
            return
        actual = list(result.data[0].keys())
        for key, col_name in cols.items():
            if col_name not in actual:
                msg = (
                    f"\n{'!'*60}\n"
                    f"  COLUMN NOT FOUND — WORKFLOW STOPPED\n"
                    f"  Table   : {table}\n"
                    f"  Missing : '{col_name}'\n"
                    f"  Actual  : {actual}\n"
                    f"{'!'*60}"
                )
                log.error(msg)
                raise ColumnNotFoundError(f"'{col_name}' not in '{table}'")
        log.info(f"   [COL-CHECK] All columns verified OK for '{table}'")
    except ColumnNotFoundError:
        raise
    except Exception as exc:
        log.warning(f"   [COL-CHECK] Could not verify: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# FETCH — cheap (1 credit) or render (25 credits)
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
    """13902 → ₹13,902 (Indian format)"""
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
    return (v + "%") if v.isdigit() and 1 <= int(v) <= 99 else v

def valid_indian_review(raw: str) -> str:
    """
    Validate Indian number format for reviews.
    Valid: 34,452 | 2,19,513 | 1,00,258
    Invalid: 3,445,210 (US format) | numbers > 5 lakh
    Returns clean digit string if valid, else empty string.
    """
    clean = raw.replace(",", "")
    if not clean.isdigit():
        return ""
    val = int(clean)
    if val < 1 or val > MAX_REVIEWS:
        return ""
    # Validate Indian comma pattern
    if "," in raw:
        parts = raw.split(",")
        # First part: 1-3 digits, rest: exactly 2 digits each (Indian format)
        if len(parts[0]) > 3 or len(parts[0]) < 1:
            return ""
        for part in parts[1:]:
            if len(part) != 2:
                return ""
    return clean


# ══════════════════════════════════════════════════════════════════════════════
# EXTRACTORS
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
        if valid_price(v):
            return v
    cart = soup.find(string=re.compile(r"Add to cart", re.I))
    if cart:
        p = cart.find_parent("div")
        for _ in range(6):
            if not p:
                break
            prices = re.findall(r"₹\s*([\d,]+)", p.get_text())
            vlist  = sorted([int(x.replace(",","")) for x in prices
                             if valid_price(x.replace(",",""))])
            if vlist:
                return str(vlist[0])
            p = p.find_parent("div")
    return ""


def get_discount(soup, ft):
    """
    Strict: only down arrow ↓ = real product discount.
    Bank offers, cashback % are ignored.
    """
    m = re.search(r"[\u2193\u2198\u25bc\u2b07]\s*(\d{1,2})\s*%", ft)
    if m and 1 <= int(m.group(1)) <= 99:
        return m.group(1) + "%"
    tag = soup.select_one("div._1psv1zeb9._1psv1ze0._1psv1zedr")
    if tag:
        m = re.search(r"(\d{1,2})%", safe(tag))
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"
    for tag in soup.find_all(["div", "span"]):
        text = safe(tag).strip()
        if len(text) > 15:
            continue
        m = re.fullmatch(r"(\d{1,2})%\s*(off)?", text, re.I)
        if m and 1 <= int(m.group(1)) <= 99:
            return m.group(1) + "%"
    return ""


def get_original_price(soup, ft, cur, disc):
    def ok(v):
        if not v or not valid_price(v):
            return False
        return int(v) > int(cur) if cur and cur.isdigit() else True

    # Math first (5% tolerance, only ₹ prices)
    if cur and cur.isdigit() and disc:
        d = disc.replace("%","").strip()
        if d.isdigit() and 1 <= int(d) <= 99:
            exp = int(cur) / (1 - int(d)/100)
            best_v, best_diff = "", float("inf")
            for p in re.findall(r"₹\s*([\d,]+)", ft):
                v = p.replace(",","")
                if not v.isdigit() or not valid_price(v) or int(v) <= int(cur):
                    continue
                diff = abs(int(v) - exp) / exp
                if diff < best_diff and diff <= 0.05:
                    best_diff, best_v = diff, v
            if best_v:
                log.info(f"   [MATH] orig={best_v}  diff={best_diff:.2%}")
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

    # <s> strikethrough tag
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

    # Number just before current price in text
    if cur:
        pos = ft.find(cur)
        if pos > 30:
            for c in reversed(re.findall(r"₹\s*([\d,]+)", ft[max(0,pos-150):pos])):
                v = c.replace(",","")
                if ok(v): return v
    return ""


def get_rating_reviews(soup, ft):
    """
    Pattern: "4.6 ★ | 2,19,513"
    Reviews validated with Indian number format + MAX 5 lakh.
    7 methods in order of reliability.
    """
    rating     = ""
    reviews    = ""
    rating_tag = None

    # ── Rating ────────────────────────────────────────────────────────────────
    for tag in soup.find_all(["div","span"]):
        t = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.[0-9]", t):
            rating, rating_tag = t, tag
            break
    if not rating:
        m = re.search(r"([1-5]\.[0-9])\s*[★✩⭐|]", ft)
        if m: rating = m.group(1)

    # ── Method 1: inline "4.6 ★ | 34,452" ───────────────────────────────────
    if rating:
        for tag in soup.find_all(["div","span"]):
            t = safe(tag).strip()
            m = re.search(r"[1-5]\.[0-9]\s*[★✩⭐]?\s*\|\s*([\d,]+)", t)
            if m:
                v = valid_indian_review(m.group(1))
                if v:
                    reviews = v
                    log.info(f"   [M1-INLINE] reviews={v}")
                    break

    # ── Method 2: rating tag → parent → split by | ───────────────────────────
    if not reviews and rating_tag:
        node = rating_tag
        for level in range(5):
            node = node.parent
            if not node: break
            nt = safe(node).strip()
            if "|" not in nt or rating not in nt: continue
            parts = nt.split("|")
            if len(parts) >= 2:
                after = parts[-1].strip()
                v = valid_indian_review(after)
                if v:
                    reviews = v
                    log.info(f"   [M2-SPLIT] reviews={v}")
                    break

    # ── Method 3: | pipe text node → next sibling ────────────────────────────
    if not reviews:
        for pipe in soup.find_all(string=re.compile(r"^\s*\|\s*$")):
            nxt = pipe.find_next(["span","div"])
            if nxt:
                raw = safe(nxt).strip()
                v = valid_indian_review(raw)
                if v:
                    reviews = v
                    log.info(f"   [M3-PIPE] reviews={v}")
                    break

    # ── Method 4: full text pattern ───────────────────────────────────────────
    if not reviews and rating:
        for pat in [
            re.escape(rating) + r"\s*[★✩⭐]\s*\|\s*([\d,]+)",
            re.escape(rating) + r"\s*\|\s*([\d,]+)",
        ]:
            m = re.search(pat, ft)
            if m:
                v = valid_indian_review(m.group(1))
                if v:
                    reviews = v
                    log.info(f"   [M4-TEXT] reviews={v}")
                    break

    # ── Method 5: rating tag siblings ────────────────────────────────────────
    if not reviews and rating_tag:
        node = rating_tag
        for level in range(4):
            node = node.parent
            if not node: break
            for child in node.descendants:
                t = child.get_text(strip=True) if hasattr(child,"get_text") else str(child).strip()
                if t and t != rating and t not in ["★","✩","⭐","|"]:
                    v = valid_indian_review(t)
                    if v:
                        reviews = v
                        log.info(f"   [M5-SIBLING] reviews={v}")
                        break
            if reviews: break

    # ── Method 6: CSS selectors ───────────────────────────────────────────────
    if not reviews:
        for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zegu",
                    "span.Wphh3N","span._2_R_DZ","span._13vcmD"]:
            tag = soup.select_one(sel)
            if tag:
                for raw in re.findall(r"[\d,]+", safe(tag)):
                    v = valid_indian_review(raw)
                    if v:
                        reviews = v
                        log.info(f"   [M6-CSS] reviews={v}")
                        break
            if reviews: break

    # ── Method 7: keyword patterns ────────────────────────────────────────────
    if not reviews:
        for pat in [r"([\d,]+[kK]?)\s+[Rr]ating",
                    r"([\d,]+[kK]?)\s+[Rr]eview",
                    r"based on\s+([\d,]+[kK]?)\s+rating"]:
            m = re.search(pat, ft, re.I)
            if m:
                v = valid_indian_review(parse_k(m.group(1)))
                if v:
                    reviews = v
                    log.info(f"   [M7-KEYWORD] reviews={v}")
                    break

    log.info(f"   Final: rating={rating}  reviews={reviews}")
    return rating, reviews


def math_fallbacks(cur, orig, disc):
    """Fill one missing field when other two are real scraped values."""
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
def build_payload(cols, cur, orig, disc, rating, reviews):
    p = {}
    if cur and "current_price" in cols:
        p[cols["current_price"]]  = fmt_price(cur)
    if orig and "original_price" in cols:
        p[cols["original_price"]] = fmt_price(orig)
    if disc and "discount" in cols:
        p[cols["discount"]]       = fmt_disc(disc)
    if "combined" in cols:
        if rating and reviews:
            p[cols["combined"]] = f"{rating} ★ | {reviews}"
        elif rating:
            p[cols["combined"]] = rating
    else:
        if rating and "rating" in cols:
            p[cols["rating"]]  = rating
        if reviews and "reviews" in cols:
            p[cols["reviews"]] = reviews
        if reviews and "reviews2" in cols:
            p[cols["reviews2"]] = reviews
    return p


# ══════════════════════════════════════════════════════════════════════════════
# DB UPDATE WITH URL VERIFICATION
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
        client.table(table).update(payload).eq(link_col, url).execute()
        log.info(f"   [OK] {payload}")
        return True
    except Exception as exc:
        log.error(f"   [DB] {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# PROCESS ONE TABLE
# ══════════════════════════════════════════════════════════════════════════════
def process_table(client, cfg):
    name, link_col, cols = cfg["name"], cfg["link"], cfg["cols"]
    log.info(f"\n{'═'*70}")
    log.info(f"  TABLE: {name.upper()}")
    log.info(f"{'═'*70}")

    rows = [r for r in client.table(name).select("*").execute().data
            if r.get(link_col,"").strip()]
    log.info(f"  {len(rows)} products.")

    # Verify columns before processing
    verify_columns(client, name, cols)

    done = fail = 0
    for idx, row in enumerate(rows, 1):
        url = row[link_col].strip()
        log.info(f"\n  [{idx}/{len(rows)}] {url[:80]}")

        cur = orig = disc = rating = reviews = ""

        # Pass 1: CHEAP (1 credit) — try everything
        soup1 = fetch(url, render=False)
        if soup1:
            ft1            = soup1.get_text(" ", strip=True)
            cur            = get_current_price(soup1, ft1)
            disc           = get_discount(soup1, ft1)
            orig           = get_original_price(soup1, ft1, cur, disc)
            rating,reviews = get_rating_reviews(soup1, ft1)
            log.info(f"   Pass1: cur={cur} disc={disc} orig={orig} "
                     f"rating={rating} reviews={reviews}")

        time.sleep(1)

        # Pass 2: RENDER (25 credits) — only if reviews or rating missing
        if not reviews or not rating:
            log.info("   Pass2 (RENDER)...")
            soup2 = fetch(url, render=True)
            if soup2:
                ft2          = soup2.get_text(" ", strip=True)
                r2, rv2      = get_rating_reviews(soup2, ft2)
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

        payload = build_payload(cols, cur, orig, disc, rating, reviews)
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
    log.info("=" * 70)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    for cfg in TABLES:
        try:
            process_table(client, cfg)
        except ColumnNotFoundError as exc:
            log.error(f"\n{'='*70}")
            log.error(f"  WORKFLOW STOPPED: {exc}")
            log.error(f"  Fix the column name and re-run.")
            log.error(f"{'='*70}")
            raise SystemExit(1)
        except Exception as exc:
            log.error(f"  ERROR in '{cfg['name']}': {exc}")

    log.info("\n" + "="*70)
    log.info("  ALL TABLES COMPLETE")
    log.info("="*70)


if __name__ == "__main__":
    main()

