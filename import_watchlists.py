#!/usr/bin/env python3
"""
SOS Tracker — Bulk Watchlist Importer
---------------------------------------
Imports all CSV files from a folder into the dashboard database.
Each CSV should follow Yahoo Finance format:
  Row 1: column headers
  Row 2: watchlist name
  Row 3+: tickers (first column)

Usage:
    py import_watchlists.py

Place this script in the same folder as dashboard.py and your CSV files,
OR edit CSV_FOLDER below to point to a different folder.
"""

import sqlite3, os, sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
# By default looks for CSVs in the same folder as this script
# Change this if your CSVs are in a different location e.g:
# CSV_FOLDER = r"C:\Users\simon\Downloads\watchlists"
CSV_FOLDER = Path(__file__).parent
DB_PATH    = Path(__file__).parent / "marketpulse.db"

# ── Database helpers ──────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS watchlists (
            name TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS watchlist_tickers (
            watchlist TEXT,
            ticker    TEXT,
            position  INTEGER DEFAULT 0,
            PRIMARY KEY (watchlist, ticker),
            FOREIGN KEY (watchlist) REFERENCES watchlists(name) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS ticker_info (
            ticker      TEXT PRIMARY KEY,
            name        TEXT,
            market_cap  REAL,
            pe          REAL,
            div_yield   REAL,
            high_52w    REAL,
            low_52w     REAL,
            prev_close  REAL,
            fetched_date TEXT
        );
        CREATE TABLE IF NOT EXISTS price_history (
            ticker TEXT,
            date   TEXT,
            close  REAL,
            PRIMARY KEY (ticker, date)
        );
        CREATE TABLE IF NOT EXISTS live_prices (
            ticker      TEXT PRIMARY KEY,
            price       REAL,
            fetched_at  TEXT
        );
    ''')
    conn.commit()
    conn.close()

def import_csv(filepath):
    """Parse a Yahoo Finance CSV and import into DB. Returns (name, tickers, skipped)."""
    with open(filepath, encoding='utf-8-sig', errors='replace') as f:
        lines = [l.strip() for l in f.readlines()]

    # Filter empty lines
    lines = [l for l in lines if l]

    if len(lines) < 2:
        return None, [], 0

    # Row 2 = watchlist name (may be just a plain string or first CSV column)
    wl_name = lines[1].split(',')[0].strip().strip('"')
    if not wl_name:
        wl_name = Path(filepath).stem

    # Row 3+ = tickers (first column)
    tickers = []
    skipped = []
    for line in lines[2:]:
        t = line.split(',')[0].strip().strip('"').upper()
        if not t or t.startswith('#') or t == 'SYMBOL':
            continue
        # Basic sanity check — tickers shouldn't be too long or contain spaces
        if len(t) > 15 or ' ' in t:
            skipped.append(t)
            continue
        tickers.append(t)

    return wl_name, tickers, skipped

def save_watchlist(name, tickers):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO watchlists VALUES (?)", (name,))
    conn.execute("DELETE FROM watchlist_tickers WHERE watchlist=?", (name,))
    for i, t in enumerate(tickers):
        conn.execute(
            "INSERT OR IGNORE INTO watchlist_tickers VALUES (?,?,?)",
            (name, t, i)
        )
    conn.commit()
    conn.close()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 58)
    print("  SOS Tracker — Bulk Watchlist Importer")
    print("=" * 58)

    if not DB_PATH.exists():
        print("\n  Database not found — creating fresh database...")
        init_db()
        print("  Database created.")
    else:
        print(f"\n  Database: {DB_PATH.name}")
        init_db()  # ensure tables exist

    # Find all CSV files
    csv_files = sorted(Path(CSV_FOLDER).glob("*.csv"))
    if not csv_files:
        print(f"\n  No CSV files found in: {CSV_FOLDER}")
        print("  Make sure your CSV files are in the same folder as this script.")
        sys.exit(0)

    print(f"\n  Found {len(csv_files)} CSV file(s) in: {CSV_FOLDER}\n")

    ok = 0; failed = 0; total_tickers = 0

    for filepath in csv_files:
        try:
            name, tickers, skipped = import_csv(filepath)

            if not name or not tickers:
                print(f"  ! {filepath.name:40s} — empty or unreadable, skipped")
                failed += 1
                continue

            save_watchlist(name, tickers)
            total_tickers += len(tickers)
            skip_note = f"  ({len(skipped)} skipped)" if skipped else ""
            print(f"  + {filepath.name:40s} → \"{name}\"  ({len(tickers)} tickers{skip_note})")
            ok += 1

        except Exception as e:
            print(f"  ! {filepath.name:40s} — ERROR: {e}")
            failed += 1

    print()
    print(f"  Done:  {ok} watchlist(s) imported,  {failed} failed")
    print(f"  Total: {total_tickers} tickers across all watchlists")
    print()

    # Show what's now in the database
    conn = get_db()
    wls  = conn.execute("SELECT name FROM watchlists ORDER BY name").fetchall()
    conn.close()
    print(f"  Watchlists now in database ({len(wls)}):")
    for wl in wls:
        conn  = get_db()
        count = conn.execute(
            "SELECT COUNT(*) FROM watchlist_tickers WHERE watchlist=?",
            (wl['name'],)
        ).fetchone()[0]
        conn.close()
        print(f"    • {wl['name']}  ({count} tickers)")

    print()
    print("  Now run:  py dashboard.py")
    print("=" * 58)

if __name__ == "__main__":
    main()