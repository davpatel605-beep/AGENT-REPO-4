"""
master_updater.py — Universal Flipkart Price Updater  (FINAL PERFECT VERSION)
==============================================================================
Tables: earbuds → gaming cpu → gaming pc → induction → iphone → keybord →
        laptop → monitar → mouse → smart phone → smart+tv → smartwatch

Schedule: Every 3 days at 7:00 AM IST (1:30 AM UTC)
GitHub Actions cron: "30 1 */3 * *"

API: AlterLab  (https://alterlab.io)
  Secret name  : ALTERLAB_API_KEY
  Install      : pip install alterlab
  CHEAP        : client.scrape_html(url)          → result.html
  RENDER (JS)  : client.scrape_js(url)            → result.html

══════════════════════════════════════════════════════════════════════════════
VISUAL PATTERN ON FLIPKART PAGE (exact screenshot layout):
══════════════════════════════════════════════════════════════════════════════

  ┌─────────────────────────────────────────────────────┐
  │  Product Name                                       │
  │                                                     │
  │  4.1 ★  |  1,01,973    ← rating=4.1  reviews=101973│
  │                                                     │
  │  ↓ 70%   2̶,̶9̶9̶9̶   ₹899  ← disc=70%  orig  current  │
  │  (green) (silver,      (bold,                       │
  │  arrow   line-through) black)                       │
  └─────────────────────────────────────────────────────┘

  iPhone page (NO discount):
  ┌─────────────────────────────────────────────────────┐
  │  ₹69,900    ← just current price, nothing else      │
  │  (no arrow, no strikethrough number)                │
  └─────────────────────────────────────────────────────┘

══════════════════════════════════════════════════════════════════════════════
STRIKETHROUGH DIGITS (original/MRP price appears with line through each digit):
══════════════════════════════════════════════════════════════════════════════

  The number 2,999 with line-through looks like:  2̶,̶9̶9̶9̶
  Each digit with a horizontal line through its middle:
    0̶  1̶  2̶  3̶  4̶  5̶  6̶  7̶  8̶  9̶

  In HTML this is encoded as ONE of these four ways:
    <s>2,999</s>                              ← s tag      (MOST COMMON on Flipkart)
    <del>2,999</del>                          ← del tag    (semantic)
    <strike>2,999</strike>                    ← strike tag (deprecated but used)
    style="text-decoration: line-through"     ← CSS inline style

══════════════════════════════════════════════════════════════════════════════
CORE RULES:
══════════════════════════════════════════════════════════════════════════════
  1.  DISCOUNT FIRST — extract disc before everything else
  2.  If disc="" → orig="" → do NOT calculate anything (e.g. iPhone)
  3.  Current price — already working, NOT changed
  4.  Original price — calc from cur+disc, then verify against strikethrough
  5.  Reviews + Rating — NOT TOUCHED (working perfectly)
  6.  iphone table — swap=True, uses get_iphone_discount() (strict version)
  7.  math_fallbacks — NEVER auto-generate discount from nothing
  8.  Non-cancel policy — runs until all tables complete

══════════════════════════════════════════════════════════════════════════════
DISCOUNT EXTRACTION MASTERY — WHY PREVIOUS APPROACHES FAILED:
══════════════════════════════════════════════════════════════════════════════

  PROBLEM 1: Arrow is CSS/SVG — NOT in soup.get_text()
    Unicode search r"[↓▼⬇]" in ft ALWAYS fails. Stop looking for arrow.

  PROBLEM 2: Bank offers also say "X% off"
    Solution: Check 150-char context for bank keywords, reject if found.

  PROBLEM 3: CSS classes change frequently
    Solution: Use structural position (price container), not just class names.

  PROBLEM 4: Short tag search picks wrong tags
    Solution: Search INSIDE the price block container only.

  FINAL SOLUTION — 4 LAYER APPROACH:
    L1: Structural — current price tag DOM → walk up → find X% in container
    L2: Known CSS badge classes (fallback)
    L3: All short tags <= 8 chars with X% pattern — bank filter
    L4: Full text "X% off" with strict 150-char bank filter

══════════════════════════════════════════════════════════════════════════════
iPHONE SPECIAL LOGIC:
══════════════════════════════════════════════════════════════════════════════

  iPhone uses get_iphone_discount() — COMPLETELY SEPARATE from get_discount()

  KEY INSIGHT:
    Flipkart variant buttons (512GB "Out of stock") use CSS line-through on
    their price → causes false strikethrough detection → wrong orig+disc
    Solution: For iPhone, ONLY accept <s> and <del> HTML tags as strikethrough
              CSS line-through = COMPLETELY IGNORED for iPhone

  BOUNDARY:
    Cut page text at "Protect Promise Fee" — everything after = variants/offers
    Strikethrough and % must BOTH be found before this boundary

  PATTERN:
    Discount iPhone:    ↓8%  ₹74,̶9̶0̶0̶  ₹68,900  → disc=8% orig=74900 cur=68900
    No-discount iPhone: ₹69,900  → disc="" orig="" cur=69900

══════════════════════════════════════════════════════════════════════════════
ORIGINAL PRICE MASTERY — ALGORITHM:
══════════════════════════════════════════════════════════════════════════════

  Step 1: calc = round(cur / (1 - disc/100))
          e.g. cur=899, disc=70% → calc = round(899/0.30) = 2,997

  Step 2: Collect ALL strikethrough numbers from page:
          <s>, <del>, <strike>, style=line-through, Flipkart CSS classes
          Accept only: number > cur AND 100 <= number <= 50,00,000

  Step 3: Generate 5 tolerance possibilities around calc:
          [calc*0.98, calc*0.99, calc, calc*1.01, calc*1.02]
          e.g. calc=2997 → [2937, 2967, 2997, 3027, 3057]
          Check if any page number falls in this range.

  Step 4: Match decision:
          diff <= 15 rupees → use page number (exact match)
          diff <= 10%       → use page number (close match)
          diff > 10%        → use calc (page number is wrong)
          No page found     → use calc only
"""

import os, re, time, logging
from bs4 import BeautifulSoup
from alterlab import AlterLab
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Env vars ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_KEY"].strip()
# ALTERLAB_API_KEY is read automatically by AlterLab SDK from env

# AlterLab SDK client — reads ALTERLAB_API_KEY from environment automatically
alterlab = AlterLab(timeout=90, max_retries=2, retry_delay=2.0)

DELAY       = 1
MAX_REVIEWS = 500000


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
        "link": "ProductLink",
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
        "iphone": True,
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
# FETCH  — AlterLab Python SDK
# ══════════════════════════════════════════════════════════════════════════════
def fetch(url: str, render: bool = False):
    """
    AlterLab SDK:
      CHEAP  : alterlab.scrape_html(url)  → result.html  (static HTML, cheapest)
      RENDER : alterlab.scrape_js(url)    → result.html  (JS rendered, for SPAs)
    SDK reads ALTERLAB_API_KEY automatically from environment.
    """
    mode = "RENDER" if render else "CHEAP"
    try:
        if render:
            result = alterlab.scrape_js(url)
        else:
            result = alterlab.scrape_html(url)
        html = result.html or ""
        if not html:
            log.warning(f"   [{mode}] Empty HTML")
            return None
        log.info(f"   [{mode}] OK  cost=${result.billing.cost_dollars:.4f}  tier={result.billing.tier_used}")
        return BeautifulSoup(html, "html.parser")
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
# CURRENT PRICE  — NOT CHANGED (working perfectly)
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
# DISCOUNT  — FINAL 4-LAYER MASTERY VERSION (all tables except iphone)
# ══════════════════════════════════════════════════════════════════════════════
def get_discount(soup, ft):
    BANK_KW = [
        "bank", "credit", "debit", "hdfc", "sbi", "axis", "icici",
        "cashback", "upi", "emi", "kotak", "rbl", "paytm", "rupay",
        "no cost", "instant discount", "additional", "card offer",
        "flat rs", "flat rupee",
    ]

    def has_bank(text):
        t = text.lower()
        return any(kw in t for kw in BANK_KW)

    def valid_disc(val):
        return 1 <= val <= 99

    # ── L1: Structural — price container ke andar dhundho ─────────────────────
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
                        log.info(f"   [DISC-L1-STRUCT lvl={level}] {val}%")
                        return f"{val}%"
            node = node.parent

    # ── L2: Flipkart CSS discount badge classes ────────────────────────────────
    CSS_BADGE_SELS = [
        "div._1psv1zeb9._1psv1ze0._1psv1zedr",
        "div.UkUFwK", "span.UkUFwK",
        "div._3Ay6Sb", "span._2Tpdn3",
        "div.VGWC4j",
        "span._2p6lqe",
    ]
    for sel in CSS_BADGE_SELS:
        try:
            tag = soup.select_one(sel)
        except Exception:
            continue
        if not tag:
            continue
        t = tag.get_text(strip=True)
        m = re.search(r"(\d{1,2})\s*%", t)
        if m:
            val = int(m.group(1))
            if valid_disc(val):
                log.info(f"   [DISC-L2-CSS] {val}% via {sel}")
                return f"{val}%"

    # ── L3: All short tags <= 8 chars — bank filter on parent ─────────────────
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
        parent_text = safe(tag.parent)[:250] if tag.parent else ""
        if has_bank(parent_text):
            continue
        log.info(f"   [DISC-L3-SHORTTAG] {val}%")
        return f"{val}%"

    # ── L4: Full text "X% off" — strict 150-char bank filter ──────────────────
    for m in re.finditer(r"(\d{1,2})\s*%\s+off\b", ft, re.I):
        val = int(m.group(1))
        if not valid_disc(val):
            continue
        ctx = ft[max(0, m.start() - 150): m.end() + 80]
        if not has_bank(ctx):
            log.info(f"   [DISC-L4-TEXT] {val}%")
            return f"{val}%"

    log.info("   [DISC] Not found — returning ''")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# iPHONE DISCOUNT — STRICT SEPARATE VERSION
# ══════════════════════════════════════════════════════════════════════════════
def get_iphone_discount(soup, ft, cur):
    cur_int = int(cur) if cur and cur.isdigit() else 0

    BOUNDARY_PHRASES = [
        "protect promise fee",
        "protect promise",
        "add to cart",
        "buy now",
    ]
    ft_zone = ft
    for phrase in BOUNDARY_PHRASES:
        idx = ft.lower().find(phrase)
        if idx != -1:
            ft_zone = ft[:idx]
            log.info(f"   [iPHONE-BOUNDARY] Cut at '{phrase}' pos={idx}")
            break
    log.info(f"   [iPHONE-ZONE] {len(ft_zone)} chars")

    strikethrough_val = ""
    for tag in soup.find_all(["s", "del", "strike"]):
        v = to_num(safe(tag))
        if not v or not v.isdigit() or not valid_price(v):
            continue
        if cur_int > 0 and int(v) <= cur_int:
            continue
        tag_text = safe(tag)
        if tag_text and (tag_text in ft_zone or to_num(tag_text) in ft_zone):
            strikethrough_val = v
            log.info(f"   [iPHONE-S-TAG-IN-ZONE] {v}")
            break

    if not strikethrough_val:
        log.info("   [iPHONE-DISC] No <s>/<del> in zone → disc='' orig=''")
        return "", ""

    BANK_KW = [
        "bank", "credit", "debit", "hdfc", "sbi", "axis", "icici",
        "cashback", "upi", "emi", "kotak", "rbl", "paytm", "rupay",
        "no cost", "instant", "additional", "card", "off on",
        "on hdfc", "on sbi", "on axis", "on icici", "on kotak",
        "offer", "coupon", "voucher", "wallet",
    ]

    def has_bank(text):
        return any(kw in text.lower() for kw in BANK_KW)

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
                    if 1 <= val <= 99 and t in ft_zone:
                        log.info(f"   [iPHONE-DISC-STRUCT] {val}%")
                        return f"{val}%", strikethrough_val
            node = node.parent

    for sel in ["div._1psv1zeb9._1psv1ze0._1psv1zedr", "div.UkUFwK", "span.UkUFwK"]:
        try:
            tag = soup.select_one(sel)
        except Exception:
            continue
        if not tag:
            continue
        t = tag.get_text(strip=True)
        m = re.search(r"(\d{1,2})\s*%", t)
        if m and t in ft_zone:
            val = int(m.group(1))
            if 1 <= val <= 99:
                log.info(f"   [iPHONE-DISC-CSS] {val}%")
                return f"{val}%", strikethrough_val

    for m in re.finditer(r"(\d{1,2})\s*%", ft_zone):
        val = int(m.group(1))
        if not (1 <= val <= 99):
            continue
        ctx = ft_zone[max(0, m.start() - 120): m.end() + 60]
        if not has_bank(ctx):
            log.info(f"   [iPHONE-DISC-ZONE-TEXT] {val}%")
            return f"{val}%", strikethrough_val

    if strikethrough_val and cur and cur.isdigit() and strikethrough_val.isdigit():
        orig_int = int(strikethrough_val)
        cur_int2 = int(cur)
        if orig_int > cur_int2:
            d = round((orig_int - cur_int2) / orig_int * 100)
            if 1 <= d <= 99:
                log.info(f"   [iPHONE-DISC-CALC] {d}% (from cur+s-tag)")
                return f"{d}%", strikethrough_val

    log.info("   [iPHONE-DISC] <s> found but % not confirmed → orig only, disc=''")
    return "", strikethrough_val


# ══════════════════════════════════════════════════════════════════════════════
# ORIGINAL PRICE  — FINAL MASTERY VERSION
# ══════════════════════════════════════════════════════════════════════════════
def get_original_price(soup, ft, cur, disc):
    if not cur or not str(cur).isdigit() or not disc:
        return ""
    d = disc.replace("%", "").strip()
    if not d.isdigit() or not (1 <= int(d) <= 99):
        return ""

    cur_int  = int(cur)
    disc_int = int(d)
    calc     = round(cur_int / (1 - disc_int / 100))
    log.info(f"   [ORIG-CALC] cur={cur} disc={disc}% → calc={calc}")

    min_v = cur_int + 1
    found = set()

    for tag in soup.find_all("s"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_v and valid_price(v):
            found.add(int(v))
            log.info(f"   [ORIG-S-TAG] {v}")

    for tag in soup.find_all("del"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_v and valid_price(v):
            found.add(int(v))
            log.info(f"   [ORIG-DEL-TAG] {v}")

    for tag in soup.find_all("strike"):
        v = to_num(safe(tag))
        if v and v.isdigit() and int(v) >= min_v and valid_price(v):
            found.add(int(v))
            log.info(f"   [ORIG-STRIKE-TAG] {v}")

    for tag in soup.find_all(True):
        style = tag.get("style", "")
        if "line-through" in style:
            v = to_num(safe(tag))
            if v and v.isdigit() and int(v) >= min_v and valid_price(v):
                found.add(int(v))
                log.info(f"   [ORIG-LINETHRU-CSS] {v}")

    MRP_SELS = [
        "div.v1zwn21m.v1zwn28._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.v1zwn21m._1psv1zeb9._1psv1ze0._1psv1zedi._1psv1zefu",
        "div.yRaY8j.ZYYwLA",
        "div.yRaY8j",
        "div._3I9_wc._2p6lqe",
        "div._3I9_wc",
    ]
    for sel in MRP_SELS:
        tag = soup.select_one(sel)
        if tag:
            v = to_num(safe(tag))
            if v and v.isdigit() and int(v) >= min_v and valid_price(v):
                found.add(int(v))
                log.info(f"   [ORIG-MRP-CLASS] {v} via {sel}")

    log.info(f"   [ORIG-PAGE-ALL] {sorted(found)}")

    five_pts = [round(calc * r) for r in [0.98, 0.99, 1.0, 1.01, 1.02]]
    log.info(f"   [ORIG-5-TOLERANCE] {five_pts}")

    if found:
        closest  = min(found, key=lambda x: abs(x - calc))
        diff_rs  = abs(closest - calc)
        diff_pct = diff_rs / calc if calc else 1

        if diff_rs <= 15:
            log.info(f"   [ORIG-EXACT diff=Rs.{diff_rs}] page={closest} calc={calc}")
            return str(closest)
        elif diff_pct <= 0.10:
            log.info(f"   [ORIG-NEAR diff={diff_pct:.1%}] page={closest} calc={calc}")
            return str(closest)
        else:
            log.info(f"   [ORIG-CALC-WIN diff=Rs.{diff_rs} {diff_pct:.1%}] calc={calc} page_was={closest}")
            return str(calc)

    log.info(f"   [ORIG-CALC-ONLY] {calc}")
    return str(calc)


# ══════════════════════════════════════════════════════════════════════════════
# RATING  — NOT TOUCHED (working perfectly)
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
# REVIEWS  — NOT TOUCHED (working perfectly)
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
                log.info(f"   [REVIEW-STRIP] {clean} → {stripped} (disc={disc_num})")
                clean = stripped
                val   = int(clean)
        elif len(clean) > 5 and clean[-2:] == disc_num:
            stripped = clean[:-2]
            if stripped.isdigit() and 1 <= int(stripped) <= MAX_REVIEWS:
                log.info(f"   [REVIEW-STRIP2] {clean} → {stripped} (disc={disc_num})")
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
            for pat in [re.escape(rating) + r"\s*[|]\s*([\d,]+)"]:
                m = re.search(pat, ft)
                if m:
                    v = validate_review(m.group(1), discount, force)
                    if v:
                        log.info(f"   [M3-TEXT] reviews={v}")
                        return v
        for sel in [
            "div._1psv1zeb9._1psv1ze0._1psv1zegu",
            "span.Wphh3N", "span._2_R_DZ", "span._13vcmD",
        ]:
            tag = soup.select_one(sel)
            if tag:
                for raw in re.findall(r"[\d,]+", safe(tag)):
                    v = validate_review(raw, discount, force)
                    if v:
                        log.info(f"   [M4-CSS] reviews={v}")
                        return v
        for pat in [
            r"([\d,]+[kK]?)\s+[Rr]ating",
            r"([\d,]+[kK]?)\s+[Rr]eview",
            r"based on\s+([\d,]+[kK]?)\s+rating",
        ]:
            m = re.search(pat, ft, re.I)
            if m:
                v = validate_review(parse_k(m.group(1)), discount, force)
                if v:
                    log.info(f"   [M5-KEYWORD] reviews={v}")
                    return v
        return ""

    for attempt in range(1, 6):
        force  = attempt == 5
        result = try_methods(force=force)
        if result:
            return result
        if attempt < 5:
            log.warning(f"   [REVIEW] Attempt {attempt} failed, retrying...")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# MATH FALLBACKS  — ONLY cur + orig → disc  (never reverse)
# ══════════════════════════════════════════════════════════════════════════════
def math_fallbacks(cur, orig, disc):
    if cur and orig and not disc:
        if str(cur).isdigit() and str(orig).isdigit() and int(orig) > int(cur):
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
        if cur and "current_price" in cols:
            p[cols["current_price"]]  = fmt_price(cur)
        if orig and "original_price" in cols:
            p[cols["original_price"]] = fmt_price(orig)
    else:
        if cur and "current_price" in cols:
            p[cols["current_price"]]  = fmt_price(cur)
        if orig and "original_price" in cols:
            p[cols["original_price"]] = fmt_price(orig)
    if disc and "discount" in cols:
        p[cols["discount"]] = fmt_disc(disc)
    if "combined" in cols:
        if rating and reviews:
            p[cols["combined"]] = f"{rating} | {fmt_reviews(reviews)}"
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
            clean  = url.strip().rstrip("/")
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
    name      = cfg["name"]
    link_col  = cfg["link"]
    cols      = cfg["cols"]
    swap      = cfg.get("swap", False)
    is_iphone = cfg.get("iphone", False)

    log.info(f"\n{'=' * 70}")
    log.info(f"  TABLE: {name.upper()}  {'[SWAPPED]' if swap else ''}  {'[iPHONE-MODE]' if is_iphone else ''}")
    log.info(f"{'=' * 70}")

    rows = [
        r for r in client.table(name).select("*").execute().data
        if r.get(link_col, "").strip()
    ]
    log.info(f"  {len(rows)} products.")

    done = fail = 0
    for idx, row in enumerate(rows, 1):
        url = row[link_col].strip()
        log.info(f"\n  [{idx}/{len(rows)}] {url[:80]}")

        cur = orig = disc = rating = reviews = ""

        # Pass 1: CHEAP (static HTML — cheapest tier)
        soup1 = fetch(url, render=False)
        if soup1:
            ft1    = soup1.get_text(" ", strip=True)
            cur    = get_current_price(soup1, ft1)

            if is_iphone:
                disc, orig = get_iphone_discount(soup1, ft1, cur)
                if disc and orig:
                    orig_calc = get_original_price(soup1, ft1, cur, disc)
                    if orig_calc:
                        orig = orig_calc
            else:
                disc = get_discount(soup1, ft1)
                orig = get_original_price(soup1, ft1, cur, disc)

            rating  = get_rating(soup1, ft1)
            reviews = extract_review_number(soup1, ft1, rating, disc)
            log.info(
                f"   Pass1: cur={cur} disc={disc} orig={orig} "
                f"rating={rating} reviews={reviews}"
            )

        time.sleep(1)

        # Pass 2: RENDER (JS on) — only if anything missing
        needs_render = (not disc and not is_iphone) or not reviews or not rating
        if is_iphone:
            needs_render = not cur or not reviews or not rating
        if needs_render:
            reason = []
            if not disc and not is_iphone: reason.append("no disc")
            if not reviews:                reason.append("no reviews")
            if not rating:                 reason.append("no rating")
            if not cur and is_iphone:      reason.append("no cur")
            log.info(f"   Pass2 (RENDER) [{', '.join(reason)}]...")
            soup2 = fetch(url, render=True)
            if soup2:
                ft2 = soup2.get_text(" ", strip=True)
                if is_iphone:
                    if not disc:
                        disc2, orig2 = get_iphone_discount(soup2, ft2, cur)
                        if disc2:
                            disc = disc2
                            orig = orig2
                            if disc and orig:
                                orig_calc = get_original_price(soup2, ft2, cur, disc)
                                if orig_calc:
                                    orig = orig_calc
                else:
                    if not disc:
                        disc = get_discount(soup2, ft2)
                        if disc and not orig:
                            orig = get_original_price(soup2, ft2, cur, disc)
                r2  = get_rating(soup2, ft2)
                rv2 = extract_review_number(soup2, ft2, r2, disc)
                if not rating:  rating  = r2
                if not reviews: reviews = rv2
                if not cur:     cur     = get_current_price(soup2, ft2)
                if not orig and disc and not is_iphone:
                    orig = get_original_price(soup2, ft2, cur, disc)
                log.info(
                    f"   Pass2: disc={disc} orig={orig} "
                    f"rating={rating} reviews={reviews}"
                )
        else:
            log.info("   Pass2 skipped")

        # Math fallback: cur + orig → disc only (not for iphone)
        if not is_iphone:
            cur, orig, disc = math_fallbacks(cur, orig, disc)

        # Sanity: current must always be less than original
        if cur and orig and str(cur).isdigit() and str(orig).isdigit():
            if int(cur) >= int(orig):
                log.warning(
                    f"   SANITY FAIL: cur({cur}) >= orig({orig}) — clearing orig+disc"
                )
                orig = disc = ""

        payload = build_payload(cols, cur, orig, disc, rating, reviews, swap=swap)
        log.info(f"   PAYLOAD: {payload}")

        ok = update_db(client, name, link_col, url, payload)
        if ok:
            done += 1
        else:
            fail += 1

        time.sleep(DELAY)

    log.info(f"\n  {name}: Done={done}  Fail={fail}  Total={len(rows)}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN  — NON-CANCEL POLICY
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log.info("=" * 70)
    log.info(
        f"  MASTER FLIPKART UPDATER — {len(TABLES)} tables | "
        f"AlterLab SDK"
    )
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

    log.info("\n" + "=" * 70)
    log.info("  ALL TABLES COMPLETE")
    log.info("=" * 70)


if __name__ == "__main__":
    main()

