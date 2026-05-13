"""
update_three_tables.py — Focused updater for iphone, smart phone, smartwatch
=============================================================================
Problems being solved:
  - iphone: discount wrongly added when none exists; Price col getting wrong data
  - smart phone: Price col 80% right; Original Price 80% wrong
  - smartwatch: Current price and discount % not accurate

Key rules:
  - Star + Reviews: NOT TOUCHED (working perfectly)
  - LLM agent used for discount verification
  - Random check: if 20 consecutive products show discount → LLM verify
  - 3-pass system per product
"""

import os, re, json, time, logging, random, requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from supabase import create_client, Client
from collections import Counter

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

OPENROUTER_KEY   = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = "google/gemma-4-31b-it:free"
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"

ENDPOINT        = "https://api.scraperapi.com/"
REQUEST_TIMEOUT = 90
DELAY           = 1
MAX_REVIEWS     = 500000


# ══════════════════════════════════════════════════════════════════════════════
# TABLE CONFIG — only 3 tables
# ══════════════════════════════════════════════════════════════════════════════
TABLES = [
    {
        "name": "iphone",
        "link": "Product URL",
        "swap": True,   # Discounted Price = current, Price = MRP
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
# LLM AGENT — for discount verification
# ══════════════════════════════════════════════════════════════════════════════
def llm_verify_discount(html_snippet: str, suspected_disc: str) -> str:
    """
    Ask LLM to verify if the suspected discount is real.
    Returns confirmed discount % or "" if no discount on page.
    """
    if not OPENROUTER_KEY:
        return suspected_disc

    prompt = (
        "You are analyzing a Flipkart product page HTML.\n"
        "Task: Is there a real product discount shown on this page?\n\n"
        "A real discount looks like: ↓70% (green down arrow + number + %)\n"
        "NOT a real discount: bank offers, credit card cashback, EMI discounts\n\n"
        f"The code thinks discount is: {suspected_disc}\n\n"
        "Look at the HTML and answer:\n"
        "- If real product discount exists → return just the number (e.g. 70)\n"
        "- If NO real product discount → return: NONE\n"
        "- If you see a different discount % → return that number\n\n"
        f"HTML (first 3000 chars):\n{html_snippet[:3000]}\n\n"
        "Answer (number or NONE):"
    )
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 10,
            },
            timeout=120,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip()
        log.info(f"   [LLM-VERIFY] answer={answer!r}")

        if answer.upper() == "NONE":
            log.info("   [LLM] Confirmed: NO discount on this page")
            return ""

        m = re.search(r"\b(\d{1,2})\b", answer)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 99:
                log.info(f"   [LLM] Confirmed discount: {val}%")
                return str(val) + "%"
    except Exception as exc:
        log.warning(f"   [LLM-ERROR] {exc} — using original value")
        return suspected_disc

    return suspected_disc


def llm_get_discount(html_snippet: str) -> str:
    """Ask LLM to find discount when our methods fail."""
    if not OPENROUTER_KEY:
        return ""
    prompt = (
        "Flipkart product page HTML. Find the product discount percentage.\n"
        "Pattern: ↓70% means 70% discount (green arrow + number + %).\n"
        "Ignore bank/card/cashback offers.\n"
        "Return ONLY the number (e.g. 70) or NONE if no discount.\n\n"
        f"HTML:\n{html_snippet[:3000]}\n\nAnswer:"
    )
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 10,
            },
            timeout=120,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip()
        if answer.upper() == "NONE":
            return ""
        m = re.search(r"\b(\d{1,2})\b", answer)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 99:
                return str(val) + "%"
    except Exception as exc:
        log.warning(f"   [LLM-GET-ERROR] {exc}")
    return ""


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
# REVIEW VALIDATION — NOT TOUCHED
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
        result = try_methods(force=(attempt == 5))
        if result:
            return result
        if attempt < 5:
            log.warning(f"   [REVIEW] Attempt {attempt} failed, retrying...")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# CURRENT PRICE — NOT TOUCHED
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
# DISCOUNT — strong multi-source + LLM verification
# ══════════════════════════════════════════════════════════════════════════════
def get_discount(soup, ft) -> str:
    candidates = []

    # S1: Short standalone tag (2-6 chars) with "X%" — the actual badge
    for tag in soup.find_all(["div", "span"]):
        t = safe(tag).strip()
        if 2 <= len(t) <= 6:
            m = re.fullmatch(r"(\d{1,2})\s*%\s*(off)?", t, re.I)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 99:
                    parent_t = safe(tag.parent)[:60].lower() if tag.parent else ""
                    if "rating" not in parent_t and "review" not in parent_t:
                        candidates.append(val)

    # S2: Flipkart CSS discount class
    for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zedr",
                "div.UkUFwK", "span.UkUFwK"]:
        try:
            tag = soup.select_one(sel)
        except Exception:
            continue
        if tag:
            m = re.search(r"(\d{1,2})\s*%", safe(tag).strip())
            if m:
                val = int(m.group(1))
                if 1 <= val <= 99:
                    candidates.append(val)
                    break

    # S3: Near rating tag
    for tag in soup.find_all(["div", "span"]):
        if re.fullmatch(r"[1-5]\.[0-9]", safe(tag).strip()):
            node = tag.parent
            for _ in range(5):
                if not node: break
                for child in node.find_all(["div", "span"]):
                    t = safe(child).strip()
                    if 2 <= len(t) <= 6:
                        m = re.fullmatch(r"(\d{1,2})\s*%\s*(off)?", t, re.I)
                        if m:
                            val = int(m.group(1))
                            if 1 <= val <= 99:
                                candidates.append(val)
                                break
                node = node.parent
            break

    # S4: "X% off" text — exclude bank offers
    bank_kw = ["bank","credit","debit","hdfc","sbi","axis","icici",
               "cashback","upi","emi","kotak","rbl","paytm","rupay"]
    for m in re.finditer(r"\b(\d{1,2})%\s+off\b", ft, re.I):
        val = int(m.group(1))
        if not (1 <= val <= 99): continue
        ctx = ft[max(0, m.start()-80): m.end()+40].lower()
        if not any(kw in ctx for kw in bank_kw):
            candidates.append(val)
            break

    if not candidates:
        return ""

    votes   = Counter(candidates)
    best    = votes.most_common(1)[0][0]
    log.info(f"   [DISC] {best}% candidates={candidates}")
    return str(best) + "%"


def get_rating(soup, ft) -> str:
    for tag in soup.find_all(["div","span"]):
        t = safe(tag).strip()
        if re.fullmatch(r"[1-5]\.[0-9]", t):
            return t
    m = re.search(r"([1-5]\.[0-9])\s*[★✩⭐|]", ft)
    if m: return m.group(1)
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# ORIGINAL PRICE — strikethrough match
# ══════════════════════════════════════════════════════════════════════════════
def get_original_price(soup, ft, cur, disc) -> str:
    if not cur or not cur.isdigit() or not disc:
        return ""
    d = disc.replace("%","").strip()
    if not d.isdigit() or not (1 <= int(d) <= 99):
        return ""

    calc_mrp = round(int(cur) / (1 - int(d) / 100))
    min_orig = int(cur) + 1
    log.info(f"   [MRP-CALC] cur={cur} disc={disc} → calc={calc_mrp}")

    found = []
    for tag in soup.find_all("s"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_orig and valid_price(v):
            found.append(int(v))
    for tag in soup.find_all("del"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_orig and valid_price(v):
            found.append(int(v))
    for tag in soup.find_all(True):
        if "line-through" in tag.get("style", ""):
            v = to_num(safe(tag))
            if v and v.isdigit() and int(v) >= min_orig and valid_price(v):
                found.append(int(v))
    for sel in ["div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
                "div.yRaY8j.ZYYwLA","div.yRaY8j","div._3I9_wc._2p6lqe","div._3I9_wc"]:
        tag = soup.select_one(sel)
        if tag:
            v = to_num(safe(tag))
            if v and v.isdigit() and int(v) >= min_orig and valid_price(v):
                found.append(int(v))

    if found:
        closest  = min(found, key=lambda x: abs(x - calc_mrp))
        diff_rs  = abs(closest - calc_mrp)
        diff_pct = diff_rs / calc_mrp if calc_mrp else 1
        if diff_rs <= 15:
            log.info(f"   [MRP-PAGE ₹{diff_rs}] {closest}")
            return str(closest)
        elif diff_pct <= 0.10:
            log.info(f"   [MRP-PAGE {diff_pct:.1%}] {closest}")
            return str(closest)
        else:
            log.info(f"   [MRP-CALC] {calc_mrp}")
            return str(calc_mrp)

    log.info(f"   [MRP-CALC-ONLY] {calc_mrp}")
    return str(calc_mrp)


def math_fallbacks(cur, orig, disc):
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
        if cur and "current_price" in cols:
            p[cols["current_price"]]  = fmt_price(cur)
        if orig and "original_price" in cols:
            p[cols["original_price"]] = fmt_price(orig)
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
                    success = True
                except Exception as ce:
                    log.warning(f"   [COL-SKIP] '{col}' → {ce}")
            return success
    except Exception as exc:
        log.error(f"   [DB] {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# PROCESS TABLE — with LLM verification + random spot check
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
    consecutive_disc = 0   # track consecutive products with discount

    for idx, row in enumerate(rows, 1):
        url = row[link_col].strip()
        log.info(f"\n  [{idx}/{len(rows)}] {url[:80]}")

        cur = orig = disc = rating = reviews = ""
        html_for_llm = ""

        # ── Pass 1a: CHEAP ────────────────────────────────────────────────────
        soup1 = fetch(url, render=False)
        if soup1:
            ft1          = soup1.get_text(" ", strip=True)
            cur          = get_current_price(soup1, ft1)
            disc         = get_discount(soup1, ft1)
            orig         = get_original_price(soup1, ft1, cur, disc)
            rating       = get_rating(soup1, ft1)
            reviews      = extract_review_number(soup1, ft1, rating, disc)
            html_for_llm = str(soup1)
            log.info(f"   Pass1a: cur={cur} disc={disc} orig={orig} "
                     f"rating={rating} reviews={reviews}")
        time.sleep(1)

        # ── Pass 1b: CHEAP retry if missing ───────────────────────────────────
        if not disc or not reviews or not rating:
            log.info("   Pass1b (CHEAP retry)...")
            soup1b = fetch(url, render=False)
            if soup1b:
                ft1b = soup1b.get_text(" ", strip=True)
                if not disc:
                    disc = get_discount(soup1b, ft1b)
                    if disc and not orig:
                        orig = get_original_price(soup1b, ft1b, cur, disc)
                if not cur:    cur     = get_current_price(soup1b, ft1b)
                if not rating: rating  = get_rating(soup1b, ft1b)
                if not reviews:
                    reviews = extract_review_number(soup1b, ft1b, rating, disc)
                if not html_for_llm:
                    html_for_llm = str(soup1b)
                log.info(f"   Pass1b: disc={disc} rating={rating} reviews={reviews}")
        time.sleep(1)

        # ── Pass 2: RENDER + LLM verification ─────────────────────────────────
        if not disc or not reviews or not rating:
            log.info("   Pass2 (RENDER + LLM)...")
            soup2 = fetch(url, render=True)
            if soup2:
                ft2 = soup2.get_text(" ", strip=True)
                html_for_llm = str(soup2)

                if not disc:
                    try:
                        disc = llm_get_discount(html_for_llm)
                    except Exception as e:
                        log.warning(f"   [LLM-GET-SKIP] {e}")
                    if not disc:
                        disc = get_discount(soup2, ft2)

                r2  = get_rating(soup2, ft2)
                rv2 = extract_review_number(soup2, ft2, r2, disc)
                if not rating:  rating  = r2
                if not reviews: reviews = rv2
                if not cur:     cur     = get_current_price(soup2, ft2)
                if not orig and disc:
                    orig = get_original_price(soup2, ft2, cur, disc)
                log.info(f"   Pass2: disc={disc} rating={rating} reviews={reviews}")

        # ── Pass 3: RENDER fallback (no LLM) ─────────────────────────────────
        if not disc or not reviews or not rating:
            log.info("   Pass3 (RENDER fallback)...")
            soup3 = fetch(url, render=True)
            if soup3:
                ft3 = soup3.get_text(" ", strip=True)
                if not disc:    disc    = get_discount(soup3, ft3)
                if not rating:  rating  = get_rating(soup3, ft3)
                if not reviews: reviews = extract_review_number(soup3, ft3, rating, disc)
                if not cur:     cur     = get_current_price(soup3, ft3)
                if not orig and disc:
                    orig = get_original_price(soup3, ft3, cur, disc)
                log.info(f"   Pass3: disc={disc} rating={rating} reviews={reviews}")
        else:
            log.info("   Pass2/3 skipped ✅ credits saved")

        # ── LLM VERIFICATION for discount ─────────────────────────────────────
        # Verify discount using LLM in these cases:
        #   1. iPhone — always verify (many iPhones have no discount)
        #   2. Any table — if 20 consecutive products show discount (random check)
        #   3. Random 10% spot check
        should_verify = False

        if disc and name == "iphone":
            should_verify = True
            log.info("   [VERIFY] iPhone — always verify discount with LLM")
        elif disc and consecutive_disc >= 20:
            should_verify = True
            log.info(f"   [VERIFY] {consecutive_disc} consecutive discounts — spot check")
            consecutive_disc = 0
        elif disc and random.random() < 0.10:
            should_verify = True
            log.info("   [VERIFY] Random 10% spot check")

        if should_verify and html_for_llm:
            try:
                verified = llm_verify_discount(html_for_llm, disc)
                if verified != disc:
                    log.info(f"   [LLM-CORRECTION] {disc} → {verified}")
                    disc = verified
                    if not disc:
                        orig = ""   # no discount = no original price
            except Exception as e:
                log.warning(f"   [LLM-VERIFY-SKIP] {e}")

        # Track consecutive discounts
        if disc:
            consecutive_disc += 1
        else:
            consecutive_disc = 0

        # ── Math fallbacks ────────────────────────────────────────────────────
        cur, orig, disc = math_fallbacks(cur, orig, disc)

        # ── Sanity check ──────────────────────────────────────────────────────
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
    log.info(f"  FOCUSED UPDATER — iphone + smart phone + smartwatch")
    log.info(f"  Keys: {len(SCRAPERAPI_KEYS)} | LLM: {'YES' if OPENROUTER_KEY else 'NO'}")
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
                    log.error(f"  Skipping after 3 attempts.")
                    table_done = True
                else:
                    log.info(f"  Retrying...")
                    time.sleep(5)

    log.info("\n" + "="*70)
    log.info("  ALL 3 TABLES COMPLETE")
    log.info("="*70)


if __name__ == "__main__":
    main()

