"""
iphone_updater.py — Separate iPhone Price Updater (TEST VERSION)
=================================================================

iPhone Flipkart page ke 2 patterns:

PATTERN 1 — Discount wala iPhone:
  ┌─────────────────────────────────────────┐
  │  ↓ 8%   ₹74,900   ₹68,900             │
  │  (green) (strikethrough) (current)      │
  └─────────────────────────────────────────┘

PATTERN 2 — No discount iPhone:
  ┌─────────────────────────────────────────┐
  │  ₹69,900  ← sirf current price          │
  │  (no arrow, no strikethrough)           │
  └─────────────────────────────────────────┘

iPhone Table Columns (SWAP = True):
  "Discounted Price" = current price  (selling price)
  "Price"            = original/MRP   (sirf discount hone par)
  "Discount Percentage" = discount %  (sirf discount hone par)
  "Product Rating"   = rating
  "Number of Reviews" + "Number of Ratings" = reviews

DISCOUNT RULE:
  - Bank offers IGNORE karo (HDFC, SBI, Axis, 10% off on card etc.)
  - Sirf ACTUAL product discount lo (strikethrough price + % badge)
  - Agar page pe actual product discount nahi → disc="" → orig="" bhi ""
"""

import os, re, time, logging, requests
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

# iPhone table config
IPHONE_CFG = {
    "name":     "iphone",
    "link":     "Product URL",
    "swap":     True,
    "cols": {
        "current_price":  "Discounted Price",
        "original_price": "Price",
        "discount":       "Discount Percentage",
        "rating":         "Product Rating",
        "reviews":        "Number of Reviews",
        "reviews2":       "Number of Ratings",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def safe(tag, d=""):
    return tag.get_text(strip=True) if tag else d


def to_num(t):
    return re.sub(r"[^\d]", "", str(t)).strip()


def valid_price(v):
    return str(v).isdigit() and 100 <= int(v) <= 5000000


def parse_k(t):
    t = t.strip().replace(",", "")
    m = re.match(r"([\d.]+)[kK]", t)
    if m:
        return str(int(float(m.group(1)) * 1000))
    m = re.match(r"(\d+)", t)
    return m.group(1) if m else ""


def fmt_price(v):
    if not v or not str(v).isdigit():
        return v
    s = str(int(v))
    if len(s) <= 3:
        return f"₹{s}"
    result, s = s[-3:], s[:-3]
    while s:
        result, s = s[-2:] + "," + result, s[:-2]
    return f"₹{result.lstrip(',')}"


def fmt_disc(v):
    v = str(v).replace("%", "").strip()
    return (v + "%") if v.isdigit() and 1 <= int(v) <= 99 else ""


def fmt_reviews(v):
    if not v or not str(v).isdigit():
        return v
    n = int(v)
    if n < 1000:
        return str(n)
    s = str(n)
    result, s = s[-3:], s[:-3]
    while s:
        result, s = s[-2:] + "," + result, s[:-2]
    return result.lstrip(",")


# ══════════════════════════════════════════════════════════════════════════════
# FETCH
# ══════════════════════════════════════════════════════════════════════════════
def fetch(url: str, render: bool = False):
    key = get_key()
    if not key:
        return None
    params = {"api_key": key, "url": url, "country_code": "in"}
    if render:
        params["premium"] = "true"
        params["render"]  = "true"
    mode = "RENDER" if render else "CHEAP"
    try:
        resp = requests.get(
            f"{ENDPOINT}?{urlencode(params)}", timeout=REQUEST_TIMEOUT
        )
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
# CURRENT PRICE
# ══════════════════════════════════════════════════════════════════════════════
def get_current_price(soup, ft):
    for sel in [
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
        "div.Nx9bqj.CxhGGd",
        "div.Nx9bqj",
        "div._30jeq3._16Jk6d",
        "div._30jeq3",
        "div.CEmiEU",
    ]:
        tag = soup.select_one(sel)
        if tag:
            v = to_num(safe(tag))
            if v and valid_price(v):
                return v
    cart = soup.find(string=re.compile(r"Add to cart", re.I))
    if cart:
        p = cart.find_parent("div")
        for _ in range(6):
            if not p:
                break
            prices = re.findall(r"₹\s*([\d,]+)", p.get_text())
            vlist  = sorted(
                [int(x.replace(",", "")) for x in prices
                 if valid_price(x.replace(",", ""))]
            )
            if vlist:
                return str(vlist[0])
            p = p.find_parent("div")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# IPHONE DISCOUNT — STRICT VERSION
#
# iPhone pages mein BANK OFFERS bahut hote hain:
#   "15% off on HDFC Bank Credit Card"
#   "10% off on SBI Debit Card"
#   "5% cashback on Axis Bank"
#
# In sab ko IGNORE karna hai.
#
# ACTUAL product discount tab hota hai jab:
#   1. Strikethrough price page pe ho (original price kata hua dikhta hai)
#   2. Discount badge clearly price ke SAATH ho (not in offers section)
#
# STRATEGY:
#   Step 1: Page pe strikethrough price hai ya nahi check karo
#           Agar nahi → disc="" (no discount on this product)
#   Step 2: Strikethrough price mile → uske paas wala % value = actual discount
#           Uski context mein bank keywords nahi hone chahiye
#   Step 3: Agar strikethrough mile lekin % nahi → calc karo cur+orig se
#
# Bank keywords list — EXTENDED for iPhone pages:
# ══════════════════════════════════════════════════════════════════════════════
def get_iphone_discount(soup, ft, cur):
    """
    iPhone ke liye STRICT discount extraction.

    IMAGE 1 (discount wala):
      ↓74%   4,̶9̶9̶9̶   ₹1,299
      Green arrow + % + strikethrough + current price

    IMAGE 2 (no discount):
      ₹79,900
      +₹220 Protect Promise Fee   ← LAST LINE — iske baad kuch nahi dekhna

    RULE:
      - "Protect Promise Fee" line tak hi dekhna hai
      - Uske baad jo bhi % aaye — IGNORE
      - Strikethrough price bhi usi boundary ke andar hona chahiye
      - Bank offers IGNORE
    """

    # ── BOUNDARY: "Protect Promise Fee" ke baad sab cut karo ─────────────────
    # Image 2 mein yahi last relevant line hai
    # Iske baad variant prices, bank offers, EMI sab aata hai — sab ignore
    BOUNDARY_PHRASES = [
        "protect promise fee",
        "protect promise",
        "add to cart",
        "buy now",
    ]
    ft_upper = ft
    for phrase in BOUNDARY_PHRASES:
        idx = ft.lower().find(phrase)
        if idx != -1:
            ft_upper = ft[:idx]
            log.info(f"   [iPHONE-BOUNDARY] Cut at '{phrase}' pos={idx}")
            break

    log.info(f"   [iPHONE-ZONE] Searching in first {len(ft_upper)} chars of page")

    # ── BANK keywords ─────────────────────────────────────────────────────────
    BANK_KW = [
        "bank", "credit", "debit", "hdfc", "sbi", "axis", "icici",
        "cashback", "upi", "emi", "kotak", "rbl", "paytm", "rupay",
        "no cost", "instant", "additional", "card", "off on",
        "on hdfc", "on sbi", "on axis", "on icici", "on kotak",
        "on rbl", "on paytm", "on rupay", "flat ₹", "flat rs",
        "offer", "coupon", "voucher", "wallet",
    ]

    def has_bank(text):
        t = text.lower()
        return any(kw in t for kw in BANK_KW)

    cur_int = int(cur) if cur and cur.isdigit() else 0

    # ── Step 1: Strikethrough price BOUNDARY ke andar dhundho ────────────────
    # Agar strikethrough nahi mili → no product discount → return "", ""
    # Image 2: koi strikethrough nahi → ""
    # Image 1: 4,999 strikethrough → orig confirmed

    strikethrough_found = False
    strikethrough_val   = ""

    # Boundary wala soup banana — sirf relevant DOM nodes dekhna
    # Hum ft_upper (cut text) mein number dhundhte hain aur DOM se verify karte hain
    for tag in soup.find_all(["s", "del", "strike"]):
        v = to_num(safe(tag))
        if not v or not v.isdigit() or not valid_price(v):
            continue
        if cur_int > 0 and int(v) <= cur_int:
            continue
        # Check: kya yeh tag boundary ke andar hai?
        tag_text = safe(tag)
        if tag_text and tag_text in ft_upper:
            strikethrough_found = True
            strikethrough_val   = v
            log.info(f"   [iPHONE-STRIKE-IN-ZONE] {v}")
            break
        # Fallback: ft_upper mein number exist karta hai?
        if v in ft_upper.replace(",", ""):
            strikethrough_found = True
            strikethrough_val   = v
            log.info(f"   [iPHONE-STRIKE-NUM-FOUND] {v}")
            break

    if not strikethrough_found:
        for tag in soup.find_all(True):
            if "line-through" in tag.get("style", ""):
                v = to_num(safe(tag))
                if v and v.isdigit() and valid_price(v):
                    if cur_int == 0 or int(v) > cur_int:
                        if v in ft_upper.replace(",", "") or safe(tag) in ft_upper:
                            strikethrough_found = True
                            strikethrough_val   = v
                            log.info(f"   [iPHONE-LINETHRU-IN-ZONE] {v}")
                            break

    if not strikethrough_found:
        for sel in ["div.yRaY8j.ZYYwLA", "div.yRaY8j", "div._3I9_wc"]:
            tag = soup.select_one(sel)
            if tag:
                v = to_num(safe(tag))
                if v and v.isdigit() and valid_price(v):
                    if cur_int == 0 or int(v) > cur_int:
                        if v in ft_upper.replace(",", ""):
                            strikethrough_found = True
                            strikethrough_val   = v
                            log.info(f"   [iPHONE-MRP-CSS-IN-ZONE] {v}")
                            break

    if not strikethrough_found:
        log.info("   [iPHONE-DISC] No strikethrough in zone → no discount → ''")
        return "", ""

    # ── Step 2: Strikethrough mila → % dhundho BOUNDARY ke andar ────────────
    # Sirf ft_upper (boundary se pehle) mein % dhundho
    # Bank filter lagao

    # Short tag scan — boundary ke andar wale tags
    PRICE_SELS = [
        "div.Nx9bqj.CxhGGd", "div.Nx9bqj",
        "div._30jeq3._16Jk6d", "div._30jeq3",
        "div.CEmiEU",
        "div.v1zwn21l.v1zwn20._1psv1zeb9._1psv1ze0",
    ]
    for sel in PRICE_SELS:
        price_tag = soup.select_one(sel)
        if not price_tag:
            continue
        node = price_tag.parent
        for _ in range(6):
            if not node:
                break
            container_text = node.get_text(" ", strip=True)
            if has_bank(container_text[:300]):
                node = node.parent
                continue
            for child in node.find_all(["div", "span"], recursive=True):
                t = child.get_text(strip=True)
                if len(t) > 10:
                    continue
                m = re.fullmatch(r"(\d{1,2})\s*%\s*(?:off)?", t, re.I)
                if m:
                    val = int(m.group(1))
                    if 1 <= val <= 99:
                        # Confirm: yeh % boundary ke andar hai?
                        if t in ft_upper:
                            log.info(f"   [iPHONE-DISC-STRUCT-ZONE] {val}%")
                            return f"{val}%", strikethrough_val
            node = node.parent

    # CSS badge classes
    for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zedr", "div.UkUFwK", "span.UkUFwK"]:
        try:
            tag = soup.select_one(sel)
        except Exception:
            continue
        if not tag:
            continue
        t   = tag.get_text(strip=True)
        m   = re.search(r"(\d{1,2})\s*%", t)
        if m and t in ft_upper:
            val = int(m.group(1))
            if 1 <= val <= 99:
                log.info(f"   [iPHONE-DISC-CSS-ZONE] {val}%")
                return f"{val}%", strikethrough_val

    # ft_upper mein directly search — bank filter ke saath
    for m in re.finditer(r"(\d{1,2})\s*%", ft_upper):
        val = int(m.group(1))
        if not (1 <= val <= 99):
            continue
        ctx = ft_upper[max(0, m.start() - 120): m.end() + 60]
        if not has_bank(ctx):
            log.info(f"   [iPHONE-DISC-ZONE-TEXT] {val}%")
            return f"{val}%", strikethrough_val

    # ── Step 3: % nahi mila lekin strikethrough hai → calc se ────────────────
    if strikethrough_val and cur and cur.isdigit() and strikethrough_val.isdigit():
        orig_int = int(strikethrough_val)
        cur_int2 = int(cur)
        if orig_int > cur_int2:
            d = round((orig_int - cur_int2) / orig_int * 100)
            if 1 <= d <= 99:
                log.info(f"   [iPHONE-DISC-CALC] {d}% (from cur+strikethrough)")
                return f"{d}%", strikethrough_val

    log.info("   [iPHONE-DISC] Strikethrough found but % not in zone → ''")
    return "", strikethrough_val


# ══════════════════════════════════════════════════════════════════════════════
# RATING
# ══════════════════════════════════════════════════════════════════════════════
def get_rating(soup, ft) -> str:
    for tag in soup.find_all(["div", "span"]):
        t = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.[0-9]", t):
            return t
    m = re.search(r"([1-5]\.[0-9])\s*[|]", ft)
    if m:
        return m.group(1)
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# REVIEWS
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
    if disc_num.isdigit() and len(disc_num) <= 2:
        if clean.endswith(disc_num):
            stripped = clean[: -len(disc_num)]
            if stripped.isdigit() and int(stripped) >= 1:
                clean = stripped
                val   = int(clean)
        elif len(clean) > 5 and clean[-2:] == disc_num:
            stripped = clean[:-2]
            if stripped.isdigit() and 1 <= int(stripped) <= MAX_REVIEWS:
                clean = stripped
                val   = int(clean)
    if "," in raw:
        parts = raw.split(",")
        valid_indian = 1 <= len(parts[0]) <= 3 and all(len(p) == 2 for p in parts[1:])
        if not valid_indian and not force_accept:
            return ""
    if val > MAX_REVIEWS and not force_accept:
        return ""
    return clean


def extract_review_number(soup, ft, rating, discount) -> str:
    def try_methods(force: bool = False) -> str:
        if rating:
            for tag in soup.find_all(["div", "span"]):
                t = safe(tag).strip()
                m = re.search(r"[1-5]\.[0-9]\s*[|]\s*([\d,]+)", t)
                if m:
                    v = validate_review(m.group(1), discount, force)
                    if v:
                        return v
        for pipe in soup.find_all(string=re.compile(r"^\s*\|\s*$")):
            nxt = pipe.find_next(["span", "div"])
            if nxt:
                v = validate_review(safe(nxt).strip(), discount, force)
                if v:
                    return v
        if rating:
            m = re.search(re.escape(rating) + r"\s*[|]\s*([\d,]+)", ft)
            if m:
                v = validate_review(m.group(1), discount, force)
                if v:
                    return v
        for sel in ["span.Wphh3N", "span._2_R_DZ", "span._13vcmD"]:
            tag = soup.select_one(sel)
            if tag:
                for raw in re.findall(r"[\d,]+", safe(tag)):
                    v = validate_review(raw, discount, force)
                    if v:
                        return v
        for pat in [
            r"([\d,]+[kK]?)\s+[Rr]ating",
            r"([\d,]+[kK]?)\s+[Rr]eview",
        ]:
            m = re.search(pat, ft, re.I)
            if m:
                v = validate_review(parse_k(m.group(1)), discount, force)
                if v:
                    return v
        return ""

    for attempt in range(1, 6):
        result = try_methods(force=(attempt == 5))
        if result:
            return result
        if attempt < 5:
            log.warning(f"   [REVIEW] Attempt {attempt} failed...")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# DB UPDATE
# ══════════════════════════════════════════════════════════════════════════════
def update_db(client, url, payload):
    cfg      = IPHONE_CFG
    table    = cfg["name"]
    link_col = cfg["link"]

    if not payload:
        log.warning("   Empty payload — skipping.")
        return False
    try:
        check = client.table(table).select(link_col).eq(link_col, url).execute()
        if not check.data:
            clean  = url.strip().rstrip("/")
            check2 = client.table(table).select(link_col).eq(link_col, clean).execute()
            if check2.data:
                url = clean
            else:
                log.error(f"   [URL-NOT-FOUND] {url[:70]}")
                return False
        try:
            client.table(table).update(payload).eq(link_col, url).execute()
            log.info(f"   [OK] {payload}")
            return True
        except Exception:
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
# PROCESS
# ══════════════════════════════════════════════════════════════════════════════
def process():
    cfg      = IPHONE_CFG
    cols     = cfg["cols"]
    link_col = cfg["link"]

    log.info("=" * 70)
    log.info("  iPHONE UPDATER — TEST RUN")
    log.info("=" * 70)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    rows = [
        r for r in client.table("iphone").select("*").execute().data
        if r.get(link_col, "").strip()
    ]
    log.info(f"  {len(rows)} iPhones found.")

    done = fail = 0
    for idx, row in enumerate(rows, 1):
        url = row[link_col].strip()
        log.info(f"\n  [{idx}/{len(rows)}] {url[:80]}")

        cur = disc = orig = rating = reviews = ""

        # Pass 1: CHEAP
        soup1 = fetch(url, render=False)
        if soup1:
            ft1    = soup1.get_text(" ", strip=True)
            cur    = get_current_price(soup1, ft1)
            disc, orig = get_iphone_discount(soup1, ft1, cur)
            rating = get_rating(soup1, ft1)
            reviews = extract_review_number(soup1, ft1, rating, disc)
            log.info(f"   Pass1: cur={cur} disc={disc} orig={orig} rating={rating} reviews={reviews}")

        time.sleep(1)

        # Pass 2: RENDER — agar kuch missing
        if not cur or not rating or not reviews:
            log.info("   Pass2 (RENDER)...")
            soup2 = fetch(url, render=True)
            if soup2:
                ft2 = soup2.get_text(" ", strip=True)
                if not cur:
                    cur = get_current_price(soup2, ft2)
                if not disc:
                    disc, orig = get_iphone_discount(soup2, ft2, cur)
                r2  = get_rating(soup2, ft2)
                rv2 = extract_review_number(soup2, ft2, r2, disc)
                if not rating:  rating  = r2
                if not reviews: reviews = rv2
                log.info(f"   Pass2: cur={cur} disc={disc} orig={orig} rating={rating} reviews={reviews}")
        else:
            log.info("   Pass2 skipped ✅")

        # Sanity check
        if cur and orig and cur.isdigit() and orig.isdigit():
            if int(cur) >= int(orig):
                log.warning(f"   SANITY: cur({cur}) >= orig({orig}) — clearing orig+disc")
                orig = disc = ""

        # Build payload — swap=True: Discounted Price=cur, Price=orig
        p = {}
        if cur:
            p[cols["current_price"]] = fmt_price(cur)
        if orig:
            p[cols["original_price"]] = fmt_price(orig)
        if disc:
            p[cols["discount"]] = fmt_disc(disc)
        if rating:
            p[cols["rating"]] = rating
        if reviews:
            p[cols["reviews"]]  = fmt_reviews(reviews)
            p[cols["reviews2"]] = fmt_reviews(reviews)

        log.info(f"   PAYLOAD: {p}")

        ok = update_db(client, url, p)
        if ok: done += 1
        else:  fail += 1

        time.sleep(DELAY)

    log.info(f"\n  Done={done}  Fail={fail}  Total={len(rows)}")


if __name__ == "__main__":
    process()

