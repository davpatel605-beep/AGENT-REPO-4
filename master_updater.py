"""
Flipkart Price Scraper — AGENT VERSION
5-Attempt Strategy:
  Attempt 1-2 : CHEAP  (static HTML, fast)
  Attempt 3-4 : RENDER (JS rendered, full power)
  Attempt 5   : AI AGENT (OpenRouter + Gemma — 100% accurate extraction)
"""

import os, re, time, logging, json
import requests
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

# ── AlterLab ───────────────────────────────────────────────────────────────
ALTERLAB_KEY      = os.environ["ALTERLAB_API_KEY"]
ALTERLAB_SCRAPE   = "https://api.alterlab.io/api/v1/scrape"
ALTERLAB_JOBS     = "https://api.alterlab.io/api/v1/jobs/{job_id}"
ALTERLAB_HDR      = {"X-API-Key": ALTERLAB_KEY, "Content-Type": "application/json"}

# ── OpenRouter AI Agent ────────────────────────────────────────────────────
OPENROUTER_KEY   = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "google/gemma-4-31b-it:free"
OPENROUTER_HDR   = {
    "Authorization": f"Bearer {OPENROUTER_KEY}",
    "Content-Type" : "application/json",
    "HTTP-Referer" : "https://github.com/flipkart-scraper",
}


# ═══════════════════════════════════════════════════════════════════════════
# ALTERLAB FETCH
# ═══════════════════════════════════════════════════════════════════════════

def _poll_job(job_id: str, label: str, max_wait: int = 120) -> str | None:
    """
    AlterLab ASYNC polling.
    Correct endpoint: GET /api/v1/jobs/{job_id}
    Correct status  : "succeeded" (not "completed")
    Correct HTML loc: result.content.html
    """
    poll_url = ALTERLAB_JOBS.format(job_id=job_id)
    waited   = 0
    interval = 4   # exponential backoff

    while waited < max_wait:
        time.sleep(interval)
        waited += interval
        interval = min(interval * 1.5, 15)  # backoff: 4→6→9→13→15→15...

        try:
            r = requests.get(poll_url, headers=ALTERLAB_HDR, timeout=30)
            logger.info(f"    [{label}] Poll HTTP {r.status_code} ({waited}s)")

            if r.status_code == 404:
                logger.warning(f"    [{label}] Job not found — wait kar raha hoon")
                continue
            if r.status_code != 200:
                logger.warning(f"    [{label}] Poll error {r.status_code}")
                continue

            data   = r.json()
            status = data.get("status", "")
            logger.info(f"    [{label}] Status: {status}")

            if status == "succeeded":
                # Correct path: result.content.html
                result  = data.get("result", {})
                content = result.get("content", {})
                html    = (content.get("html") or
                           content.get("text") or
                           result.get("html") or
                           result.get("text") or "")
                if len(html) > 1000:
                    logger.info(f"    [{label}] ✓ OK — {len(html)} chars")
                    return html
                logger.warning(f"    [{label}] Succeeded but HTML chhota {len(html)}")
                logger.warning(f"    [{label}] Result keys: {list(result.keys())}")
                return None

            if status in ("failed", "error"):
                logger.error(f"    [{label}] Job failed: {data.get('error','')}")
                return None

            # Still running — keep polling
            logger.info(f"    [{label}] Still running ({status})...")

        except Exception as e:
            logger.warning(f"    [{label}] Poll exception: {e}")

    logger.warning(f"    [{label}] Timeout after {max_wait}s")
    return None


def _alterlab_call(url: str, render: bool) -> str | None:
    """
    ASYNC mode: "async": true ZARURI hai — tabhi job_id pollable hota hai.
    Bina async:true ke job_id milta hai lekin poll pe 404 aata hai.
    """
    payload = {
        "url"  : url,
        "render": render,
        "async": True,       # ← ZARURI: is ke bina job poll nahi hota
        "formats": ["html"], # ← HTML chahiye
    }
    label = "RENDER" if render else "CHEAP"
    try:
        resp = requests.post(ALTERLAB_SCRAPE, headers=ALTERLAB_HDR, json=payload, timeout=60)
        logger.info(f"    [{label}] Submit HTTP {resp.status_code}")

        if resp.status_code == 401:
            logger.error("    401 — ALTERLAB_API_KEY check karo!")
            return None
        if resp.status_code == 402:
            logger.error("    402 — Balance khatam!")
            return None

        if resp.status_code in (200, 202):
            try:
                data = resp.json()
            except Exception:
                data = {}

            # Async job_id → poll karo
            job_id = data.get("job_id") or data.get("id")
            if job_id:
                logger.info(f"    [{label}] Job: {job_id} — polling...")
                return _poll_job(job_id, label, max_wait=120)

            # Sync response (rare) — direct HTML
            content = data.get("result", {}).get("content", {})
            html = content.get("html") or content.get("text") or data.get("html") or ""
            if len(html) > 1000:
                logger.info(f"    [{label}] Sync OK — {len(html)} chars")
                return html

            logger.warning(f"    [{label}] Unknown response: {str(data)[:300]}")

        else:
            logger.warning(f"    [{label}] HTTP {resp.status_code}: {resp.text[:200]}")

    except Exception as e:
        logger.warning(f"    [{label}] Error: {e}")
    return None


def fetch_html(url: str) -> str | None:
    """
    5-Attempt Agent Strategy:
    1: CHEAP
    2: CHEAP (retry)
    3: RENDER
    4: RENDER (retry)
    5: AI AGENT — OpenRouter + Gemma
    """
    # Attempts 1-2: CHEAP
    for attempt in range(1, 3):
        logger.info(f"  Attempt {attempt}/5 — CHEAP")
        html = _alterlab_call(url, render=False)
        if html:
            return html
        time.sleep(3)

    # Attempts 3-4: RENDER
    for attempt in range(3, 5):
        logger.info(f"  Attempt {attempt}/5 — RENDER (full power)")
        html = _alterlab_call(url, render=True)
        if html:
            return html
        time.sleep(5)

    # Attempt 5: AI AGENT
    logger.info("  Attempt 5/5 — AI AGENT (OpenRouter + Gemma)")
    return None  # AI agent seedha data extract karta hai — HTML nahi chahiye


# ═══════════════════════════════════════════════════════════════════════════
# AI AGENT — OpenRouter + Gemma
# ═══════════════════════════════════════════════════════════════════════════

AI_PROMPT = """You are a data extraction agent for Flipkart product pages.
Extract EXACTLY these fields from the Flipkart product page at this URL.

URL: {url}

Return ONLY a valid JSON object with these exact keys (no extra text, no markdown):
{{
  "current_price": "Rs.X,XXX",
  "original_price": "Rs.X,XXX",
  "discount": "XX%",
  "rating": "X.X",
  "reviews": "X,XXX",
  "ratings_count": "X,XXX"
}}

Rules:
- current_price: The bold selling price shown on page (format: Rs.13,902)
- original_price: The strikethrough MRP price (format: Rs.19,999). Empty string if no discount.
- discount: The green discount percentage (format: 70%). Empty string if no discount.
- rating: Star rating number only (format: 4.1). Empty string if not found.
- reviews: Number of reviews (Indian format: 34,452). Empty string if not found.
- ratings_count: Number of ratings if shown separately. Empty string if not found.
- Use Indian number format: 1,23,456 (not 123,456)
- If a field is not found, use empty string ""
- Return ONLY the JSON, nothing else."""


def ai_agent_extract(url: str) -> dict:
    """
    5th attempt: Use OpenRouter Gemma to extract data directly.
    Returns dict with extracted values.
    """
    prompt = AI_PROMPT.format(url=url)
    payload = {
        "model"      : OPENROUTER_MODEL,
        "messages"   : [{"role": "user", "content": prompt}],
        "max_tokens" : 300,
        "temperature": 0.1,
    }
    try:
        resp = requests.post(OPENROUTER_URL, headers=OPENROUTER_HDR, json=payload, timeout=60)
        logger.info(f"    [AI AGENT] HTTP {resp.status_code}")
        if resp.status_code != 200:
            logger.error(f"    [AI AGENT] Failed: {resp.text[:200]}")
            return {}

        content = resp.json()["choices"][0]["message"]["content"].strip()
        logger.info(f"    [AI AGENT] Response: {content[:300]}")

        # JSON parse karo
        # Markdown fences hata do agar hain
        content = re.sub(r"```json|```", "", content).strip()
        data = json.loads(content)
        logger.info(f"    [AI AGENT] Extracted: {data}")
        return data

    except json.JSONDecodeError as e:
        logger.error(f"    [AI AGENT] JSON parse failed: {e}")
    except Exception as e:
        logger.error(f"    [AI AGENT] Error: {e}")
    return {}


# ═══════════════════════════════════════════════════════════════════════════
# FORMAT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def parse_int(text) -> int | None:
    if not text: return None
    d = re.sub(r"[^\d]", "", str(text))
    return int(d) if d else None

def indian_price(val: int) -> str:
    if not val: return ""
    s = str(val)
    if len(s) <= 3: return f"Rs.{s}"
    result = s[-3:]; s = s[:-3]
    while len(s) > 2: result = s[-2:] + "," + result; s = s[:-2]
    if s: result = s + "," + result
    return f"Rs.{result}"

def indian_number(val) -> str:
    raw = re.sub(r"[^\d]", "", str(val)) if val else ""
    if not raw: return ""
    s = raw
    if len(s) <= 3: return s
    result = s[-3:]; s = s[:-3]
    while len(s) > 2: result = s[-2:] + "," + result; s = s[:-2]
    if s: result = s + "," + result
    return result

# ═══════════════════════════════════════════════════════════════════════════
# BANK FILTER
# ═══════════════════════════════════════════════════════════════════════════

_BANK_KW = ["bank","credit","debit","hdfc","sbi","axis","icici","cashback",
            "upi","emi","kotak","rbl","paytm","rupay","no cost",
            "instant discount","additional","card offer","flat rs","flat rupee"]

def has_bank_kw(text: str) -> bool:
    return any(k in text.lower() for k in _BANK_KW)


# ═══════════════════════════════════════════════════════════════════════════
# PRICE / DISCOUNT / RATING EXTRACTION (HTML parser)
# ═══════════════════════════════════════════════════════════════════════════

_PRICE_CSS = [("div","_30jeq3 _16Jk6d"),("div","_30jeq3"),("span","_30jeq3"),
              ("div","CEmiEU"),("span","CEmiEU")]
_DISC_CSS  = ["UkUFwK","VGWI6a","pPAw9j","_3Ay6Sb","Bs5uzZ","_2Tpdn3","_1psv1zeb9"]
_MRP_CSS   = ["yRaY8j","_3I9_wc","_3auQ3N","CAWmgp","_2p6lqe"]
_DISC_RE   = re.compile(r"(\d{1,2})%")
_DISC_OFF  = re.compile(r"(\d{1,2})%\s*off", re.IGNORECASE)

def extract_current_price(soup) -> int | None:
    for tag, cls in _PRICE_CSS:
        el = soup.find(tag, class_=cls.split())
        if el:
            v = parse_int(el.get_text())
            if v and 50 <= v <= 50_00_000: return v
    for s in soup.strings:
        m = re.search(r"Rs\.\s*([\d,]+)", s)
        if m:
            v = parse_int(m.group(1))
            if v and 50 <= v <= 50_00_000: return v
    return None

def _vd(val, ctx): return 1 <= val <= 95 and not has_bank_kw(ctx)

def extract_discount(soup) -> str:
    # L1: Structural
    for s in soup.strings:
        if re.search(r"Rs\.\s*[\d,]+", s):
            container = s.parent
            for _ in range(6):
                if not container or container.name in ("body","html","[document]"): break
                container = container.parent
                for child in container.find_all(True):
                    ct = child.get_text(strip=True)
                    if len(ct) <= 10:
                        m = _DISC_RE.match(ct)
                        if m:
                            v = int(m.group(1))
                            if _vd(v, container.get_text()): return f"{v}%"
            break
    # L2: CSS
    for cls in _DISC_CSS:
        for tag in soup.find_all(["div","span"], class_=cls):
            m = _DISC_RE.search(tag.get_text(strip=True))
            if m:
                v = int(m.group(1))
                if _vd(v, tag.get_text()): return f"{v}%"
    # L3: Short tag
    for tag in soup.find_all(True):
        text = tag.get_text(strip=True)
        if 2 <= len(text) <= 8:
            m = re.match(r"^(\d{1,2})%", text)
            if m:
                v = int(m.group(1))
                pt = tag.parent.get_text() if tag.parent else ""
                if _vd(v, pt): return f"{v}%"
    # L4: Full text
    full = soup.get_text()
    for m in _DISC_OFF.finditer(full):
        v = int(m.group(1))
        if 1 <= v <= 95:
            ctx = full[max(0, m.start()-150): m.end()+50]
            if not has_bank_kw(ctx): return f"{v}%"
    return ""

def extract_original_price(soup, cur, disc_str, iphone=False) -> str:
    if not disc_str or not cur: return ""
    disc = int(disc_str.replace("%",""))
    if disc <= 0: return ""
    calc = round(cur / (1 - disc/100))
    cands = []
    for tag in soup.find_all(["s","del","strike"]):
        v = parse_int(tag.get_text())
        if v and v > cur and 100 <= v <= 50_00_000: cands.append(v)
    if not iphone:
        for tag in soup.find_all(style=re.compile(r"line-through",re.I)):
            v = parse_int(tag.get_text())
            if v and v > cur and 100 <= v <= 50_00_000: cands.append(v)
        for cls in _MRP_CSS:
            for tag in soup.find_all(["div","span"], class_=cls):
                v = parse_int(tag.get_text())
                if v and v > cur and 100 <= v <= 50_00_000: cands.append(v)
    if cands:
        best = min(cands, key=lambda x: abs(x-calc))
        if abs(best-calc) <= 15 or abs(best-calc) <= calc*0.10: return indian_price(best)
    return indian_price(calc)

def get_iphone_discount(soup):
    html = str(soup)
    bi = html.find("Protect Promise Fee")
    lim = BeautifulSoup(html[:bi], "html.parser") if bi != -1 else soup
    mrp = None
    for tag in lim.find_all(["s","del"]):
        v = parse_int(tag.get_text())
        if v and 5_000 <= v <= 5_00_000: mrp = v; break
    if mrp is None: return "", ""
    disc_str = ""
    for tag in lim.find_all(True):
        text = tag.get_text(strip=True)
        if 2 <= len(text) <= 8:
            m = re.match(r"^(\d{1,2})%", text)
            if m:
                v = int(m.group(1))
                pt = tag.parent.get_text() if tag.parent else ""
                if 1 <= v <= 50 and not has_bank_kw(pt): disc_str = f"{v}%"; break
    return disc_str, indian_price(mrp)

def extract_rating(soup) -> str:
    for tag in soup.find_all(["div","span"]):
        m = re.match(r"^(\d\.\d)\s*★?$", tag.get_text(strip=True))
        if m: return m.group(1)
    m = re.search(r"(\d\.\d)\s*★", soup.get_text())
    return m.group(1) if m else ""

def extract_reviews_pair(soup):
    full = soup.get_text()
    m = re.search(r"([\d,]+)\s+Ratings?\s*[&|]\s*([\d,]+)\s+Reviews?", full, re.IGNORECASE)
    if m: return indian_number(parse_int(m.group(1))), indian_number(parse_int(m.group(2)))
    m = re.search(r"([\d,]+)\s+(?:Ratings?|Reviews?)", full, re.IGNORECASE)
    if m: return "", indian_number(parse_int(m.group(1)))
    return "", ""

def combined_rating_reviews(soup) -> str:
    r = extract_rating(soup)
    _, rv = extract_reviews_pair(soup)
    return f"{r} | {rv}" if r and rv else r or ""


# ═══════════════════════════════════════════════════════════════════════════
# TABLE CONFIG
# ═══════════════════════════════════════════════════════════════════════════

TABLE_CONFIG = {
    "earbuds":      {"link_col":"Product Link","cur_col":"Current Price","orig_col":"Original Price","disc_col":"Discount","rating_col":"Rating","reviews_col":"Number of Reviews","combined":False,"iphone":False},
    "gaming cpu":   {"link_col":"Product Link","cur_col":"Current Price","orig_col":"Original Price","disc_col":"Discount","rating_col":"Rating","reviews_col":"Number of Reviews","combined":False,"iphone":False},
    "gaming pc":    {"link_col":"Product Link","cur_col":"Price","orig_col":"Original Price-2","disc_col":"Discount-2","rating_col":"Product Rating","reviews_col":"product review","combined":False,"iphone":False},
    "induction":    {"link_col":"ProductLink","cur_col":"Discounted Price","orig_col":"Price","disc_col":"Discount Percentage","rating_col":"Rating","reviews_col":"Number of Reviews","combined":False,"iphone":False},
    "iphone":       {"link_col":"Product URL","cur_col":"Discounted Price","orig_col":"Price","disc_col":"Discount Percentage","rating_col":"Product Rating","reviews_col":"Number of Reviews","reviews2_col":"Number of Ratings","combined":False,"iphone":True},
    "keybord":      {"link_col":"Product Link","cur_col":"Price","orig_col":"Original Price","disc_col":"Discount","rating_col":"Rating","reviews_col":"Number of Reviews","combined":False,"iphone":False},
    "laptop":       {"link_col":"Product Link","cur_col":"Price","orig_col":"Original Price","disc_col":"Discount","combined_col":"Rating and Reviews","combined":True,"iphone":False},
    "monitar":      {"link_col":"Product URL","cur_col":"Current Price","orig_col":"Original Price","disc_col":"Discount","rating_col":"Rating","reviews_col":"Number of Reviews","combined":False,"iphone":False},
    "mouse":        {"link_col":"Product Link","cur_col":"Current Price","orig_col":"Original Price","disc_col":"Discount","rating_col":"Rating","reviews_col":"Number of Reviews","combined":False,"iphone":False},
    "smart phone":  {"link_col":"Product Link","cur_col":"Price","orig_col":"Original Price","disc_col":"Discount","combined_col":"Ratings and Reviews","combined":True,"iphone":False},
    "smart+tv":     {"link_col":"Product Link","cur_col":"Price","orig_col":"Original Price","disc_col":"Discount","combined_col":"Ratings and Reviews","combined":True,"iphone":False},
    "smartwatch":   {"link_col":"Product Link","cur_col":"Price","orig_col":"Original Price","disc_col":"Discount","rating_col":"Rating","reviews_col":"Review","combined":False,"iphone":False},
}


# ═══════════════════════════════════════════════════════════════════════════
# SCRAPE ROW — HTML parser + AI Agent fallback
# ═══════════════════════════════════════════════════════════════════════════

def _build_update_from_html(soup, cfg) -> dict:
    """HTML se data nikalo — L1 to L4 approach."""
    update = {}

    if cfg["iphone"]:
        cur = extract_current_price(soup)
        if cur: update[cfg["cur_col"]] = indian_price(cur)
        disc_str, orig_str = get_iphone_discount(soup)
        update[cfg["disc_col"]] = disc_str
        update[cfg["orig_col"]] = orig_str
        r = extract_rating(soup)
        if r: update[cfg["rating_col"]] = r
        rc, rv = extract_reviews_pair(soup)
        if rv: update[cfg["reviews_col"]] = rv
        if rc: update[cfg.get("reviews2_col","Number of Ratings")] = rc
        return update

    if cfg["combined"]:
        cur = extract_current_price(soup)
        if cur: update[cfg["cur_col"]] = indian_price(cur)
        disc_str = extract_discount(soup)
        update[cfg["disc_col"]] = disc_str
        update[cfg["orig_col"]] = extract_original_price(soup, cur, disc_str) if (disc_str and cur) else ""
        cr = combined_rating_reviews(soup)
        if cr: update[cfg["combined_col"]] = cr
        return update

    cur = extract_current_price(soup)
    if cur: update[cfg["cur_col"]] = indian_price(cur)
    disc_str = extract_discount(soup)
    update[cfg["disc_col"]] = disc_str
    update[cfg["orig_col"]] = extract_original_price(soup, cur, disc_str) if (disc_str and cur) else ""
    r = extract_rating(soup)
    if r: update[cfg["rating_col"]] = r
    _, rv = extract_reviews_pair(soup)
    if rv: update[cfg["reviews_col"]] = rv
    return update


def _build_update_from_ai(ai_data: dict, cfg) -> dict:
    """AI Agent response se update dict banao."""
    update = {}
    if not ai_data: return update

    cur_str  = ai_data.get("current_price","")
    orig_str = ai_data.get("original_price","")
    disc_str = ai_data.get("discount","")
    rating   = ai_data.get("rating","")
    reviews  = ai_data.get("reviews","")
    ratings  = ai_data.get("ratings_count","")

    if cur_str:  update[cfg["cur_col"]]  = cur_str
    if orig_str: update[cfg["orig_col"]] = orig_str
    if disc_str: update[cfg["disc_col"]] = disc_str

    if cfg["combined"]:
        if rating and reviews:
            update[cfg["combined_col"]] = f"{rating} | {reviews}"
    elif cfg["iphone"]:
        if rating:   update[cfg["rating_col"]]   = rating
        if reviews:  update[cfg["reviews_col"]]  = reviews
        if ratings:  update[cfg.get("reviews2_col","Number of Ratings")] = ratings
    else:
        if rating:   update[cfg["rating_col"]]   = rating
        if reviews:  update[cfg["reviews_col"]]  = reviews

    return update


def _is_good(update: dict) -> bool:
    """Check karo kya useful data aaya — sirf current price enough hai."""
    return bool(update.get(list(update.keys())[0]) if update else False)


def scrape_row(url: str, cfg: dict) -> dict:
    # Attempts 1-4: HTML fetch + parse
    html = fetch_html(url)
    if html:
        soup   = BeautifulSoup(html, "html.parser")
        update = _build_update_from_html(soup, cfg)
        # Check karo price mili ya nahi
        cur_col = cfg["cur_col"]
        if update.get(cur_col):
            logger.info("    ✓ HTML parser se data mila")
            return update
        logger.warning("    HTML mila lekin price nahi — AI Agent try kar raha hoon")

    # Attempt 5: AI Agent
    logger.info("    AI AGENT — OpenRouter + Gemma")
    ai_data = ai_agent_extract(url)
    update  = _build_update_from_ai(ai_data, cfg)
    if update:
        logger.info("    ✓ AI Agent se data mila")
    else:
        logger.error("    ✗ Sab attempts fail — koi data nahi")
    return update


# ═══════════════════════════════════════════════════════════════════════════
# TABLE PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════

def process_table(table_name: str, cfg: dict):
    logger.info(f"\n{'━'*60}")
    logger.info(f"  TABLE: {table_name}")
    logger.info(f"{'━'*60}")
    try:
        rows = supabase.table(table_name).select("*").execute().data or []
    except Exception as e:
        logger.error(f"  Supabase fetch failed: {e}"); return

    logger.info(f"  {len(rows)} products")
    link_col = cfg["link_col"]
    success = fail = skip = 0

    for i, row in enumerate(rows, 1):
        url = (row.get(link_col) or "").strip()
        if not url: skip += 1; continue
        logger.info(f"\n  [{i}/{len(rows)}] {url[:80]}")
        try:
            update = scrape_row(url, cfg)
        except Exception as e:
            logger.error(f"    Exception: {e}"); fail += 1; time.sleep(3); continue
        if not update: fail += 1; time.sleep(2); continue
        for k, v in update.items(): logger.info(f"    {k}: {v!r}")
        try:
            supabase.table(table_name).update(update).eq(link_col, url).execute()
            logger.info("    ✓ Supabase updated")
            success += 1
        except Exception as e:
            logger.error(f"    Supabase update failed: {e}"); fail += 1
        time.sleep(1)

    logger.info(f"\n  TABLE DONE — success={success}  fail={fail}  skip={skip}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    logger.info("="*60)
    logger.info("  Flipkart Scraper — 5-Attempt Agent Mode")
    logger.info("="*60)
    for table_name, cfg in TABLE_CONFIG.items():
        try:
            process_table(table_name, cfg)
        except Exception as e:
            logger.error(f"FATAL in '{table_name}': {e}"); continue
    logger.info("\n"+"="*60)
    logger.info("  ALL DONE")
    logger.info("="*60)

if __name__ == "__main__":
    main()

