name: Update Gaming CPU Prices & Ratings

on:
  schedule:
    - cron: "0 3 * * *"   # Daily 08:30 AM IST
  workflow_dispatch:

jobs:
  update-gaming-cpu:
    name: Scrape Flipkart & Update Supabase
    runs-on: ubuntu-latest
    timeout-minutes: 60

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run update_gaming_cpu.py
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          SCRAPERAPI_KEY: ${{ secrets.SCRAPERAPI_KEY }}
        run: python update_gaming_cpu.py

      - name: Upload run log
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: gaming-cpu-log-${{ github.run_number }}
          path: "*.log"
          if-no-files-found: ignore
          retention-days: 7

