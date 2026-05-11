"""
Flipkart Price Scraper — FINAL VERSION
WebScraping.AI (2 keys) + Supabase + GitHub Actions

STRATEGY:
  1. Current Price  → text se direct nikalo (bold selling price)
  2. Discount %     → text se nikalo (green badge)
  3. Original Price → CALCULATE: cur / (1 - disc/100)
  4. Rating + Reviews → text se nikalo

FETCH:
  Attempt 1: STATIC HTML (1 credit)
  Attempt 2: JS Render   (2 credits)
  Attempt 3: AI/fields   (5 credits) — last resort
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

# ── WebScraping.AI — 2 keys auto-rotate ───────────────────────────────────
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
    logger.warning(f"  Key rotate → key {_ws_idx + 1}")


# ═══════════════════════════════════════════════════════════════════════════
# FETCH
# ═══════════════════════════════════════════════════════════════════════════

def fetch_html(url: str, js: bool) -> str | None:
    label  = "JS" if js else "STATIC"
    params = {"api_key": _key(), "url": url,
               "js": "true" if js else "false",
               "country": "in", "timeout": 15000}
    try:
        r = requests.get(WS_HTML, params=params, timeout=60)
        logger.info(f"    [{label}] HTTP {r.status_code} — {len(r.text)} chars")
        if r.status_code == 200 and len(r.text) > 500:
            return r.text
        if r.status_code == 402:
            logger.warning("    Credits khatam — rotating key")
            _rotate()
    except Exception as e:
        logger.warning(f"    [{label}] Error: {e}")
    return None


def fetch_ai(url: str) -> dict:
    fields = {
        "current_price": "The bold selling price customer pays NOW (e.g. Rs.899 or ₹899). NOT the strikethrough price.",
        "discount"     : "Discount percentage shown in green (e.g. 70%). Empty string if no discount.",
        "rating"       : "Star rating number (e.g. 4.1). Empty string if not shown.",
        "reviews"      : "Total reviews/ratings count Indian format (e.g. 34,452). Empty string if not shown.",
    }
    params = {"api_key": _key(), "url": url,
               "fields": json.dumps(fields),
               "country": "in", "js": "true"}
    try:
        r = requests.get(WS_AI, params=params, timeout=60)
        logger.info(f"    [AI] HTTP {r.status_code}")
        if r.status_code == 200:
            raw  = r.json()
            data = raw.get("result", raw) if isinstance(raw, dict) else {}
            logger.info(f"    [AI] Data: {data}")
            return data if isinstance(data, dict) else {}
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

def calc_original(cur: int, disc: int) -> str:
    """Original Price = Current / (1 - disc/100)"""
    if not cur or not disc or disc <= 0 or disc >= 100:
        return ""
    orig = round(cur / (1 - disc / 100))
    return indian_price(orig)

def to_rs(s: str) -> str:
    """₹1,999 → Rs.1,999"""
    s = str(s or "").strip()
    s = re.sub(r"^[₹Rs\.\s]+", "", s).strip()
    return f"Rs.{s}" if s else ""

_BANK_KW = ["bank","credit","debit","hdfc","sbi","axis","icici","cashback",
            "upi","emi","kotak","rbl","paytm","rupay","no cost",
            "instant discount","additional","card offer","flat rs","flat rupee"]

def has_bank_kw(t: str) -> bool:
    return any(k in t.lower() for k in _BANK_KW)


# ═══════════════════════════════════════════════════════════════════════════
# TEXT-BASED EXTRACTORS
# ═══════════════════════════════════════════════════════════════════════════

# Current price CSS classes (multiple Flipkart versions)
_CUR_CSS = [
    ("div",  "_30jeq3 _16Jk6d"), ("div",  "_30jeq3"), ("span", "_30jeq3"),
    ("div",  "CEmiEU"),           ("span", "CEmiEU"),
    ("div",  "hl05au"),           ("span", "hl05au"),
    ("div",  "Nx9bqj"),           ("span", "Nx9bqj"),
]

def extract_current_price(soup: BeautifulSoup) -> int | None:
    """
    Current price = bold selling price NOW.
    Method 1: Known CSS → Method 2: ₹ pattern → Method 3: Rs. pattern
    """
    # CSS classes
    for tag, cls in _CUR_CSS:
        el = soup.find(tag, class_=cls.split())
        if el:
            v = parse_int(el.get_text())
            if v and 50 <= v <= 50_00_000:
                return v

    # ₹ symbol (new Flipkart)
    for s in soup.strings:
        m = re.search(r"₹\s*([\d,]+)", s)
        if m:
            v = parse_int(m.group(1))
            if v and 50 <= v <= 50_00_000:
                return v

    # Rs. pattern
    for s in soup.strings:
        m = re.search(r"Rs\.\s*([\d,]+)", s)
        if m:
            v = parse_int(m.group(1))
            if v and 50 <= v <= 50_00_000:
                return v

    return None


_DISC_CSS = ["UkUFwK","VGWI6a","pPAw9j","_3Ay6Sb","Bs5uzZ","_2Tpdn3","_1psv1zeb9"]

def extract_discount(soup: BeautifulSoup) -> int | None:
    """
    Discount % from page.
    Multiple methods + bank offer filter.
    Returns integer (e.g. 70) or None.
    """
    text       = soup.get_text()
    candidates = []

    # L1: CSS badge classes
    for cls in _DISC_CSS:
        for tag in soup.find_all(["div","span"], class_=cls):
            t = tag.get_text(strip=True)
            m = re.match(r"^(\d{1,2})%", t)
            if m:
                v = int(m.group(1))
                if 1 <= v <= 95 and not has_bank_kw(t):
                    candidates.append(v)

    # L2: Short tags (<=8 chars)
    for tag in soup.find_all(True):
        t = tag.get_text(strip=True)
        if 2 <= len(t) <= 8:
            m = re.match(r"^(\d{1,2})%", t)
            if m:
                v = int(m.group(1))
                pt = tag.parent.get_text() if tag.parent else ""
                if 1 <= v <= 95 and not has_bank_kw(pt):
                    candidates.append(v)

    # L3: "X% off" pattern in full text
    for m in re.finditer(r"(\d{1,2})%\s*off", text, re.IGNORECASE):
        v = int(m.group(1))
        if 1 <= v <= 95:
            ctx = text[max(0, m.start()-100): m.end()+50]
            if not has_bank_kw(ctx):
                candidates.append(v)

    if not candidates:
        return None

    # Most common value = real discount (bank offers alag numbers hote hain)
    return Counter(candidates).most_common(1)[0][0]


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
# BUILD UPDATE DICT
# ═══════════════════════════════════════════════════════════════════════════

def build_from_html(soup: BeautifulSoup, cfg: dict) -> dict:
    """
    Step 1: Current price nikalo
    Step 2: Discount nikalo
    Step 3: Original = CALCULATE karo
    Step 4: Rating + Reviews nikalo
    """
    update = {}

    # ── iPhone special ────────────────────────────────────────────────────
    if cfg["iphone"]:
        html_str = str(soup)
        bi       = html_str.find("Protect Promise Fee")
        lim      = BeautifulSoup(html_str[:bi], "html.parser") if bi != -1 else soup

        cur  = extract_current_price(lim)
        disc = extract_discount(lim)

        if cur:
            update[cfg["cur_col"]] = indian_price(cur)
        if disc:
            update[cfg["disc_col"]] = f"{disc}%"
            update[cfg["orig_col"]] = calc_original(cur, disc)
        else:
            update[cfg["disc_col"]] = ""
            update[cfg["orig_col"]] = ""

        rating = extract_rating(lim)
        if rating: update[cfg["rating_col"]] = rating
        rc, rv = extract_reviews(lim)
        if rv: update[cfg["reviews_col"]] = rv
        if rc: update[cfg.get("reviews2_col", "Number of Ratings")] = rc
        return update

    # ── Standard / Combined ───────────────────────────────────────────────
    cur  = extract_current_price(soup)
    disc = extract_discount(soup)

    logger.info(f"    Current price : {cur}")
    logger.info(f"    Discount      : {disc}%")

    if cur:
        update[cfg["cur_col"]] = indian_price(cur)

    if disc:
        update[cfg["disc_col"]] = f"{disc}%"
        # Original Price = CALCULATE (not scrape)
        update[cfg["orig_col"]] = calc_original(cur, disc)
        logger.info(f"    Original calc : {update[cfg['orig_col']]}")
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


def build_from_ai(ai_data: dict, cfg: dict) -> dict:
    """
    AI response se build.
    Original price = calculate (not from AI).
    """
    update = {}
    if not ai_data: return update

    cur_str  = to_rs(str(ai_data.get("current_price") or ""))
    disc_str = str(ai_data.get("discount") or "").strip()
    rating   = str(ai_data.get("rating")   or "").strip()
    reviews  = str(ai_data.get("reviews")  or "").strip()

    cur_int  = parse_int(cur_str)
    disc_int = parse_int(disc_str)

    if cur_int:
        update[cfg["cur_col"]] = cur_str

    if disc_int and 1 <= disc_int <= 95:
        update[cfg["disc_col"]] = f"{disc_int}%"
        # Original = CALCULATE
        update[cfg["orig_col"]] = calc_original(cur_int, disc_int)
        logger.info(f"    [AI] cur={cur_str} disc={disc_int}% orig={update[cfg['orig_col']]}")
    else:
        update[cfg["disc_col"]] = ""
        update[cfg["orig_col"]] = ""

    if cfg["combined"]:
        if rating and reviews:
            update[cfg["combined_col"]] = f"{rating} | {reviews}"
    else:
        if rating:  update[cfg.get("rating_col",  "Rating")]            = rating
        if reviews: update[cfg.get("reviews_col", "Number of Reviews")] = reviews

    return update


# ═══════════════════════════════════════════════════════════════════════════
# TABLE CONFIG
# ═══════════════════════════════════════════════════════════════════════════

TABLE_CONFIG = {
    "earbuds": {
        "link_col":"Product Link", "cur_col":"Current Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False,
    },
    "gaming cpu": {
        "link_col":"Product Link", "cur_col":"Current Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False,
    },
    "gaming pc": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price-2", "disc_col":"Discount-2",
        "rating_col":"Product Rating", "reviews_col":"product review",
        "combined":False, "iphone":False,
    },
    "induction": {
        "link_col":"ProductLink", "cur_col":"Discounted Price",
        "orig_col":"Price", "disc_col":"Discount Percentage",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False,
    },
    "iphone": {
        "link_col":"Product URL", "cur_col":"Discounted Price",
        "orig_col":"Price", "disc_col":"Discount Percentage",
        "rating_col":"Product Rating", "reviews_col":"Number of Reviews",
        "reviews2_col":"Number of Ratings",
        "combined":False, "iphone":True,
    },
    "keybord": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False,
    },
    "laptop": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "combined_col":"Rating and Reviews",
        "combined":True, "iphone":False,
    },
    "monitar": {
        "link_col":"Product URL", "cur_col":"Current Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False,
    },
    "mouse": {
        "link_col":"Product Link", "cur_col":"Current Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Number of Reviews",
        "combined":False, "iphone":False,
    },
    "smart phone": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "combined_col":"Ratings and Reviews",
        "combined":True, "iphone":False,
    },
    "smart+tv": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "combined_col":"Ratings and Reviews",
        "combined":True, "iphone":False,
    },
    "smartwatch": {
        "link_col":"Product Link", "cur_col":"Price",
        "orig_col":"Original Price", "disc_col":"Discount",
        "rating_col":"Rating", "reviews_col":"Review",
        "combined":False, "iphone":False,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# SCRAPE ROW
# ═══════════════════════════════════════════════════════════════════════════

def scrape_row(url: str, cfg: dict) -> dict:
    # Attempt 1: STATIC (1 credit)
    logger.info("  Attempt 1 — STATIC (1 credit)")
    html = fetch_html(url, js=False)
    if html:
        soup   = BeautifulSoup(html, "html.parser")
        update = build_from_html(soup, cfg)
        if update.get(cfg["cur_col"]):
            logger.info("  ✓ STATIC — data mila (1 credit)")
            return update

    # Attempt 2: JS Render (2 credits)
    logger.info("  Attempt 2 — JS RENDER (2 credits)")
    html = fetch_html(url, js=True)
    if html:
        soup   = BeautifulSoup(html, "html.parser")
        update = build_from_html(soup, cfg)
        if update.get(cfg["cur_col"]):
            logger.info("  ✓ JS RENDER — data mila (2 credits)")
            return update

    # Attempt 3: AI (5 credits) — last resort
    logger.info("  Attempt 3 — AI LAST RESORT (5 credits)")
    ai    = fetch_ai(url)
    update = build_from_ai(ai, cfg)
    if update:
        logger.info("  ✓ AI — data mila (5 credits)")
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
    logger.info("  Flipkart Scraper — Final Version")
    logger.info(f"  Keys: {len(_WS_KEYS)} | STATIC(1) → JS(2) → AI(5)")
    logger.info("="*60)

    for name, cfg in TABLE_CONFIG.items():
        try:
            process_table(name, cfg)
        except Exception as e:
            logger.error(f"FATAL '{name}': {e}"); continue

    logger.info("\n" + "="*60 + "\n  ALL DONE\n" + "="*60)

if __name__ == "__main__":
    main()
