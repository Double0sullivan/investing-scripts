#!/usr/bin/env python3
"""
Market Pulse — Live Price Refresher
------------------------------------
Fetches current S&P 500 and Nasdaq prices from Yahoo Finance
and writes them into your Market_Pulse_ATH_Tracker.xlsx spreadsheet.

Usage:
    python refresh_prices.py

Requirements:
    pip install yfinance openpyxl
"""

import sys
import datetime

try:
    import yfinance as yf
except ImportError:
    print("Installing yfinance...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance", "-q"])
    import yfinance as yf

from openpyxl import load_workbook

# ── Config ────────────────────────────────────────────────────────────────────
SPREADSHEET = "Market_Pulse_ATH_Tracker.xlsx"   # path to your spreadsheet
SP500_CELL  = "C9"    # cell where S&P 500 live price goes
NASDAQ_CELL = "C18"   # cell where Nasdaq live price goes
DATE_CELL   = "B4"    # cell showing last updated date
SHEET_NAME  = "Dashboard"

TICKERS = {
    "S&P 500":  "^GSPC",
    "Nasdaq":   "^IXIC",
}

# Cells for 52-week highs (auto-updated)
SP500_52WH_CELL  = "C8"   # S&P 500 all-time/52W high
NASDAQ_52WH_CELL = "C17"  # Nasdaq all-time/52W high

# ── Fetch prices ──────────────────────────────────────────────────────────────
def get_price_and_52wh(ticker_symbol: str):
    ticker = yf.Ticker(ticker_symbol)
    data = ticker.history(period="1d", interval="1m")
    if data.empty:
        data = ticker.history(period="2d")
    if data.empty:
        raise ValueError(f"No data returned for {ticker_symbol}")
    price   = round(float(data["Close"].iloc[-1]), 2)
    info    = ticker.info
    high_52w = info.get("fiftyTwoWeekHigh")
    high_52w = round(float(high_52w), 2) if high_52w else None
    return price, high_52w

def main():
    print("=" * 50)
    print("  Market Pulse — Live Price Refresher")
    print("=" * 50)

    # Fetch prices
    prices = {}
    highs  = {}
    for name, symbol in TICKERS.items():
        try:
            price, high_52w = get_price_and_52wh(symbol)
            prices[name] = price
            highs[name]  = high_52w
            high_str = f"  52W High: {high_52w:,.2f}" if high_52w else ""
            print(f"  + {name:12s}  Price: {price:,.2f}{high_str}")
        except Exception as e:
            print(f"  ! {name:12s}  ERROR: {e}")
            prices[name] = None
            highs[name]  = None

    if all(v is None for v in prices.values()):
        print("\n  Could not fetch any prices. Check your internet connection.")
        sys.exit(1)

    # Update spreadsheet
    try:
        wb = load_workbook(SPREADSHEET)
    except FileNotFoundError:
        print(f"\n  ERROR: '{SPREADSHEET}' not found.")
        print(f"  Make sure this script is in the same folder as the spreadsheet.")
        sys.exit(1)

    ws = wb[SHEET_NAME]

    if prices["S&P 500"] is not None:
        ws[SP500_CELL].value         = prices["S&P 500"]
        ws[SP500_CELL].number_format = "#,##0.00"
    if highs["S&P 500"] is not None:
        ws[SP500_52WH_CELL].value         = highs["S&P 500"]
        ws[SP500_52WH_CELL].number_format = "#,##0.00"

    if prices["Nasdaq"] is not None:
        ws[NASDAQ_CELL].value         = prices["Nasdaq"]
        ws[NASDAQ_CELL].number_format = "#,##0.00"
    if highs["Nasdaq"] is not None:
        ws[NASDAQ_52WH_CELL].value         = highs["Nasdaq"]
        ws[NASDAQ_52WH_CELL].number_format = "#,##0.00"

    # Update the "last updated" timestamp
    now = datetime.datetime.now().strftime("%B %d, %Y at %I:%M %p")
    ws[DATE_CELL] = f"Last updated: {now}"

    wb.save(SPREADSHEET)

    print()
    print(f"  ✓ Spreadsheet updated: {SPREADSHEET}")
    print(f"  ✓ Timestamp: {now}")
    print()
    print("  Open Market_Pulse_ATH_Tracker.xlsx to see the latest drawdowns.")
    print("=" * 50)

if __name__ == "__main__":
    main()
