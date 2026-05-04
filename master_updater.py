"""
induction_test.py — Sirf Induction Table ka Data Nikalne ke liye
=================================================================
Master updater se extract kiya gaya — same logic, same functions
Sirf TABLES mein sirf induction hai

Induction Config:
  Table name  : induction
  Link column : Product Link
  swap        : True
    → "Discounted Price" column = current price  (selling price)
    → "Price"            column = original/MRP   (MRP)
    → "Discount Percentage" = discount %
  Rating      : Rating
  Reviews     : Number of Reviews
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

# ── Sirf Induction ────────────────────────────────────────────────────────────
TABLES = [
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
]


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
        return f"Rs.{s}"
    result, s = s[-3:], s[:-3]
    while s:
        result, s = s[-2:] + "," + result, s[:-2]
    return f"Rs.{result.lstrip(',')}"


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
    m = re.search(r"Buy\s*at\s*Rs\.?\s*([\d,]+)", ft, re.I)
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
            prices = re.findall(r"Rs\.?\s*([\d,]+)", p.get_text())
            vlist  = sorted(
                [int(x.replace(",", "")) for x in prices
                 if valid_price(x.replace(",", ""))]
            )
            if vlist:
                return str(vlist[0])
            p = p.find_parent("div")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# DISCOUNT — 4 Layer
# ══════════════════════════════════════════════════════════════════════════════
def get_discount(soup, ft):
    BANK_KW = [
        "bank", "credit", "debit", "hdfc", "sbi", "axis", "icici",
        "cashback", "upi", "emi", "kotak", "rbl", "paytm", "rupay",
        "no cost", "instant discount", "additional", "card offer",
        "flat rs", "flat rupee",
    ]

    def has_bank(text):
        return any(kw in text.lower() for kw in BANK_KW)

    def valid_disc(val):
        return 1 <= val <= 99

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
        for level in range(6):
            if not node:
                break
            container_text = node.get_text(" ", strip=True)
            if has_bank(container_text[:400]):
                node = node.parent
                continue
            for child in node.find_all(["div", "span"], recursive=True):
                t = child.get_text(strip=True)
                if len(t) > 10:
                    continue
                m = re.fullmatch(r"(\d{1,2})\s*%\s*(?:off)?", t, re.I)
                if m:
                    val = int(m.group(1))
                    if valid_disc(val):
                        log.info(f"   [DISC-L1] {val}%")
                        return f"{val}%"
            node = node.parent

    for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zedr",
                "div.UkUFwK", "span.UkUFwK",
                "div._3Ay6Sb", "span._2Tpdn3", "div.VGWC4j", "span._2p6lqe"]:
        try:
            tag = soup.select_one(sel)
        except Exception:
            continue
        if not tag:
            continue
        m = re.search(r"(\d{1,2})\s*%", tag.get_text(strip=True))
        if m:
            val = int(m.group(1))
            if valid_disc(val):
                log.info(f"   [DISC-L2] {val}%")
                return f"{val}%"

    for tag in soup.find_all(["div", "span"]):
        t = tag.get_text(strip=True)
        if len(t) > 8:
            continue
        m = re.fullmatch(r"(\d{1,2})\s*%\s*(?:off)?", t, re.I)
        if not m:
            continue
        val = int(m.group(1))
        if not valid_disc(val):
            continue
        if has_bank(safe(tag.parent)[:250] if tag.parent else ""):
            continue
        log.info(f"   [DISC-L3] {val}%")
        return f"{val}%"

    for m in re.finditer(r"(\d{1,2})\s*%\s+off\b", ft, re.I):
        val = int(m.group(1))
        if not valid_disc(val):
            continue
        ctx = ft[max(0, m.start() - 150): m.end() + 80]
        if not has_bank(ctx):
            log.info(f"   [DISC-L4] {val}%")
            return f"{val}%"

    log.info("   [DISC] Not found → ''")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# ORIGINAL PRICE
# ══════════════════════════════════════════════════════════════════════════════
def get_original_price(soup, ft, cur, disc):
    if not cur or not str(cur).isdigit() or not disc:
        return ""
    d = disc.replace("%", "").strip()
    if not d.isdigit() or not (1 <= int(d) <= 99):
        return ""

    cur_int  = int(cur)
    calc     = round(cur_int / (1 - int(d) / 100))
    log.info(f"   [ORIG-CALC] cur={cur} disc={disc}% → calc={calc}")

    min_v = cur_int + 1
    found = set()

    for tag in soup.find_all("s"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_v and valid_price(v):
            found.add(int(v))
    for tag in soup.find_all("del"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_v and valid_price(v):
            found.add(int(v))
    for tag in soup.find_all("strike"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_v and valid_price(v):
            found.add(int(v))
    for tag in soup.find_all(True):
        if "line-through" in tag.get("style", ""):
            v = to_num(safe(tag))
            if v and v.isdigit() and int(v) >= min_v and valid_price(v):
                found.add(int(v))
    for sel in ["div.yRaY8j.ZYYwLA", "div.yRaY8j", "div._3I9_wc._2p6lqe", "div._3I9_wc",
                "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu"]:
        tag = soup.select_one(sel)
        if tag:
            v = to_num(safe(tag))
            if v and v.isdigit() and int(v) >= min_v and valid_price(v):
                found.add(int(v))

    log.info(f"   [ORIG-FOUND] {sorted(found)}")

    if found:
        closest  = min(found, key=lambda x: abs(x - calc))
        diff_rs  = abs(closest - calc)
        diff_pct = diff_rs / calc if calc else 1
        if diff_rs <= 15:
            log.info(f"   [ORIG-EXACT] {closest}")
            return str(closest)
        elif diff_pct <= 0.10:
            log.info(f"   [ORIG-NEAR] {closest}")
            return str(closest)
        else:
            log.info(f"   [ORIG-CALC-WIN] {calc}")
            return str(calc)

    log.info(f"   [ORIG-CALC-ONLY] {calc}")
    return str(calc)


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
                        log.info(f"   [M1-INLINE] reviews={v}")
                        return v
        for pipe in soup.find_all(string=re.compile(r"^\s*\|\s*$")):
            nxt = pipe.find_next(["span", "div"])
            if nxt:
                v = validate_review(safe(nxt).strip(), discount, force)
                if v:
                    log.info(f"   [M2-PIPE] reviews={v}")
                    return v
        if rating:
            m = re.search(re.escape(rating) + r"\s*[|]\s*([\d,]+)", ft)
            if m:
                v = validate_review(m.group(1), discount, force)
                if v:
                    log.info(f"   [M3-TEXT] reviews={v}")
                    return v
        for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zegu",
                    "span.Wphh3N", "span._2_R_DZ", "span._13vcmD"]:
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
        result = try_methods(force=(attempt == 5))
        if result:
            return result
        if attempt < 5:
            log.warning(f"   [REVIEW] Attempt {attempt} failed...")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# MATH FALLBACKS
# ══════════════════════════════════════════════════════════════════════════════
def math_fallbacks(cur, orig, disc):
    if cur and orig and not disc:
        if str(cur).isdigit() and str(orig).isdigit() and int(orig) > int(cur):
            d = round((int(orig) - int(cur)) / int(orig) * 100)
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
        if cur  and "current_price"  in cols: p[cols["current_price"]]  = fmt_price(cur)
        if orig and "original_price" in cols: p[cols["original_price"]] = fmt_price(orig)
    else:
        if cur  and "current_price"  in cols: p[cols["current_price"]]  = fmt_price(cur)
        if orig and "original_price" in cols: p[cols["original_price"]] = fmt_price(orig)
    if disc and "discount" in cols:
        p[cols["discount"]] = fmt_disc(disc)
    if rating and "rating" in cols:
        p[cols["rating"]] = rating
    if reviews and "reviews" in cols:
        p[cols["reviews"]] = fmt_reviews(reviews)
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
def process_table(client, cfg):
    name     = cfg["name"]
    link_col = cfg["link"]
    cols     = cfg["cols"]
    swap     = cfg.get("swap", False)

    log.info(f"\n{'=' * 70}")
    log.info(f"  TABLE: {name.upper()}  [SWAPPED={swap}]")
    log.info(f"{'=' * 70}")

    all_rows = client.table(name).select("*").execute().data
    log.info(f"  Total rows from DB: {len(all_rows)}")
    if all_rows:
        log.info(f"  Column names: {list(all_rows[0].keys())}")
        log.info(f"  Looking for link_col={link_col!r}")
    rows = [r for r in all_rows if r.get(link_col, "").strip()]
    log.info(f"  {len(rows)} products with valid link.")

    done = fail = 0
    for idx, row in enumerate(rows, 1):
        url = row[link_col].strip()
        log.info(f"\n  [{idx}/{len(rows)}] {url[:80]}")

        cur = orig = disc = rating = reviews = ""

        # Pass 1: CHEAP
        soup1 = fetch(url, render=False)
        if soup1:
            ft1     = soup1.get_text(" ", strip=True)
            cur     = get_current_price(soup1, ft1)
            disc    = get_discount(soup1, ft1)
            orig    = get_original_price(soup1, ft1, cur, disc)
            rating  = get_rating(soup1, ft1)
            reviews = extract_review_number(soup1, ft1, rating, disc)
            log.info(f"   Pass1: cur={cur} disc={disc} orig={orig} rating={rating} reviews={reviews}")

        time.sleep(1)

        # Pass 2: RENDER — agar kuch missing
        if not disc or not reviews or not rating:
            reason = []
            if not disc:    reason.append("no disc")
            if not reviews: reason.append("no reviews")
            if not rating:  reason.append("no rating")
            log.info(f"   Pass2 (RENDER) [{', '.join(reason)}]...")
            soup2 = fetch(url, render=True)
            if soup2:
                ft2 = soup2.get_text(" ", strip=True)
                if not disc:
                    disc = get_discount(soup2, ft2)
                    if disc and not orig:
                        orig = get_original_price(soup2, ft2, cur, disc)
                r2  = get_rating(soup2, ft2)
                rv2 = extract_review_number(soup2, ft2, r2, disc)
                if not rating:  rating  = r2
                if not reviews: reviews = rv2
                if not cur:     cur     = get_current_price(soup2, ft2)
                if not orig and disc:
                    orig = get_original_price(soup2, ft2, cur, disc)
                log.info(f"   Pass2: disc={disc} orig={orig} rating={rating} reviews={reviews}")
        else:
            log.info("   Pass2 skipped")

        cur, orig, disc = math_fallbacks(cur, orig, disc)

        if cur and orig and str(cur).isdigit() and str(orig).isdigit():
            if int(cur) >= int(orig):
                log.warning(f"   SANITY: cur({cur}) >= orig({orig}) — clearing")
                orig = disc = ""

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
    log.info("  INDUCTION TABLE UPDATER — TEST RUN")
    log.info("=" * 70)

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    for cfg in TABLES:
        try:
            process_table(client, cfg)
        except Exception as exc:
            log.error(f"  ERROR: {exc}")

    log.info("\n" + "=" * 70)
    log.info("  INDUCTION COMPLETE")
    log.info("=" * 70)


if __name__ == "__main__":
    main()

