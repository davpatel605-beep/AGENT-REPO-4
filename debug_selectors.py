"""
debug_selectors.py
Fetches ONE Flipkart product page and saves the HTML + prints all
price/rating related class names found, so we can fix the selectors.
"""

import os
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.environ["SCRAPERAPI_KEY"].strip()

# Paste any working product URL from your DB here
TEST_URL = "https://www.flipkart.com/ptron-bassbuds-tango-w-50hrs-playtime-ai-enc-calls-custom-eq-app-support-btv6-0-bluetooth/p/itmcf5b5a3a41bb1?pid=ACCHG53YBUHFBXEU&marketplace=FLIPKART"

params = {
    "api_key":      SCRAPERAPI_KEY,
    "url":          TEST_URL,
    "country_code": "in",
    "premium":      "true",
    "render":       "true",
}
full_url = f"https://api.scraperapi.com/?{urlencode(params)}"

print("Fetching page...")
resp = requests.get(full_url, timeout=90)
print(f"Status: {resp.status_code}")

html = resp.text

# Save full HTML for inspection
with open("page_debug.html", "w", encoding="utf-8") as f:
    f.write(html)
print("Full HTML saved to page_debug.html")

soup = BeautifulSoup(html, "html.parser")

# Print all divs/spans that likely contain price or rating
print("\n--- Searching for price-related elements ---")
keywords = ["price", "Price", "₹", "rating", "Rating", "review", "Review", "discount", "off"]
found = set()
for tag in soup.find_all(["div", "span"]):
    text = tag.get_text(strip=True)
    classes = " ".join(tag.get("class", []))
    if any(k in text for k in keywords) and classes and len(text) < 50:
        key = f"{tag.name}.{classes[:60]}  =>  '{text}'"
        if key not in found:
            found.add(key)
            print(key)

print(f"\nTotal matching elements: {len(found)}")
