"""
test_alterlab.py — AlterLab API key test
"""
import os
import requests

key = os.environ.get("ALTERLAB_API_KEY", "NOT_FOUND")

print(f"Key length: {len(key)}")
print(f"Key starts with: {key[:15]}")
print(f"Key ends with: {key[-5:]}")
print(f"Has spaces: {' ' in key}")
print(f"Has newline: {chr(10) in key}")

# Direct API test — no SDK
url = "https://api.alterlab.io/v1/scrape"
headers = {"X-API-Key": key.strip()}
body = {"url": "https://httpbin.org/get"}

resp = requests.post(url, headers=headers, json=body, timeout=30)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:200]}")
