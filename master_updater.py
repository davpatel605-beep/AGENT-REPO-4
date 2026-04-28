"""
master_updater.py — Universal Flipkart Price Updater
All 11 tables in one run.

Strategy per product:
  Pass 1 (CHEAP  - 1 credit)  : Current Price + Discount + Original Price
  Pass 2 (RENDER - 25 credits): Rating + Reviews (ONLY if missing from Pass 1)

Math fallbacks (if scraping fails):
  cur + disc  -> orig = cur / (1 - disc/100)
  orig + disc -> cur  = orig * (1 - disc/100)
  cur + orig  -> disc = (orig - cur) / orig * 100

API Key rotation: auto-switches to next key on 401/403.

Environment Variables:
  SUPABASE_URL, SUPABASE_KEY
  SCRAPERAPI_KEY, SCRAPERAPI_KEY_2 ... SCRAPERAPI_KEY_6
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
for i in ["", "_2", "_3", "_4", "_5", "_6"]:
    k = os.environ.get(f"SCRAPERAPI_KEY{i}", "").strip()
    if k:
        SCRAPERAPI_KEYS.append(k)

current_key_idx = 0

def get_key():
    return SCRAPERAPI_KEYS[current_key_idx] if SCRAPERAPI_KEYS else ""

def rotate_key():
    global current_key_idx
    if current_key_idx < len(SCRAPERAPI_KEYS) - 1:
        current_key_idx += 1
        log.warning(f"   [KEY] Rotated to key #{current_key_idx + 1}")
        return True
    log.error("   [KEY] All keys exhausted!")
    return False

ENDPOINT       = "https://api.scraperapi.com/"
REQUEST_TIMEOUT = 90
DELAY           = 1


# ══════════════════════════════════════════════════════════════════════════════
# TABLE CONFIGS
# Defines column mapping for each table.
# Keys: current_price, original_price, discount, rating, reviews, combined, link
# "combined" = Rating and Reviews stored in ONE column as "4.1 ★ | 239"
# ══════════════════════════════════════════════════════════════════════════════
TABLES = [
    {
        "name": "gaming cpu",
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
        "name": "gaming pc",
        "link": "Product Link",
        "cols": {
            "current_price":  "price",
            "original_price": "Original Price-2",
            "discount":       "Discount-2",
            "rating":         "Product Rating",
            "reviews":        "product review",
        },
    },
    {
        "name": "induction",
        "link": "Product Link",
        "cols": {
            "current_price":  "Price",
            "original_price": "Discount Price",
            "discount":       "Discount percentage",
            "rating":         "Rating",
            "reviews":        "Number of reviews",
        },
    },
    {
        "name": "iphone",
        "link": "Product URL",
        "cols": {
            "current_price":  "Price",
            "original_price": "Discounted Price",
            "discount":       "Discount Percentage",
            "rating":         "Product Rating",
            "reviews":        "Number of Reviews",
            "reviews2":       "Number of Rating",
        },
    },
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
            "combined":       "Rating and Reviews",
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
            log.warning(f"   [{mode}] Key limit — rotating...")
            if rotate_key():
                return fetch(url, render)
            return None
        resp.raise_for_status()
        log.info(f"   [{mode}] HTTP {resp.status_code}  bytes={len(resp.text)}")
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
    """Convert '13902' -> '₹13,902'"""
    if not v or not v.isdigit():
        return v
    n, s = int(v), str(int(v))
    if len(s) <= 3:
        return f"₹{s}"
    result, s = s[-3:], s[:-3]
    while s:
        result, s = s[-2:] + "," + result, s[:-2]
    return f"₹{result.lstrip(',')}"

def fmt_disc(v):
    v = v.replace("%", "").strip()
    return (v + "%") if v.isdigit() and 1 <= int(v) <= 99 else v


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
            vlist  = sorted([int(x.replace(",","")) for x in prices if valid_price(x.replace(",",""))])
            if vlist:
                return str(vlist[0])
            p = p.find_parent("div")
    return ""


def get_discount(soup, ft):
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
        if len(text) > 20:
            continue
        m = re.search(r"(\d{1,2})%\s*(off)?$", text, re.I)
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
            expected = int(cur) / (1 - int(d)/100)
            best_v, best_diff = "", float("inf")
            for p in re.findall(r"₹\s*([\d,]+)", ft):
                v = p.replace(",","")
                if not v.isdigit() or not valid_price(v) or int(v) <= int(cur):
                    continue
                diff = abs(int(v) - expected) / expected
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
    for sel in ["div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
                "div.yRaY8j.ZYYwLA","div.yRaY8j","div._3I9_wc._2p6lqe","div._3I9_wc"]:
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
            window = ft[max(0,pos-150):pos]
            for c in reversed(re.findall(r"₹\s*([\d,]+)", window)):
                v = c.replace(",","")
                if ok(v): return v
    return ""


def get_rating_and_reviews(soup, ft):
    rating, reviews = "", ""

    # Rating: find exact decimal tag
    for tag in soup.find_all(["div","span"]):
        t = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.\d", t):
            rating = t
            break
    if not rating:
        m = re.search(r"([1-5]\.\d)\s*[★✩⭐|]", ft)
        if m: rating = m.group(1)

    # Reviews — DOM parent method (most reliable)
    if rating:
        for tag in soup.find_all(["div","span"]):
            if safe(tag).strip() == rating:
                parent = tag.parent
                if parent:
                    for child in parent.descendants:
                        t = child.get_text(strip=True) if hasattr(child,"get_text") else str(child).strip()
                        if t and t != rating and t not in ["★","✩","⭐","|",""]:
                            v = t.replace(",","")
                            if v.isdigit() and int(v) >= 1:
                                reviews = v
                                log.info(f"   [DOM-PARENT] reviews={v}")
                                break
                if reviews: break

    # Reviews — pipe | next sibling
    if not reviews:
        for pipe in soup.find_all(string=re.compile(r"^\s*\|\s*$")):
            nxt = pipe.find_next(["span","div"])
            if nxt:
                v = to_num(safe(nxt))
                if v.isdigit() and int(v) >= 1:
                    reviews = v
                    log.info(f"   [PIPE-NEXT] reviews={v}")
                    break

    # Reviews — inline pattern "4.1 ★ | 239"
    if not reviews:
        for tag in soup.find_all(["div","span"]):
            t = safe(tag).strip()
            m = re.search(r"[1-5]\.\d\s*[★✩⭐]?\s*\|\s*([\d,]+)", t)
            if m:
                v = m.group(1).replace(",","")
                if v.isdigit() and int(v) >= 1:
                    reviews = v
                    log.info(f"   [INLINE] reviews={v}")
                    break

    # Reviews — full text patterns
    if not reviews and rating:
        for pat in [
            re.escape(rating) + r"\s*[★✩⭐]\s*\|\s*([\d,]+)",
            re.escape(rating) + r"\s*\|\s*([\d,]+)",
            re.escape(rating) + r"[^\d]{1,8}([\d,]+)",
        ]:
            m = re.search(pat, ft)
            if m:
                v = m.group(1).replace(",","")
                if v.isdigit() and int(v) >= 1:
                    reviews = v
                    break

    # Reviews — CSS selectors
    if not reviews:
        for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zegu",
                    "span.Wphh3N","span._2_R_DZ","span._13vcmD"]:
            tag = soup.select_one(sel)
            if tag:
                for n in re.findall(r"[\d,]+", safe(tag)):
                    v = n.replace(",","")
                    if v.isdigit() and int(v) >= 1:
                        reviews = v
                        break
            if reviews: break

    # Reviews — keyword patterns
    if not reviews:
        for pat in [r"([\d,]+[kK]?)\s+[Rr]ating",
                    r"([\d,]+[kK]?)\s+[Rr]eview",
                    r"based on\s+([\d,]+[kK]?)\s+rating"]:
            m = re.search(pat, ft, re.I)
            if m:
                v = parse_k(m.group(1))
                if v.isdigit() and int(v) >= 1:
                    reviews = v
                    break

    return rating, reviews


def math_fallbacks(cur, orig, disc):
    if cur and disc and not orig:
        d = disc.replace("%","").strip()
        if d.isdigit() and cur.isdigit() and 1 <= int(d) <= 99:
            orig = str(round(int(cur)/(1 - int(d)/100)))
            log.info(f"   [MATH] orig={orig}")
    if orig and disc and not cur:
        d = disc.replace("%","").strip()
        if d.isdigit() and orig.isdigit() and 1 <= int(d) <= 99:
            cur = str(round(int(orig)*(1 - int(d)/100)))
            log.info(f"   [MATH] cur={cur}")
    if cur and orig and not disc:
        if cur.isdigit() and orig.isdigit() and int(orig) > int(cur):
            d = round((int(orig)-int(cur))/int(orig)*100)
            if 1 <= d <= 99:
                disc = str(d) + "%"
                log.info(f"   [MATH] disc={disc}")
    return cur, orig, disc


# ══════════════════════════════════════════════════════════════════════════════
# BUILD DB PAYLOAD
# ══════════════════════════════════════════════════════════════════════════════
def build_payload(cols, cur, orig, disc, rating, reviews):
    payload = {}

    if cur and "current_price" in cols:
        payload[cols["current_price"]]  = fmt_price(cur)
    if orig and "original_price" in cols:
        payload[cols["original_price"]] = fmt_price(orig)
    if disc and "discount" in cols:
        payload[cols["discount"]]       = fmt_disc(disc)
    if "combined" in cols:
        # "4.1 ★ | 239" format
        if rating and reviews:
            payload[cols["combined"]] = f"{rating} ★ | {reviews}"
        elif rating:
            payload[cols["combined"]] = rating
    else:
        if rating and "rating" in cols:
            payload[cols["rating"]]   = rating
        if reviews and "reviews" in cols:
            payload[cols["reviews"]]  = reviews
        if reviews and "reviews2" in cols:
            payload[cols["reviews2"]] = reviews

    return payload


# ══════════════════════════════════════════════════════════════════════════════
# DB UPDATE WITH URL VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════
def update_db(client, table_name, link_col, url, payload):
    if not payload:
        log.warning("   Empty payload — skipping.")
        return False
    try:
        # Verify URL exists
        check = client.table(table_name).select(link_col).eq(link_col, url).execute()
        if not check.data:
            clean = url.strip().rstrip("/")
            check2 = client.table(table_name).select(link_col).eq(link_col, clean).execute()
            if check2.data:
                url = clean
                log.info("   [URL-FIX] Matched cleaned URL")
            else:
                log.error(f"   [URL-NOT-FOUND] {url[:70]}")
                return False

        result = client.table(table_name).update(payload).eq(link_col, url).execute()
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

    rows = client.table(name).select("*").execute().data
    rows = [r for r in rows if r.get(link_col,"").strip()]
    log.info(f"  {len(rows)} products.")

    done = fail = 0
    for idx, row in enumerate(rows, 1):
        url = row[link_col].strip()
        log.info(f"\n  [{idx}/{len(rows)}] {url[:80]}")

        cur = orig = disc = rating = reviews = ""

        # PASS 1: CHEAP — all fields
        soup1 = fetch(url, render=False)
        if soup1:
            ft1              = soup1.get_text(" ", strip=True)
            cur              = get_current_price(soup1, ft1)
            disc             = get_discount(soup1, ft1)
            orig             = get_original_price(soup1, ft1, cur, disc)
            rating, reviews  = get_rating_and_reviews(soup1, ft1)
            log.info(f"   Pass1: cur={cur} disc={disc} orig={orig} rating={rating} reviews={reviews}")

        time.sleep(1)

        # PASS 2: RENDER — only if reviews or rating missing
        if not reviews or not rating:
            log.info("   Pass2 (RENDER) — fetching for reviews/rating...")
            soup2 = fetch(url, render=True)
            if soup2:
                ft2             = soup2.get_text(" ", strip=True)
                r2, rv2         = get_rating_and_reviews(soup2, ft2)
                if not rating:   rating  = r2
                if not reviews:  reviews = rv2
                if not cur:      cur     = get_current_price(soup2, ft2)
                if not disc:     disc    = get_discount(soup2, ft2)
                if not orig:     orig    = get_original_price(soup2, ft2, cur, disc)
                log.info(f"   Pass2: rating={rating} reviews={reviews}")
        else:
            log.info("   Pass2 skipped — data complete ✅")

        # Math fallbacks
        cur, orig, disc = math_fallbacks(cur, orig, disc)

        # Sanity
        if cur and orig and cur.isdigit() and orig.isdigit():
            if int(cur) >= int(orig):
                log.warning(f"   SANITY: cur({cur}) >= orig({orig}) — clearing orig")
                orig = ""

        payload = build_payload(cols, cur, orig, disc, rating, reviews)
        log.info(f"   PAYLOAD: {payload}")

        ok = update_db(client, name, link_col, url, payload)
        if ok: done += 1
        else:  fail += 1

        time.sleep(DELAY)

    log.info(f"\n  {name}: Updated={done}  Failed={fail}  Total={len(rows)}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log.info("=" * 70)
    log.info(f"  MASTER FLIPKART UPDATER — {len(TABLES)} tables")
    log.info(f"  API Keys: {len(SCRAPERAPI_KEYS)}")
    log.info("=" * 70)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    for cfg in TABLES:
        try:
            process_table(client, cfg)
        except Exception as exc:
            log.error(f"  ERROR in table '{cfg['name']}': {exc}")

    log.info("\n" + "=" * 70)
    log.info("  ALL TABLES COMPLETE")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
