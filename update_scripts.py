#!/usr/bin/env python3
"""
update_scripts.py
------------------
Downloads the latest versions of all your investing scripts
straight from GitHub, overwriting the old ones automatically.

Usage:
    py update_scripts.py
"""

import urllib.request
import os
import sys

GITHUB_USER = "Double0sullivan"
GITHUB_REPO = "investing-scripts"
BRANCH      = "main"

BASE_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{BRANCH}"

SCRIPTS = [
    "refresh_prices.py",
    "refresh_watchlist.py",
    "build_stock_charts.py",
]

def download(filename):
    url      = f"{BASE_URL}/{filename}"
    dest     = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        urllib.request.urlretrieve(url, dest)
        print(f"  + {filename}")
    except Exception as e:
        print(f"  ! {filename}  —  {e}")

def main():
    print("=" * 50)
    print("  Investing Scripts — Auto Updater")
    print("=" * 50)
    print(f"\n  Fetching latest from GitHub ({GITHUB_USER}/{GITHUB_REPO})...\n")
    for script in SCRIPTS:
        download(script)
    print("\n  All done! Your scripts are up to date.")
    print("=" * 50)

if __name__ == "__main__":
    main()
