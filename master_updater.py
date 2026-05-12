"""
master_updater.py — Universal Flipkart Price Updater
=====================================================
Tables (12): earbuds → gaming cpu → gaming pc → induction → iphone →
             keybord → laptop → monitar → mouse → smart phone → smart+tv → smartwatch

Features:
  1.  8 ScraperAPI keys — auto-rotation on 401/403
  2.  12 tables with exact column mapping — line by line order
  3.  SWAP mode — induction + iphone
  4.  Discount — ↓ arrow pattern + CSS class + short tag
  5.  Reviews — 5 methods + Indian format + last-2-digits strip + 5 lakh limit
  6.  Math fallbacks — only when real scraped data exists
  7.  Pass 1a + 1b: CHEAP x2 (1 credit each)
  8.  Pass 2: RENDER + LLM (Gemma via OpenRouter) — if disc/reviews missing
  9.  URL verification + auto-clean before DB update
  10. Column error → skip, continue next product
  11. NON-CANCEL policy
  12. ₹ symbol + Indian number format
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

OPENROUTER_KEY   = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = "google/gemma-4-31b-it:free"
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"

ENDPOINT        = "https://api.scraperapi.com/"
REQUEST_TIMEOUT = 90
DELAY           = 1
MAX_REVIEWS     = 500000


# ══════════════════════════════════════════════════════════════════════════════
# TABLE CONFIG — all 12 tables in exact order, line by line
# swap=True → induction/iphone: Discounted Price col = current, Price col = MRP
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
        "name": "gaming cpu",
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
        "name": "gaming pc",
        "link": "Product Link",
        "swap": False,
        "cols": {
            "current_price":  "Price",
            "original_price": "Original Price-2",
            "discount":       "Discount-2",
            "rating":         "Product Rating",
            "reviews":        "product review",
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
# LLM — OpenRouter Gemma for discount extraction
# ══════════════════════════════════════════════════════════════════════════════
def ask_llm_discount(html_snippet: str) -> str:
    if not OPENROUTER_KEY:
        return ""
    prompt = (
        "You are analyzing a Flipkart product page HTML snippet.\n"
        "Find the discount percentage. It appears as a number followed by % sign,\n"
        "near a down arrow symbol, in green bold text.\n"
        "Example: 70%, 69% off, 78%\n\n"
        "IMPORTANT:\n"
        "- Return ONLY the number (e.g. 70) with no % sign, no other text\n"
        "- If no discount exists, return: NONE\n"
        "- Ignore bank offers, credit card offers, cashback percentages\n"
        "- The real discount is always near the product price\n\n"
        f"HTML:\n{html_snippet[:3000]}\n\n"
        "Answer (just the number or NONE):"
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
            timeout=120,  # 2 minutes — enough for LLM to think
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip()
        log.info(f"   [LLM] answer: {answer!r}")
        if answer.upper() == "NONE" or not answer:
            return ""
        m = re.search(r"\b(\d{1,2})\b", answer)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 99:
                log.info(f"   [LLM-DISC] {val}%")
                return str(val) + "%"
    except Exception as exc:
        log.warning(f"   [LLM] Error: {exc}")
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
# REVIEW VALIDATION
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
            log.info(f"   [REVIEW-STRIP] {clean} → {stripped} (removed disc digits)")
            clean = stripped
            val   = int(clean)

    if "," in raw:
        parts = raw.split(",")
        valid_indian = (
            1 <= len(parts[0]) <= 3 and
            all(len(p) == 2 for p in parts[1:])
        )
        if not valid_indian and not force_accept:
            log.warning(f"   [REVIEW-REJECT] Not Indian format: {raw}")
            return ""

    if val > MAX_REVIEWS and not force_accept:
        log.warning(f"   [REVIEW-REJECT] Exceeds 5 lakh: {val}")
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
    """
    Pattern (from image): ↓78%  4,999  ₹1,099
    - ↓ = green down arrow (CSS, comes before number)
    - 78 = 1-2 digit number (large green bold text)
    - % = percent sign (comes right after number)

    The "78%" is in a short HTML tag. Find it.
    """
    from collections import Counter
    candidates = []

    # S1: Short tag (2-6 chars) with ONLY "X%" — the discount badge
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

    # S2: Flipkart CSS discount badge class
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

    # S3: Near rating tag — discount line is just below rating
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

    # S4: "X% off" in text — exclude bank offers
    bank_kw = ["bank","credit","debit","hdfc","sbi","axis","icici",
               "cashback","upi","emi","kotak","rbl","paytm","rupay"]
    for m in re.finditer(r"(\d{1,2})%\s+off", ft, re.I):
        val = int(m.group(1))
        if not (1 <= val <= 99): continue
        ctx = ft[max(0, m.start()-80): m.end()+40].lower()
        if not any(kw in ctx for kw in bank_kw):
            candidates.append(val)
            break

    if not candidates:
        log.info("   [DISC] Not found")
        return ""

    votes = Counter(candidates)
    best_val = votes.most_common(1)[0][0]
    log.info(f"   [DISC] {best_val}% votes={candidates}")
    return str(best_val) + "%"


def get_original_price(soup, ft, cur, disc):
    """
    Original price = MRP (strikethrough number between ↓86% and ₹current).

    Strikethrough digit patterns in HTML (line through each digit):
      0̶ = <s>0</s>  circle with line
      1̶ = <s>1</s>  bar with line
      2̶ = <s>2</s>  curve with line
      3̶ = <s>3</s>  two bumps with line
      4̶ = <s>4</s>  angle with line
      5̶ = <s>5</s>  flat-curve-flat with line
      6̶ = <s>6</s>  oval+tail with line
      7̶ = <s>7</s>  top+diagonal with line
      8̶ = <s>8</s>  TWO ovals with line (NOT same as 0̶)
      9̶ = <s>9</s>  oval on top+tail down with line

    HTML tags: <s>2,999</s> or <del>2,999</del> or style="line-through"

    Strategy:
      1. Calculate exact MRP = cur / (1 - disc/100)
      2. Collect all strikethrough numbers from page
      3. Find closest to calculated — within ₹15 → use page number
         within 10% → use page number; else → use exact calculation
    """
    if not cur or not cur.isdigit() or not disc:
        return ""

    d = disc.replace("%","").strip()
    if not d.isdigit() or not (1 <= int(d) <= 99):
        return ""

    calc_mrp  = round(int(cur) / (1 - int(d) / 100))
    min_orig  = int(cur) + 1
    log.info(f"   [MRP-CALC] cur={cur} disc={disc} → calc={calc_mrp}")

    # Collect strikethrough numbers from all sources
    found = []

    # A. <s> tag — most common on Flipkart
    for tag in soup.find_all("s"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_orig and valid_price(v):
            found.append(int(v))

    # B. <del> tag
    for tag in soup.find_all("del"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_orig and valid_price(v):
            found.append(int(v))

    # C. CSS line-through style
    for tag in soup.find_all(True):
        if "line-through" in tag.get("style", ""):
            v = to_num(safe(tag))
            if v and v.isdigit() and int(v) >= min_orig and valid_price(v):
                found.append(int(v))

    # D. Flipkart MRP CSS classes
    for sel in [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA", "div.yRaY8j",
        "div._3I9_wc._2p6lqe", "div._3I9_wc",
    ]:
        tag = soup.select_one(sel)
        if tag:
            v = to_num(safe(tag))
            if v and v.isdigit() and int(v) >= min_orig and valid_price(v):
                found.append(int(v))

    # Match found numbers to calculated MRP
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
            log.info(f"   [MRP-CALC ₹{diff_rs}diff] {calc_mrp}")
            return str(calc_mrp)

    # JSON-LD fallback
    def ok(v):
        if not v or not valid_price(v): return False
        return int(v) >= min_orig
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

    # No strikethrough found → use exact calculation
    log.info(f"   [MRP-CALC-ONLY] {calc_mrp}")
    return str(calc_mrp)


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

        # ── Pass 1a: CHEAP (1 credit) — try everything ────────────────────────
        soup1 = fetch(url, render=False)
        if soup1:
            ft1     = soup1.get_text(" ", strip=True)
            cur     = get_current_price(soup1, ft1)
            disc    = get_discount(soup1, ft1)
            orig    = get_original_price(soup1, ft1, cur, disc)
            rating  = get_rating(soup1, ft1)
            reviews = extract_review_number(soup1, ft1, rating, disc)
            log.info(f"   Pass1a: cur={cur} disc={disc} orig={orig} "
                     f"rating={rating} reviews={reviews}")

        time.sleep(1)

        # ── Pass 1b: CHEAP retry (1 credit) — if anything missing ─────────────
        if not disc or not reviews or not rating:
            log.info("   Pass1b (CHEAP retry)...")
            soup1b = fetch(url, render=False)
            if soup1b:
                ft1b = soup1b.get_text(" ", strip=True)
                if not disc:
                    disc = get_discount(soup1b, ft1b)
                if not cur:
                    cur  = get_current_price(soup1b, ft1b)
                if not orig and disc:
                    orig = get_original_price(soup1b, ft1b, cur, disc)
                if not rating:
                    rating = get_rating(soup1b, ft1b)
                if not reviews:
                    reviews = extract_review_number(soup1b, ft1b, rating, disc)
                log.info(f"   Pass1b: disc={disc} rating={rating} reviews={reviews}")

        time.sleep(1)

        # ── Pass 2: RENDER + LLM — if disc/reviews/rating still missing ───────
        if not disc or not reviews or not rating:
            log.info("   Pass2 (RENDER + LLM)...")
            soup2 = fetch(url, render=True)
            if soup2:
                ft2 = soup2.get_text(" ", strip=True)

                # LLM for discount — wrapped safely, never blocks workflow
                if not disc:
                    try:
                        disc = ask_llm_discount(str(soup2))
                    except Exception as llm_err:
                        log.warning(f"   [LLM-SKIP] {llm_err}")
                        disc = ""
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

        # ── Pass 3: RENDER again (no LLM) — if still missing after Pass2 ──────
        if not disc or not reviews or not rating:
            log.info("   Pass3 (RENDER fallback — no LLM)...")
            soup3 = fetch(url, render=True)
            if soup3:
                ft3 = soup3.get_text(" ", strip=True)
                if not disc:
                    disc = get_discount(soup3, ft3)
                r3  = get_rating(soup3, ft3)
                rv3 = extract_review_number(soup3, ft3, r3, disc)
                if not rating:  rating  = r3
                if not reviews: reviews = rv3
                if not cur:     cur     = get_current_price(soup3, ft3)
                if not orig and disc:
                    orig = get_original_price(soup3, ft3, cur, disc)
                log.info(f"   Pass3: disc={disc} rating={rating} reviews={reviews}")
        else:
            log.info("   Pass2/Pass3 skipped ✅ credits saved")

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
                log.warning("  KeyboardInterrupt ignored — NON-CANCEL policy active.")
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

