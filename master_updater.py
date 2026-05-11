"""
Flipkart Price Scraper — FINAL ACCURATE VERSION
WebScraping.AI (2 keys) + Supabase + GitHub Actions

FLIPKART PAGE PATTERN (image se confirmed):
  ↓78%    4,999(line)    ₹1,099
  disc%   orig(strike)   current(bold)

ORIGINAL PRICE STRATEGY:
  Step 1: <s>/<del> tags se strikethrough number nikalo  ← PAGE SE
  Step 2: Calculate: cur / (1 - disc/100)               ← MATH SE
  Step 3: diff <= Rs.20 → page wala use karo (accurate)
           diff > Rs.20  → calculated use karo

SWAP TABLES (briefing se):
  induction : cur_col="Discounted Price", orig_col="Price"  (swap=True)
  iphone    : cur_col="Discounted Price", orig_col="Price"  (swap=True)
"""

import os, re, time, json, logging
import requests
from collections import Counter
from bs4 import BeautifulSoup
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Supabase ───────────────────────────────────────────────────────────────
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"],
)

# ── WebScraping.AI — 2 keys ────────────────────────────────────────────────
_WS_KEYS = [k for k in [
    os.environ.get("WEBSCRAPING_AI_KEY", ""),
    os.environ.get("WEBSCRAPING_AI_KEY_2", ""),
] if k.strip()]
_ws_idx = 0

WS_HTML = "https://api.webscraping.ai/html"
WS_AI   = "https://api.webscraping.ai/ai/fields"

def _key():
    return _WS_KEYS[_ws_idx % len(_WS_KEYS)]

def _rotate():
    global _ws_idx
    _ws_idx = (_ws_idx + 1) % len(_WS_KEYS)
    logger.warning(f"  ⚡ Key rotate → key {_ws_idx + 1}")


# ═══════════════════════════════════════════════════════════════════════════
# FETCH
# ═══════════════════════════════════════════════════════════════════════════

def fetch_html(url: str, js: bool) -> str | None:
    label  = "JS" if js else "STATIC"
    params = {
        "api_key" : _key(),
        "url"     : url,
        "js"      : "true" if js else "false",
        "country" : "in",
        "timeout" : 15000,
    }
    try:
        r = requests.get(WS_HTML, params=params, timeout=60)
        logger.info(f"    [{label}] HTTP {r.status_code} — {len(r.text)} chars")
        if r.status_code == 200 and len(r.text) > 500:
            return r.text
        if r.status_code == 402:
            logger.warning("    Credits khatam — key rotate")
            _rotate()
    except Exception as e:
        logger.warning(f"    [{label}] Error: {e}")
    return None


def fetch_ai(url: str) -> dict:
    """Last resort — 5 credits."""
    fields = {
        "current_price": "Bold selling price NOW after discount. e.g. ₹1,099 or Rs.1,099",
        "discount"     : "Green discount % with down arrow. e.g. 78%. Empty if none.",
        "original_price": "Strikethrough MRP price. e.g. ₹4,999. Empty if no discount.",
        "rating"       : "Star rating number. e.g. 4.2. Empty if not shown.",
        "reviews"      : "Total reviews/ratings count. e.g. 34,452. Empty if not shown.",
    }
    params = {
        "api_key" : _key(),
        "url"     : url,
        "fields"  : json.dumps(fields),
        "country" : "in",
        "js"      : "true",
    }
    try:
        r = requests.get(WS_AI, params=params, timeout=60)
        logger.info(f"    [AI] HTTP {r.status_code}")
        if r.status_code == 200:
            raw  = r.json()
            data = raw.get("result", raw) if isinstance(raw, dict) else {}
            if isinstance(data, dict):
                logger.info(f"    [AI] Got: {data}")
                return data
        if r.status_code == 402:
            _rotate()
    except Exception as e:
        logger.error(f"    [AI] Error: {e}")
    return {}


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def parse_int(t) -> int | None:
    if not t: return None
    d = re.sub(r"[^\d]", "", str(t))
    return int(d) if d else None

def indian_price(v: int) -> str:
    if not v: return ""
    s = str(v)
    if len(s) <= 3: return f"Rs.{s}"
    r = s[-3:]; s = s[:-3]
    while len(s) > 2: r = s[-2:] + "," + r; s = s[:-2]
    if s: r = s + "," + r
    return f"Rs.{r}"

def indian_number(v) -> str:
    raw = re.sub(r"[^\d]", "", str(v)) if v else ""
    if not raw: return ""
    s = raw
    if len(s) <= 3: return s
    r = s[-3:]; s = s[:-3]
    while len(s) > 2: r = s[-2:] + "," + r; s = s[:-2]
    if s: r = s + "," + r
    return r

def to_rs(s: str) -> str:
    s = str(s or "").strip()
    s = re.sub(r"^[₹Rs\.\s]+", "", s).strip()
    return f"Rs.{s}" if s else ""

_BANK_KW = [
    "bank","credit","debit","hdfc","sbi","axis","icici","cashback",
    "upi","emi","kotak","rbl","paytm","rupay","no cost",
    "instant discount","additional","card offer","flat rs","flat rupee",
]
def has_bank_kw(t: str) -> bool:
    return any(k in t.lower() for k in _BANK_KW)


# ═══════════════════════════════════════════════════════════════════════════
# CURRENT PRICE — Bold price (selling price NOW)
# ═══════════════════════════════════════════════════════════════════════════

# Flipkart current price CSS — multiple versions
_CUR_CSS = [
    ("div",  "_30jeq3 _16Jk6d"), ("div",  "_30jeq3"), ("span", "_30jeq3"),
    ("div",  "CEmiEU"),           ("span", "CEmiEU"),
    ("div",  "hl05au"),           ("span", "hl05au"),
    ("div",  "Nx9bqj"),           ("span", "Nx9bqj"),
]

def extract_current_price(soup: BeautifulSoup) -> int | None:
    """
    Bold selling price = current price (after discount).
    Method 1: CSS → Method 2: ₹ symbol → Method 3: Rs. pattern
    """
    # Method 1: CSS classes
    for tag, cls in _CUR_CSS:
        el = soup.find(tag, class_=cls.split())
        if el:
            v = parse_int(el.get_text())
            if v and 50 <= v <= 50_00_000:
                return v

    # Method 2: ₹ symbol — collect all, pick best (skip bank offer amounts)
    candidates_rs = []
    for s in soup.strings:
        # Bank offer context skip karo
        parent_text = s.parent.get_text() if s.parent else ""
        if has_bank_kw(parent_text):
            continue
        for m in re.finditer(r"₹\s*([\d,]+)", str(s)):
            v = parse_int(m.group(1))
            if v and 100 <= v <= 50_00_000:
                candidates_rs.append(v)
    if candidates_rs:
        # Smallest valid price = current (not the MRP which is larger)
        return min(candidates_rs)

    # Method 3: Rs. pattern — same approach
    candidates_rs2 = []
    for s in soup.strings:
        parent_text = s.parent.get_text() if s.parent else ""
        if has_bank_kw(parent_text):
            continue
        for m in re.finditer(r"Rs\.\s*([\d,]+)", str(s)):
            v = parse_int(m.group(1))
            if v and 100 <= v <= 50_00_000:
                candidates_rs2.append(v)
    if candidates_rs2:
        return min(candidates_rs2)

    return None


# ═══════════════════════════════════════════════════════════════════════════
# DISCOUNT — Green % badge
# ═══════════════════════════════════════════════════════════════════════════

_DISC_CSS = [
    "UkUFwK","VGWI6a","pPAw9j","_3Ay6Sb","Bs5uzZ","_2Tpdn3","_1psv1zeb9","_11fdBN",
]

def extract_discount(soup: BeautifulSoup) -> int | None:
    """
    Discount % from green badge.
    Pattern from image: ↓78% — comes BEFORE strikethrough price.
    Bank offer filter lagao.
    """
    candidates = []

    # L1: CSS badge
    for cls in _DISC_CSS:
        for tag in soup.find_all(["div","span"], class_=cls):
            t = tag.get_text(strip=True)
            m = re.match(r"^(\d{1,2})%", t)
            if m:
                v = int(m.group(1))
                if 1 <= v <= 95 and not has_bank_kw(t):
                    candidates.append(v)

    # L2: Short tags ≤8 chars
    for tag in soup.find_all(True):
        t = tag.get_text(strip=True)
        if 2 <= len(t) <= 8:
            m = re.match(r"^(\d{1,2})%", t)
            if m:
                v = int(m.group(1))
                pt = tag.parent.get_text() if tag.parent else ""
                if 1 <= v <= 95 and not has_bank_kw(pt):
                    candidates.append(v)

    # L3: "X% off" in full text
    text = soup.get_text()
    for m in re.finditer(r"(\d{1,2})%\s*off", text, re.IGNORECASE):
        v = int(m.group(1))
        if 1 <= v <= 95:
            ctx = text[max(0, m.start()-100): m.end()+50]
            if not has_bank_kw(ctx):
                candidates.append(v)

    if not candidates:
        return None
    # Most common = real discount (bank offers different numbers)
    return Counter(candidates).most_common(1)[0][0]


# ═══════════════════════════════════════════════════════════════════════════
# ORIGINAL PRICE — Strikethrough + Verification
# ═══════════════════════════════════════════════════════════════════════════

# Strikethrough digits: 0̶1̶2̶3̶4̶5̶6̶7̶8̶9̶
# HTML pe: <s>4,999</s> ya <del>4,999</del>
# Pattern from image: 4,999 with horizontal line through digits

def get_strikethrough_prices(soup: BeautifulSoup, iphone_mode: bool = False) -> list[int]:
    """
    Strikethrough = original MRP (line drawn through digits).
    Sources:
      - <s>4,999</s>      ← Most common on Flipkart
      - <del>4,999</del>
      - <strike>4,999</strike>
      - style="text-decoration: line-through"  (skip for iPhone — variant bleed)
    """
    prices = set()

    # HTML strikethrough tags — most reliable
    for tag in soup.find_all(["s", "del", "strike"]):
        v = parse_int(tag.get_text())
        if v and 100 <= v <= 50_00_000:
            prices.add(v)

    # CSS line-through (skip for iPhone)
    if not iphone_mode:
        for tag in soup.find_all(style=re.compile(r"line-through", re.I)):
            v = parse_int(tag.get_text())
            if v and 100 <= v <= 50_00_000:
                prices.add(v)

    return list(prices)


def get_original_price(
    cur: int,
    disc: int,
    soup: BeautifulSoup,
    iphone_mode: bool = False,
) -> str:
    """
    DUAL METHOD — verify karo:

    Method A: Page se strikethrough price nikalo
    Method B: Calculate: cur / (1 - disc/100)

    Verify:
      diff <= Rs.20  → Method A (page ka real data)
      diff > Rs.20   → Method B (calculation more reliable)
      No page price  → Method B
    """
    if not cur or not disc or disc <= 0 or disc >= 100:
        return ""

    # Method B: Calculate
    calc_orig = round(cur / (1 - disc / 100))

    # Method A: Strikethrough from page
    strikethrough = get_strikethrough_prices(soup, iphone_mode)

    if strikethrough:
        # Sabse reasonable strikethrough choose karo
        # (cur se bada hona chahiye, calc se zyada door nahi)
        valid = [p for p in strikethrough if p > cur]
        if valid:
            best = min(valid, key=lambda x: abs(x - calc_orig))
            diff = abs(best - calc_orig)

            logger.info(f"    Strikethrough: ₹{best} | Calc: ₹{calc_orig} | Diff: ₹{diff}")

            if diff <= 20:
                # Page ka data accurate hai
                logger.info(f"    ✓ Page price use kar raha hoon (diff ≤ Rs.20)")
                return indian_price(best)
            else:
                # Bada difference — calculation zyada reliable
                logger.info(f"    ⚠ Diff Rs.{diff} > 20 — calc use kar raha hoon")
                return indian_price(calc_orig)

    # No strikethrough found — calculate use karo
    logger.info(f"    No strikethrough — calc: ₹{calc_orig}")
    return indian_price(calc_orig)


# ═══════════════════════════════════════════════════════════════════════════
# RATING & REVIEWS
# ═══════════════════════════════════════════════════════════════════════════

def extract_rating(soup: BeautifulSoup) -> str:
    text = soup.get_text()
    for pat in [r"(\d\.\d)\s*★", r"(\d\.\d)\s+Ratings?", r"(\d\.\d)\s+out"]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def extract_reviews(soup: BeautifulSoup) -> tuple[str, str]:
    """Returns (ratings_count, reviews_count)"""
    text = soup.get_text()
    # "1,01,973 Ratings & 78,586 Reviews"
    m = re.search(
        r"([\d,]+)\s+Ratings?\s*[&|]\s*([\d,]+)\s+Reviews?",
        text, re.IGNORECASE,
    )
    if m:
        return (
            indian_number(parse_int(m.group(1))),
            indian_number(parse_int(m.group(2))),
        )
    m = re.search(r"([\d,]+)\s+(?:Ratings?|Reviews?)", text, re.IGNORECASE)
    if m:
        return "", indian_number(parse_int(m.group(1)))
    return "", ""


# ═══════════════════════════════════════════════════════════════════════════
# TABLE CONFIG — Briefing ke exact column names
# SWAP = True: cur_col aur orig_col mein price order ulta hai
# ═══════════════════════════════════════════════════════════════════════════

TABLE_CONFIG = {
    "earbuds": {
        "link_col":"Product Link", "cur_col":"Current Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False, "swap":False,
    },
    "gaming cpu": {
        "link_col":"Product Link", "cur_col":"Current Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False, "swap":False,
    },
    "gaming pc": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price-2", "disc_col":"Discount-2",
        "rating_col":"Product Rating", "reviews_col":"product review",
        "combined":False, "iphone":False, "swap":False,
    },
    "induction": {
        "link_col":"ProductLink",              # bina space
        "cur_col":"Discounted Price",          # swap=True: yeh current hai
        "orig_col":"Price",                    # swap=True: yeh MRP hai
        "disc_col":"Discount Percentage",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False, "swap":True,
    },
    "iphone": {
        "link_col":"Product URL",
        "cur_col":"Discounted Price",          # swap=True: yeh current hai
        "orig_col":"Price",                    # swap=True: yeh MRP hai
        "disc_col":"Discount Percentage",
        "rating_col":"Product Rating",
        "reviews_col":"Number of Reviews",
        "reviews2_col":"Number of Ratings",
        "combined":False, "iphone":True, "swap":True,
    },
    "keybord": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False, "swap":False,
    },
    "laptop": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "combined_col":"Rating and Reviews",
        "combined":True, "iphone":False, "swap":False,
    },
    "monitar": {
        "link_col":"Product URL", "cur_col":"Current Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False, "swap":False,
    },
    "mouse": {
        "link_col":"Product Link", "cur_col":"Current Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False, "swap":False,
    },
    "smart phone": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "combined_col":"Ratings and Reviews",
        "combined":True, "iphone":False, "swap":False,
    },
    "smart+tv": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "combined_col":"Ratings and Reviews",
        "combined":True, "iphone":False, "swap":False,
    },
    "smartwatch": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Review",
        "combined":False, "iphone":False, "swap":False,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# BUILD UPDATE DICT FROM HTML
# ═══════════════════════════════════════════════════════════════════════════

def build_from_html(soup: BeautifulSoup, cfg: dict) -> dict:
    """
    Extract karo aur verified data return karo.
    Dual method for original price.
    """
    update = {}

    # ── iPhone special ─────────────────────────────────────────────────────
    if cfg["iphone"]:
        html_str = str(soup)
        bi       = html_str.find("Protect Promise Fee")
        lim      = BeautifulSoup(html_str[:bi], "html.parser") if bi != -1 else soup

        cur  = extract_current_price(lim)
        disc = extract_discount(lim)

        logger.info(f"    [iPhone] cur={cur} disc={disc}%")

        if cur:
            update[cfg["cur_col"]] = indian_price(cur)
        if disc:
            update[cfg["disc_col"]] = f"{disc}%"
            update[cfg["orig_col"]] = get_original_price(cur, disc, lim, iphone_mode=True)
        else:
            update[cfg["disc_col"]] = ""
            update[cfg["orig_col"]] = ""

        rating = extract_rating(lim)
        if rating: update[cfg["rating_col"]] = rating
        rc, rv = extract_reviews(lim)
        if rv:     update[cfg["reviews_col"]] = rv
        if rc:     update[cfg.get("reviews2_col","Number of Ratings")] = rc
        return update

    # ── Standard / Combined ────────────────────────────────────────────────
    cur  = extract_current_price(soup)
    disc = extract_discount(soup)

    logger.info(f"    cur={cur} disc={disc}%")

    if cur:
        update[cfg["cur_col"]] = indian_price(cur)

    if disc:
        update[cfg["disc_col"]] = f"{disc}%"
        update[cfg["orig_col"]] = get_original_price(cur, disc, soup)
    else:
        update[cfg["disc_col"]] = ""
        update[cfg["orig_col"]] = ""

    rating = extract_rating(soup)
    rc, rv = extract_reviews(soup)

    if cfg["combined"]:
        if rating and rv:
            update[cfg["combined_col"]] = f"{rating} | {rv}"
        elif rating:
            update[cfg["combined_col"]] = rating
    else:
        if rating: update[cfg["rating_col"]] = rating
        if rv:     update[cfg["reviews_col"]] = rv

    return update


# ═══════════════════════════════════════════════════════════════════════════
# BUILD FROM AI
# ═══════════════════════════════════════════════════════════════════════════

def build_from_ai(ai_data: dict, cfg: dict) -> dict:
    """
    AI response se build.
    Original = strikethrough from AI ya calculate.
    """
    update = {}
    if not ai_data: return update

    cur_str   = to_rs(str(ai_data.get("current_price")  or ""))
    disc_str  = str(ai_data.get("discount")              or "").strip()
    orig_str  = to_rs(str(ai_data.get("original_price") or ""))
    rating    = str(ai_data.get("rating")                or "").strip()
    reviews   = str(ai_data.get("reviews")               or "").strip()

    cur_int   = parse_int(cur_str)
    disc_int  = parse_int(disc_str)
    orig_int  = parse_int(orig_str)

    if cur_int:
        update[cfg["cur_col"]] = cur_str

    if disc_int and 1 <= disc_int <= 95:
        update[cfg["disc_col"]] = f"{disc_int}%"

        # Dual method for original
        calc_orig = round(cur_int / (1 - disc_int / 100)) if cur_int else 0

        if orig_int and orig_int > cur_int:
            diff = abs(orig_int - calc_orig)
            logger.info(f"    [AI] orig_scraped={orig_int} calc={calc_orig} diff={diff}")
            if diff <= 20:
                update[cfg["orig_col"]] = orig_str
            else:
                update[cfg["orig_col"]] = indian_price(calc_orig)
        elif calc_orig:
            update[cfg["orig_col"]] = indian_price(calc_orig)
        else:
            update[cfg["orig_col"]] = ""
    else:
        update[cfg["disc_col"]] = ""
        update[cfg["orig_col"]] = ""

    if cfg["combined"]:
        if rating and reviews:
            update[cfg["combined_col"]] = f"{rating} | {reviews}"
    else:
        if rating:   update[cfg.get("rating_col",  "Rating")]            = rating
        if reviews:  update[cfg.get("reviews_col", "Number of Reviews")] = reviews

    return update


# ═══════════════════════════════════════════════════════════════════════════
# SCRAPE ROW — 3 attempts, credit saver
# ═══════════════════════════════════════════════════════════════════════════

def scrape_row(url: str, cfg: dict) -> dict:
    # Attempt 1: STATIC (1 credit) — target: 95% yahi kaam kare
    logger.info("  Attempt 1 — STATIC (1 credit)")
    html = fetch_html(url, js=False)
    if html:
        soup   = BeautifulSoup(html, "html.parser")
        update = build_from_html(soup, cfg)
        if update.get(cfg["cur_col"]):
            logger.info("  ✓ STATIC — 1 credit")
            return update

    # Attempt 2: JS Render (2 credits)
    logger.info("  Attempt 2 — JS RENDER (2 credits)")
    html = fetch_html(url, js=True)
    if html:
        soup   = BeautifulSoup(html, "html.parser")
        update = build_from_html(soup, cfg)
        if update.get(cfg["cur_col"]):
            logger.info("  ✓ JS RENDER — 2 credits")
            return update

    # Attempt 3: AI (5 credits) — last resort
    logger.info("  Attempt 3 — AI LAST RESORT (5 credits)")
    ai_data = fetch_ai(url)
    update  = build_from_ai(ai_data, cfg)
    if update:
        logger.info("  ✓ AI — 5 credits")
    else:
        logger.error("  ✗ Sab attempts fail")
    return update


# ═══════════════════════════════════════════════════════════════════════════
# TABLE PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════

def process_table(name: str, cfg: dict):
    logger.info(f"\n{'━'*60}\n  TABLE: {name}\n{'━'*60}")
    try:
        rows = supabase.table(name).select("*").execute().data or []
    except Exception as e:
        logger.error(f"  Supabase error: {e}"); return

    logger.info(f"  {len(rows)} products")
    lc = cfg["link_col"]
    ok = fail = skip = 0

    for i, row in enumerate(rows, 1):
        url = (row.get(lc) or "").strip()
        if not url: skip += 1; continue

        logger.info(f"\n  [{i}/{len(rows)}] {url[:80]}")
        try:
            update = scrape_row(url, cfg)
        except Exception as e:
            logger.error(f"  Exception: {e}"); fail += 1; time.sleep(3); continue

        if not update: fail += 1; time.sleep(2); continue

        for k, v in update.items():
            logger.info(f"    {k}: {v!r}")

        try:
            supabase.table(name).update(update).eq(lc, url).execute()
            logger.info("    ✓ Supabase updated"); ok += 1
        except Exception as e:
            logger.error(f"    Supabase error: {e}"); fail += 1

        time.sleep(1)

    logger.info(f"\n  DONE — ok={ok}  fail={fail}  skip={skip}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("="*60)
    logger.info("  Flipkart Scraper — Accurate + Verified")
    logger.info(f"  Keys: {len(_WS_KEYS)} | STATIC(1) → JS(2) → AI(5)")
    logger.info("="*60)
    for name, cfg in TABLE_CONFIG.items():
        try:
            process_table(name, cfg)
        except Exception as e:
            logger.error(f"FATAL '{name}': {e}"); continue
    logger.info("\n"+"="*60+"\n  ALL DONE\n"+"="*60)

if __name__ == "__main__":
    main()

