#!/usr/bin/env python3
"""
SOS Tracker
----------------------
Local web dashboard: multiple watchlists, CSV import, charts, ATH tracker.

Usage:  py dashboard.py
Needs:  py -m pip install flask yfinance pandas plotly
"""

import sys, subprocess, threading, webbrowser, time, io, csv, sqlite3, json
from datetime import datetime, date, timedelta
from pathlib import Path

def ensure(pkg):
    try: __import__(pkg)
    except ImportError:
        print(f"  Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

for p in ["flask","yfinance","pandas","plotly"]:
    ensure(p)

from flask import Flask, jsonify, request, render_template_string
import yfinance as yf
import pandas as pd

app = Flask(__name__)

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "marketpulse.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():

    conn = get_db()
    c = conn.cursor()
    c.executescript('''
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
        CREATE TABLE IF NOT EXISTS custom_ath (
            symbol TEXT PRIMARY KEY,
            label  TEXT
        );
    ''')
    # Seed default watchlist if empty
    existing = c.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0]
    if existing == 0:
        c.execute("INSERT OR IGNORE INTO watchlists VALUES ('My Watchlist')")
        for i, t in enumerate(["AAPL","MSFT","GOOGL","NVDA","VOO"]):
            c.execute("INSERT OR IGNORE INTO watchlist_tickers VALUES (?,?,?)",
                      ("My Watchlist", t, i))
    conn.commit()
    conn.close()

init_db()


init_db()

def backup_db():
    import shutil
    try:
        backup = DB_PATH.with_suffix('.backup.db')
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, backup)
    except Exception as e:
        print(f'  Warning: backup failed — {e}')

backup_db()

ATH_SYMBOLS = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
}

TODAY = date.today().isoformat()

def get_watchlists_dict():
    conn = get_db()
    wls  = {r['name']: [] for r in conn.execute("SELECT name FROM watchlists ORDER BY name")}
    for r in conn.execute("SELECT watchlist, ticker FROM watchlist_tickers ORDER BY position"):
        if r['watchlist'] in wls:
            wls[r['watchlist']].append(r['ticker'])
    conn.close()
    return wls

def ensure_ticker_info(ticker):
    """Fetch and cache company info once per day."""
    conn  = get_db()
    row   = conn.execute("SELECT * FROM ticker_info WHERE ticker=?", (ticker,)).fetchone()
    today = date.today().isoformat()
    if row and row['fetched_date'] == today:
        conn.close()
        return dict(row)
    # Fetch fresh
    try:
        t    = yf.Ticker(ticker)
        info = t.info
        data = {
            'ticker':      ticker,
            'name':        info.get('shortName') or info.get('longName') or ticker,
            'market_cap':  info.get('marketCap'),
            'pe':          info.get('trailingPE'),
            'div_yield':   info.get('dividendYield'),
            'high_52w':    info.get('fiftyTwoWeekHigh'),
            'low_52w':     info.get('fiftyTwoWeekLow'),
            'prev_close':  info.get('previousClose') or info.get('regularMarketPreviousClose'),
            'fetched_date': today,
        }
        conn.execute('''INSERT OR REPLACE INTO ticker_info
            (ticker,name,market_cap,pe,div_yield,high_52w,low_52w,prev_close,fetched_date)
            VALUES (:ticker,:name,:market_cap,:pe,:div_yield,:high_52w,:low_52w,:prev_close,:fetched_date)''',
            data)
        conn.commit()
        conn.close()
        return data
    except Exception as e:
        conn.close()
        return {'ticker': ticker, 'name': ticker}

def get_live_price(ticker):
    """Fetch live price — always fresh when user requests refresh."""
    try:
        t = yf.Ticker(ticker)
        try:    price = t.fast_info.last_price
        except: price = t.info.get('currentPrice') or t.info.get('regularMarketPrice')
        if price:
            conn = get_db()
            conn.execute("INSERT OR REPLACE INTO live_prices VALUES (?,?,?)",
                         (ticker, round(float(price),4), datetime.now().isoformat()))
            conn.commit()
            conn.close()
        return price
    except:
        # Fall back to cached price
        conn  = get_db()
        row   = conn.execute("SELECT price FROM live_prices WHERE ticker=?", (ticker,)).fetchone()
        conn.close()
        return row['price'] if row else None

def ensure_price_history(ticker, period='5y'):
    """Fetch full price history once; only update with new days after that."""
    conn     = get_db()
    last_row = conn.execute(
        "SELECT MAX(date) as last FROM price_history WHERE ticker=?", (ticker,)
    ).fetchone()
    last_date = last_row['last'] if last_row else None
    conn.close()

    today = date.today().isoformat()

    if last_date == today:
        # Already up to date
        return

    if last_date is None:
        # First fetch — get full history (always use max so we have everything)
        fetch_period = 'max'
    else:
        # Only fetch missing days
        days_behind = (date.today() - date.fromisoformat(last_date)).days
        if days_behind <= 5:   fetch_period = '5d'
        elif days_behind <= 30: fetch_period = '1mo'
        elif days_behind <= 90: fetch_period = '3mo'
        else:                   fetch_period = period

    try:
        hist = yf.Ticker(ticker).history(period=fetch_period, auto_adjust=True)['Close']
        if hist.empty:
            return
        conn = get_db()
        for dt, price in hist.items():
            d_str = str(dt.date())
            conn.execute("INSERT OR REPLACE INTO price_history VALUES (?,?,?)",
                         (ticker, d_str, round(float(price), 4)))
        conn.commit()
        conn.close()
        print(f"  DB: {ticker} — {len(hist)} price rows stored")
    except Exception as e:
        print(f"  DB: {ticker} history error — {e}")

def get_price_history_from_db(ticker, period='5y'):
    """Read stored price history from DB."""
    period_days = {
        '1mo':30,'3mo':90,'6mo':180,'1y':365,
        '2y':730,'5y':1825,'10y':3650,'20y':7300,'max':99999
    }
    days  = period_days.get(period, 1825)
    since = (date.today() - timedelta(days=days)).isoformat()
    conn  = get_db()
    rows  = conn.execute(
        "SELECT date, close FROM price_history WHERE ticker=? AND date>=? ORDER BY date",
        (ticker, since)
    ).fetchall()
    conn.close()
    return [(r['date'], r['close']) for r in rows]

# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SOS Tracker</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
:root {
  --bg:#080d14; --panel:#0d1520; --border:#1a2535;
  --accent:#3b82f6; --accent2:#06b6d4;
  --green:#22c55e; --red:#ef4444;
  --text:#e2e8f0; --muted:#64748b; --yellow:#fbbf24;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;min-height:100vh;}

nav{
  display:flex;align-items:center;gap:0.5rem;
  padding:1rem 2rem;border-bottom:1px solid var(--border);
  background:rgba(13,21,32,0.97);position:sticky;top:0;z-index:100;
  backdrop-filter:blur(10px);flex-wrap:wrap;
}
.logo{
  font-family:'Syne',sans-serif;font-weight:800;font-size:1.1rem;
  color:var(--accent);letter-spacing:-0.02em;margin-right:1rem;
}
.logo span{color:var(--accent2);}
.nav-sep{width:1px;height:20px;background:var(--border);margin:0 0.5rem;}
nav button{
  background:none;border:none;color:var(--muted);
  font-family:'DM Mono',monospace;font-size:0.78rem;cursor:pointer;
  padding:0.35rem 0.7rem;border-radius:4px;transition:all 0.15s;
  letter-spacing:0.04em;white-space:nowrap;
}
nav button:hover{color:var(--text);background:var(--border);}
nav button.active{color:var(--accent);background:rgba(59,130,246,0.1);}
nav button.nav-wl{color:var(--accent2);}
nav button.nav-wl.active{color:var(--accent2);background:rgba(6,182,212,0.1);}

.page{display:none;padding:2rem;max-width:1400px;margin:0 auto;}
.page.active{display:block;}

h1{font-family:'Syne',sans-serif;font-size:1.8rem;font-weight:800;
   margin-bottom:0.3rem;letter-spacing:-0.03em;}
.subtitle{color:var(--muted);font-size:0.78rem;margin-bottom:2rem;}

.card{background:var(--panel);border:1px solid var(--border);
      border-radius:10px;padding:1.5rem;margin-bottom:1.5rem;}

.tbl-wrap{overflow-x:auto;}
table{width:100%;border-collapse:collapse;font-size:0.82rem;}
th{text-align:left;padding:0.7rem 1rem;color:var(--muted);font-weight:400;
   border-bottom:1px solid var(--border);white-space:nowrap;
   letter-spacing:0.06em;font-size:0.72rem;}
td{padding:0.85rem 1rem;border-bottom:1px solid rgba(26,37,53,0.6);white-space:nowrap;}
tr:last-child td{border-bottom:none;}
tr:hover td{background:rgba(59,130,246,0.04);}
.ticker-cell{font-weight:500;color:var(--accent);cursor:pointer;transition:color 0.15s;}
.ticker-cell:hover{color:var(--accent2);text-decoration:underline;}
.up{color:var(--green);} .down{color:var(--red);} .neu{color:var(--muted);}

.badge{display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;font-size:0.72rem;}
.badge.up{background:rgba(34,197,94,0.15);color:var(--green);}
.badge.down{background:rgba(239,68,68,0.15);color:var(--red);}

.toolbar{display:flex;gap:0.8rem;align-items:center;margin-bottom:1.2rem;flex-wrap:wrap;}
.last-updated{color:var(--muted);font-size:0.75rem;margin-left:auto;}

input[type=text]{
  background:var(--panel);border:1px solid var(--border);
  color:var(--text);font-family:'DM Mono',monospace;
  font-size:0.85rem;padding:0.6rem 1rem;border-radius:6px;
  outline:none;transition:border-color 0.15s;width:180px;
}
input[type=text]:focus{border-color:var(--accent);}
input[type=text]::placeholder{color:var(--muted);}

.btn{
  background:var(--accent);color:white;border:none;
  padding:0.6rem 1.2rem;border-radius:6px;cursor:pointer;
  font-family:'DM Mono',monospace;font-size:0.82rem;transition:opacity 0.15s;
  white-space:nowrap;
}
.btn:hover{opacity:0.85;}
.btn.danger{background:var(--red);}
.btn.secondary{background:var(--border);color:var(--muted);}
.btn.secondary:hover{color:var(--text);}
.btn.success{background:#065f46;color:#6ee7b7;}
.btn.success:hover{background:#047857;}

select{
  background:var(--panel);border:1px solid var(--border);
  color:var(--text);font-family:'DM Mono',monospace;
  font-size:0.82rem;padding:0.6rem 1rem;border-radius:6px;
  outline:none;cursor:pointer;
}
select:focus{border-color:var(--accent);}

/* ATH */
.ath-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;}
@media(max-width:700px){.ath-grid{grid-template-columns:1fr;}}
.ath-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:1.8rem;}
.ath-name{font-family:'Syne',sans-serif;font-size:1rem;font-weight:700;margin-bottom:0.3rem;}
.ath-price{font-size:2rem;font-weight:500;margin:0.5rem 0;}
.ath-row{display:flex;justify-content:space-between;margin-top:1rem;gap:1rem;flex-wrap:wrap;}
.ath-stat-label{color:var(--muted);font-size:0.72rem;margin-bottom:0.2rem;}
.ath-stat-val{font-size:1rem;font-weight:500;}
.progress-bar{height:4px;background:var(--border);border-radius:2px;margin-top:1.2rem;overflow:hidden;}
.progress-fill{height:100%;border-radius:2px;
  background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width 0.6s ease;}

/* CHART */
#chartDiv{width:100%;height:480px;}
#relChartDiv{width:100%;height:340px;margin-top:1.5rem;}
.chart-controls{display:flex;gap:0.8rem;align-items:center;margin-bottom:1.5rem;flex-wrap:wrap;}

/* IMPORT MODAL */
.modal-bg{
  display:none;position:fixed;inset:0;
  background:rgba(0,0,0,0.7);z-index:200;
  align-items:center;justify-content:center;
}
.modal-bg.open{display:flex;}
.modal{
  background:var(--panel);border:1px solid var(--border);
  border-radius:12px;padding:2rem;width:420px;max-width:95vw;
}
.modal h2{font-family:'Syne',sans-serif;font-size:1.2rem;font-weight:700;margin-bottom:0.5rem;}
.modal p{color:var(--muted);font-size:0.8rem;margin-bottom:1.5rem;line-height:1.6;}
.file-drop{
  border:2px dashed var(--border);border-radius:8px;
  padding:2rem;text-align:center;cursor:pointer;
  transition:border-color 0.15s;margin-bottom:1.2rem;
  color:var(--muted);font-size:0.82rem;
}
.file-drop:hover,.file-drop.over{border-color:var(--accent);color:var(--accent);}
.modal-actions{display:flex;gap:0.8rem;justify-content:flex-end;}

/* LOADING */
.loading{text-align:center;padding:3rem;color:var(--muted);font-size:0.85rem;}
.dot-pulse::after{content:'...';animation:dots 1.5s steps(4,end) infinite;}
@keyframes dots{0%,20%{content:'.';}40%{content:'..';}60%,100%{content:'...';}}

/* TOAST */
#toast{
  position:fixed;bottom:2rem;right:2rem;
  background:var(--panel);border:1px solid var(--border);
  padding:0.8rem 1.2rem;border-radius:8px;font-size:0.82rem;
  opacity:0;transition:opacity 0.3s;z-index:999;pointer-events:none;
}
#toast.show{opacity:1;}

.wl-name-badge{
  display:inline-block;background:rgba(6,182,212,0.1);
  color:var(--accent2);border:1px solid rgba(6,182,212,0.2);
  border-radius:4px;padding:0.1rem 0.5rem;
  font-size:0.72rem;margin-left:0.5rem;vertical-align:middle;
}
.add-row{display:flex;gap:0.8rem;align-items:center;margin-bottom:1.2rem;flex-wrap:wrap;}
</style>
</head>
<body ondragover="event.preventDefault()" ondrop="event.preventDefault()">

<nav>
  <div class="logo">SOS<span> TRACKER</span></div>
  <button class="active" onclick="showPage('home',this)" id="navHome">HOME</button>
  <button onclick="showPage('watchlists',this)" id="navWL">WATCHLISTS</button>
  <button onclick="showPage('charts',this)" id="navCharts">CHARTS</button>
  <button onclick="showPage('ath',this)" id="navATH">ATH TRACKER</button>
  <button onclick="showPage('drawdown',this)" id="navDrawdown">DRAWDOWN</button>
  <button class="btn success" style="padding:0.35rem 0.8rem;font-size:0.75rem;margin-left:auto"
          onclick="openImport()">⬆ Import CSV</button>
</nav>

<!-- HOME -->
<div id="home" class="page active">
  <h1>SOS Tracker</h1>
  <p class="subtitle">Your investing dashboard — watchlists, charts, and market data</p>

  <div style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem;">
    <div class="card" style="flex:1;min-width:180px;cursor:pointer;border-color:rgba(59,130,246,0.25);"
         onclick="showPage('watchlists',document.getElementById('navWL'))">
      <div style="font-size:1.5rem;margin-bottom:0.5rem;">📋</div>
      <div style="font-family:'Syne',sans-serif;font-weight:700;">Watchlists</div>
      <div style="color:var(--muted);font-size:0.78rem;margin-top:0.3rem;" id="homeWlCount">—</div>
    </div>
    <div class="card" style="flex:1;min-width:180px;cursor:pointer;border-color:rgba(59,130,246,0.25);"
         onclick="showPage('charts',document.getElementById('navCharts'))">
      <div style="font-size:1.5rem;margin-bottom:0.5rem;">📈</div>
      <div style="font-family:'Syne',sans-serif;font-weight:700;">Stock Charts</div>
      <div style="color:var(--muted);font-size:0.78rem;margin-top:0.3rem;">Price history &amp; comparison</div>
    </div>
    <div class="card" style="flex:1;min-width:180px;cursor:pointer;border-color:rgba(59,130,246,0.25);"
         onclick="showPage('ath',document.getElementById('navATH'))">
      <div style="font-size:1.5rem;margin-bottom:0.5rem;">🏆</div>
      <div style="font-family:'Syne',sans-serif;font-weight:700;">ATH Tracker</div>
      <div style="color:var(--muted);font-size:0.78rem;margin-top:0.3rem;">Distance from all-time highs</div>
    </div>
    <div class="card" style="flex:1;min-width:180px;cursor:pointer;border-color:rgba(59,130,246,0.25);"
         onclick="showPage('drawdown',document.getElementById('navDrawdown'))">
      <div style="font-size:1.5rem;margin-bottom:0.5rem;">📉</div>
      <div style="font-family:'Syne',sans-serif;font-weight:700;">Drawdown</div>
      <div style="color:var(--muted);font-size:0.78rem;margin-top:0.3rem;">Rolling drawdown from peak</div>
    </div>
  </div>
</div>

<!-- WATCHLISTS PAGE -->
<div id="watchlists" class="page">
  <h1>Watchlists</h1>
  <p class="subtitle">Select a portfolio from the dropdown to view live prices</p>

  <div class="toolbar" style="margin-bottom:1.5rem;">
    <select id="wlSelector" onchange="onWLSelect()" style="min-width:220px;font-size:0.9rem;">
      <option value="">— Select a watchlist —</option>
    </select>
    <button class="btn secondary" onclick="refreshWatchlist()">⟳ Refresh Prices</button>
    <button class="btn success" onclick="openImport()">⬆ Import CSV</button>
    <button class="btn danger" onclick="deleteWatchlist()" id="deleteWLBtn" style="display:none;">
      🗑 Delete
    </button>
    <span class="last-updated" id="wlUpdated">—</span>
  </div>

  <div id="wlAddRow" style="display:none;" class="add-row">
    <input type="text" id="newTicker" placeholder="Add ticker e.g. TSLA" maxlength="12"
           onkeydown="if(event.key==='Enter') addTicker()">
    <button class="btn" onclick="addTicker()">+ Add</button>
  </div>

  <div class="card" id="wlTableCard">
    <div id="wlEmpty" style="color:var(--muted);font-size:0.85rem;padding:1rem 0;">
      Select a watchlist above or import a CSV file to get started.
    </div>
    <div class="tbl-wrap" id="wlTableWrap" style="display:none;">
      <table>
        <thead><tr>
          <th>TICKER</th><th>NAME</th><th>PRICE</th>
          <th>CHANGE</th><th>CHG %</th>
          <th>52W HIGH</th><th>% FROM 52W H</th>
          <th>52W LOW</th><th>% FROM 52W L</th>
          <th>MKT CAP</th><th>P/E</th><th>DIV YIELD</th>
          <th></th>
        </tr></thead>
        <tbody id="wlBody">
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- CHARTS -->
<div id="charts" class="page">
  <h1>Stock Charts</h1>
  <p class="subtitle">5-year price history with optional index comparison</p>
  <div class="chart-controls">
    <select id="chartTicker" onchange="loadChart()"></select>
    <select id="chartPeriod" onchange="onPeriodChange()">
      <option value="1mo">1 Month</option>
      <option value="3mo">3 Months</option>
      <option value="6mo">6 Months</option>
      <option value="1y">1 Year</option>
      <option value="2y">2 Years</option>
      <option value="5y" selected>5 Years</option>
      <option value="10y">10 Years</option>
      <option value="20y">20 Years</option>
      <option value="max">Max</option>
      <option value="custom">Custom dates...</option>
    </select>
    <select id="chartIndex" onchange="loadChart()">
      <option value="">— No comparison —</option>
      <option value="^GSPC">vs S&amp;P 500</option>
      <option value="^IXIC">vs Nasdaq</option>
      <option value="^FTSE">vs FTSE 100</option>
      <option value="^DJI">vs Dow Jones</option>
      <option value="^RUT">vs Russell 2000</option>
    </select>
    <button class="btn secondary" onclick="loadChart()">⟳ Reload</button>
  </div>
  <div id="customDateRow" style="display:none;gap:0.8rem;align-items:center;margin-bottom:1.5rem;flex-wrap:wrap;" class="chart-controls">
    <label style="color:var(--muted);font-size:0.8rem;">From</label>
    <input type="date" id="chartDateFrom" style="width:160px;background:var(--panel);border:1px solid var(--border);
           color:var(--text);font-family:DM Mono,monospace;font-size:0.82rem;padding:0.6rem 0.8rem;
           border-radius:6px;outline:none;" onchange="loadChart()">
    <label style="color:var(--muted);font-size:0.8rem;">To</label>
    <input type="date" id="chartDateTo" style="width:160px;background:var(--panel);border:1px solid var(--border);
           color:var(--text);font-family:DM Mono,monospace;font-size:0.82rem;padding:0.6rem 0.8rem;
           border-radius:6px;outline:none;" onchange="loadChart()">
    <button class="btn secondary" onclick="loadChart()">Apply</button>
  </div>
  <div class="card" style="padding:1rem;">
    <div id="chartDiv"><div class="loading"><span class="dot-pulse">Loading chart</span></div></div>
  </div>
  <div class="card" style="padding:1rem;display:none;" id="relCard">
    <div style="color:var(--muted);font-size:0.7rem;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:1rem;">
      Relative Performance vs Index
    </div>
    <div id="relChartDiv"></div>
  </div>
</div>

<!-- ATH TRACKER -->
<div id="ath" class="page">
  <h1>ATH Tracker</h1>
  <p class="subtitle">Distance from all-time highs</p>

  <div class="toolbar">
    <button class="btn secondary" onclick="refreshATH()">⟳ Refresh All</button>
    <span class="last-updated" id="athUpdated">—</span>
  </div>

  <div class="ath-grid" id="athGrid">
    <div class="loading"><span class="dot-pulse">Loading</span></div>
  </div>

  <!-- Custom trackers -->
  <div style="margin-top:2rem;">
    <div style="font-family:'Syne',sans-serif;font-weight:700;font-size:1rem;
                margin-bottom:0.3rem;">Custom Trackers</div>
    <p style="color:var(--muted);font-size:0.78rem;margin-bottom:1.2rem;">
      Add any index, commodity or ticker to track its all-time high.
      Examples: ^FTSE, GC=F (Gold), CL=F (Oil), BTC-USD, ^N225 (Nikkei)
    </p>
    <div class="add-row" style="margin-bottom:1.5rem;">
      <input type="text" id="customATHTicker" placeholder="e.g. ^FTSE" style="width:140px"
             onkeydown="if(event.key==='Enter') addCustomATH()">
      <input type="text" id="customATHLabel" placeholder="Label e.g. FTSE 100" style="width:160px"
             onkeydown="if(event.key==='Enter') addCustomATH()">
      <button class="btn" onclick="addCustomATH()">+ Add</button>
    </div>
    <div class="ath-grid" id="customAthGrid"></div>
  </div>
</div>

<!-- DRAWDOWN -->
<div id="drawdown" class="page">
  <h1>Drawdown Chart</h1>
  <p class="subtitle">Rolling drawdown from peak — shows every pullback over time</p>

  <div class="chart-controls">
    <input type="text" id="ddTicker" placeholder="e.g. AAPL or ^GSPC"
           style="width:180px" onkeydown="if(event.key==='Enter') loadDrawdown()">
    <select id="ddPeriod" onchange="loadDrawdown()">
      <option value="1y">1 Year</option>
      <option value="2y">2 Years</option>
      <option value="5y" selected>5 Years</option>
      <option value="10y">10 Years</option>
      <option value="20y">20 Years</option>
      <option value="max">Max</option>
    </select>
    <button class="btn" onclick="loadDrawdown()">Load</button>
    <button class="btn secondary" onclick="loadDrawdownPreset('^GSPC')">S&amp;P 500</button>
    <button class="btn secondary" onclick="loadDrawdownPreset('^IXIC')">Nasdaq</button>
    <button class="btn secondary" onclick="loadDrawdownPreset('^FTSE')">FTSE 100</button>
  </div>

  <div class="card" style="padding:1rem;">
    <div id="ddChartDiv" style="width:100%;height:420px;">
      <div class="loading">Enter a ticker or index above and click Load</div>
    </div>
  </div>

  <div class="card" id="ddStatsCard" style="display:none;">
    <div style="font-family:'Syne',sans-serif;font-weight:700;font-size:0.95rem;margin-bottom:1rem;">
      Drawdown Statistics
    </div>
    <div id="ddStats" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1.2rem;"></div>
  </div>
</div>

<!-- IMPORT MODAL -->
<div class="modal-bg" id="importModal">
  <div class="modal">
    <h2>Import Watchlist</h2>
    <p>
      Upload a Yahoo Finance CSV export.<br>
      Row 1: column headers &nbsp;|&nbsp; Row 2: watchlist name &nbsp;|&nbsp; Row 3+: tickers
    </p>
    <div class="file-drop" id="fileDrop"
         onclick="document.getElementById('csvFile').click()"
         ondragover="event.preventDefault();this.classList.add('over')"
         ondragleave="this.classList.remove('over')"
         ondrop="handleDrop(event)">
      Click to choose CSV file or drag &amp; drop here
    </div>
    <input type="file" id="csvFile" accept=".csv" style="display:none"
           onchange="handleFile(this.files[0])">
    <div id="importPreview" style="color:var(--muted);font-size:0.8rem;margin-bottom:1rem;"></div>
    <div class="modal-actions">
      <button class="btn secondary" onclick="closeImport()">Cancel</button>
      <button class="btn" id="importBtn" onclick="doImport()" disabled>Import</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
// ── State ─────────────────────────────────────────────────────────────────────
let watchlists   = {};
let currentWL    = null;
let pendingImport= null;

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, ms=2800) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), ms);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(n, d=2) {
  if (n===null||n===undefined||isNaN(n)) return '—';
  return Number(n).toLocaleString('en-GB',{minimumFractionDigits:d,maximumFractionDigits:d});
}
function fmtPct(n) {
  if (n===null||n===undefined||isNaN(n)) return '—';
  return (n>=0?'+':'')+fmt(n)+'%';
}
function fmtCap(n) {
  if (!n) return '—';
  if (n>=1e12) return (n/1e12).toFixed(2)+'T';
  if (n>=1e9)  return (n/1e9).toFixed(1)+'B';
  if (n>=1e6)  return (n/1e6).toFixed(1)+'M';
  return n;
}
function cls(n) {
  if (n===null||n===undefined||isNaN(n)) return 'neu';
  return n>=0?'up':'down';
}

// ── Nav ───────────────────────────────────────────────────────────────────────
function showPage(id, btn) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  if (btn) btn.classList.add('active');
  if (id==='charts') initChartPage();
  if (id==='ath')    refreshATH();
}

function buildWLSelector() {
  const sel   = document.getElementById('wlSelector');
  const names = Object.keys(watchlists);
  sel.innerHTML = '<option value="">— Select a watchlist —</option>' +
    names.map(n => `<option value="${n}"${currentWL===n?' selected':''}>${n}</option>`).join('');
}

function onWLSelect() {
  const name = document.getElementById('wlSelector').value;
  currentWL  = name || null;
  const hasWL = !!name;
  document.getElementById('deleteWLBtn').style.display  = hasWL ? 'inline-flex' : 'none';
  document.getElementById('wlAddRow').style.display     = hasWL ? 'flex'        : 'none';
  document.getElementById('wlEmpty').style.display      = hasWL ? 'none'        : 'block';
  document.getElementById('wlTableWrap').style.display  = hasWL ? 'block'       : 'none';
  if (hasWL) refreshWatchlist();
}

async function loadWatchlists() {
  const res  = await fetch('/api/watchlists');
  watchlists = await res.json();
  buildWLSelector();
  const cnt = document.getElementById('homeWlCount');
  const n   = Object.keys(watchlists).length;
  if (cnt) cnt.textContent = n + ' watchlist' + (n!==1?'s':'') + ' — click to view';
}


// ── WATCHLIST TABLE ───────────────────────────────────────────────────────────
async function refreshWatchlist() {
  if (!currentWL) return;
  document.getElementById('wlBody').innerHTML =
    '<tr><td colspan="13" class="loading"><span class="dot-pulse">Fetching prices</span></td></tr>';
  try {
    const res  = await fetch('/api/watchlist?name='+encodeURIComponent(currentWL));
    const data = await res.json();
    renderWatchlist(data);
    document.getElementById('wlUpdated').textContent =
      'Updated: '+new Date().toLocaleTimeString('en-GB');
  } catch(e) {
    document.getElementById('wlBody').innerHTML =
      '<tr><td colspan="13" class="loading">Error fetching data</td></tr>';
  }
}

function renderWatchlist(data) {
  const tbody = document.getElementById('wlBody');
  if (!data.length) {
    tbody.innerHTML='<tr><td colspan="13" class="loading">No tickers — add one above</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(d => `
    <tr>
      <td class="ticker-cell" onclick="goChart('${d.ticker}')">${d.ticker}</td>
      <td style="color:var(--muted);min-width:200px;max-width:280px;white-space:normal;line-height:1.4;">${d.name||'—'}</td>
      <td>${fmt(d.price)}</td>
      <td class="${cls(d.change)}">${d.change!=null?(d.change>=0?'+':'')+fmt(d.change):'—'}</td>
      <td><span class="badge ${cls(d.change_pct)}">${fmtPct(d.change_pct)}</span></td>
      <td>${fmt(d.high_52w)}</td>
      <td class="${cls(d.pct_52h)}">${fmtPct(d.pct_52h)}</td>
      <td>${fmt(d.low_52w)}</td>
      <td class="${cls(d.pct_52l)}">${fmtPct(d.pct_52l)}</td>
      <td style="color:var(--muted)">${fmtCap(d.market_cap)}</td>
      <td style="color:var(--muted)">${d.pe?fmt(d.pe,1):'—'}</td>
      <td style="color:var(--muted)">${d.div_yield?(d.div_yield>0.2?fmt(d.div_yield,2):fmt(d.div_yield*100,2))+'%':'—'}</td>
      <td><button class="btn danger" style="padding:0.3rem 0.6rem;font-size:0.7rem"
          onclick="removeTicker('${d.ticker}')">✕</button></td>
    </tr>`).join('');
}

async function addTicker() {
  const inp = document.getElementById('newTicker');
  const t   = inp.value.trim().toUpperCase();
  if (!t || !currentWL) return;
  inp.value = '';
  toast('Adding '+t+'...');
  const res  = await fetch('/api/tickers/add', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({watchlist: currentWL, ticker: t})
  });
  const data = await res.json();
  if (data.ok) { toast(t+' added!'); loadWatchlists(); refreshWatchlist(); }
  else         { toast('Error: '+(data.error||'Unknown')); }
}

async function removeTicker(t) {
  await fetch('/api/tickers/remove', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({watchlist: currentWL, ticker: t})
  });
  toast(t+' removed'); loadWatchlists(); refreshWatchlist();
}

async function deleteWatchlist() {
  if (!currentWL) return;
  if (!confirm('Delete watchlist "'+currentWL+'"?')) return;
  await fetch('/api/watchlist/delete', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name: currentWL})
  });
  toast(currentWL+' deleted');
  currentWL = null;
  await loadWatchlists();
  document.getElementById('wlSelector').value = '';
  onWLSelect();
}

// ── CSV IMPORT ────────────────────────────────────────────────────────────────
function openImport()  { document.getElementById('importModal').classList.add('open'); }
function closeImport() {
  document.getElementById('importModal').classList.remove('open');
  document.getElementById('importPreview').textContent = '';
  document.getElementById('importBtn').disabled = true;
  document.getElementById('fileDrop').textContent = 'Click to choose CSV file or drag & drop here';
  pendingImport = null;
}

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('fileDrop').classList.remove('over');
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
}

function handleFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    const text  = e.target.result;
    const lines = text.split(/\r?\n/).filter(l=>l.trim());
    if (lines.length < 2) { toast('CSV too short'); return; }

    // Row 2 (index 1) = watchlist name
    const wlName = lines[1].split(',')[0].trim() || file.name.replace('.csv','');

    // Row 3+ = tickers (first column)
    const tickers = lines.slice(2)
      .map(l => l.split(',')[0].trim().toUpperCase())
      .filter(t => t && !t.startsWith('#'));

    pendingImport = { name: wlName, tickers };

    document.getElementById('fileDrop').textContent = '✓ '+file.name;
    document.getElementById('importPreview').innerHTML =
      `<div style="margin-top:0.5rem;">
        <span style="color:var(--accent2);">Name:</span> ${wlName}<br>
        <span style="color:var(--accent2);">Tickers (${tickers.length}):</span> ${tickers.slice(0,10).join(', ')}${tickers.length>10?' …':''}
       </div>`;
    document.getElementById('importBtn').disabled = false;
  };
  reader.readAsText(file);
}

async function doImport() {
  if (!pendingImport) return;
  document.getElementById('importBtn').disabled = true;
  const res  = await fetch('/api/watchlist/import', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(pendingImport)
  });
  const data = await res.json();
  if (data.ok) {
    const importedName = pendingImport.name;
    const importedCount = pendingImport.tickers.length;
    closeImport();
    await loadWatchlists();  // updates watchlists dict
    currentWL = importedName;
    // Switch to watchlists page
    document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
    document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
    document.getElementById('watchlists').classList.add('active');
    document.getElementById('navWL').classList.add('active');
    // Rebuild selector and select the new watchlist
    buildWLSelector();
    document.getElementById('wlSelector').value = importedName;
    onWLSelect();
    toast('Imported "'+importedName+'" — '+importedCount+' tickers');
  } else {
    toast('Import failed: '+(data.error||'Unknown'));
    document.getElementById('importBtn').disabled = false;
  }
}

// ── CHARTS ────────────────────────────────────────────────────────────────────
function goChart(ticker) {
  showPage('charts', document.getElementById('navCharts'));
  document.getElementById('chartTicker').value = ticker;
  loadChart();
}

async function initChartPage() {
  const res  = await fetch('/api/all-tickers');
  const data = await res.json();
  const sel  = document.getElementById('chartTicker');
  const cur  = sel.value;
  sel.innerHTML = data.map(t=>`<option value="${t}">${t}</option>`).join('');
  if (cur) sel.value = cur;
  loadChart();
}

function onPeriodChange() {
  const period = document.getElementById('chartPeriod').value;
  const row    = document.getElementById('customDateRow');
  if (period === 'custom') {
    row.style.display = 'flex';
    // Default: last 5 years
    const today = new Date();
    const fiveAgo = new Date(today);
    fiveAgo.setFullYear(fiveAgo.getFullYear() - 5);
    document.getElementById('chartDateTo').value   = today.toISOString().split('T')[0];
    document.getElementById('chartDateFrom').value = fiveAgo.toISOString().split('T')[0];
  } else {
    row.style.display = 'none';
    loadChart();
  }
}

async function loadChart() {
  const ticker = document.getElementById('chartTicker').value;
  const period = document.getElementById('chartPeriod').value;
  const index  = document.getElementById('chartIndex').value;
  if (!ticker) return;

  document.getElementById('chartDiv').innerHTML =
    '<div class="loading"><span class="dot-pulse">Loading chart</span></div>';
  document.getElementById('relCard').style.display = 'none';

  const params = new URLSearchParams({ticker});
  if (period === 'custom') {
    const from = document.getElementById('chartDateFrom').value;
    const to   = document.getElementById('chartDateTo').value;
    if (!from || !to) return;
    params.append('date_from', from);
    params.append('date_to',   to);
    params.append('period',    'max');
  } else {
    params.append('period', period);
  }
  if (index) params.append('index', index);

  try {
    const res  = await fetch('/api/chart?'+params);
    const data = await res.json();
    if (data.error) {
      document.getElementById('chartDiv').innerHTML='<div class="loading">'+data.error+'</div>';
      return;
    }
    const bg    = '#080d14', grid = '#1a2535';
    const color = data.change_pct >= 0 ? '#22c55e' : '#ef4444';

    // Dynamic Y-axis: fit to data with 5% padding, never start at zero
    function yRange(values, padPct=0.05) {
      const mn  = Math.min(...values);
      const mx  = Math.max(...values);
      const pad = (mx - mn) * padPct;
      return [mn - pad, mx + pad];
    }

    const priceRange = yRange(data.prices);

    const layout = {
      paper_bgcolor:bg, plot_bgcolor:bg,
      font:{family:'DM Mono, monospace', color:'#94a3b8', size:11},
      margin:{l:60,r:30,t:50,b:50},
      title:{
        text:`<b>${ticker}</b>  ${Number(data.prices.slice(-1)[0]).toLocaleString('en-GB',{minimumFractionDigits:2,maximumFractionDigits:2})}` +
             (data.name ? `<span style="color:#64748b">  |  </span><span style="color:#94a3b8;font-weight:400">${data.name}</span>` : ''),
        font:{size:15,color:'#e2e8f0'}, x:0, xanchor:'left'
      },
      xaxis:{gridcolor:grid,linecolor:grid,tickcolor:grid,zeroline:false},
      yaxis:{gridcolor:grid,linecolor:grid,tickcolor:grid,zeroline:false,
             tickformat:',.2f', range:priceRange},
      hovermode:'x unified', showlegend:false,
    };
    Plotly.newPlot('chartDiv',[{
      x:data.dates, y:data.prices, type:'scatter', mode:'lines',
      line:{color, width:2},
      fill:'tonexty',
      fillcolor:color+'18',
      hovertemplate:'%{y:,.2f}<extra></extra>',
    }], layout, {responsive:true, displayModeBar:false});

    if (index && data.rel_dates) {
      document.getElementById('relCard').style.display='block';
      const rc       = data.rel_final>=1?'#22c55e':'#ef4444';
      const relRange = yRange(data.rel_values);
      Plotly.newPlot('relChartDiv',[{
        x:data.rel_dates, y:data.rel_values, type:'scatter', mode:'lines',
        line:{color:rc, width:2},
        fill:'tonexty', fillcolor:rc+'18',
        hovertemplate:'%{y:.3f}x<extra></extra>',
      }],{
        ...layout,
        title:{text:`Relative to ${index}`,font:{size:13,color:'#e2e8f0'},x:0,xanchor:'left'},
        margin:{l:60,r:30,t:40,b:50},
        shapes:[{type:'line',x0:data.rel_dates[0],x1:data.rel_dates.slice(-1)[0],
                 y0:1,y1:1,line:{color:'#334455',width:1,dash:'dot'}}],
        yaxis:{...layout.yaxis, tickformat:'.3f', ticksuffix:'x', range:relRange},
      },{responsive:true,displayModeBar:false});
    }
  } catch(e) {
    document.getElementById('chartDiv').innerHTML='<div class="loading">Error loading chart</div>';
  }
}

// ── ATH ───────────────────────────────────────────────────────────────────────
function athCardHTML(d, showDelete=false) {
  const dd  = d.price&&d.ath ? (d.price-d.ath)/d.ath*100 : null;
  const p52 = d.price&&d.high_52w ? (d.price-d.high_52w)/d.high_52w*100 : null;
  const pct = d.price&&d.ath ? Math.max(0,d.price/d.ath*100) : 0;
  const delBtn = showDelete
    ? `<button class="btn danger" style="padding:0.3rem 0.6rem;font-size:0.7rem;margin-top:1rem;"
         onclick="removeCustomATH('${d.symbol}')">✕ Remove</button>`
    : '';
  return `<div class="ath-card">
    <div class="ath-name">${d.name}</div>
    <div style="color:var(--muted);font-size:0.72rem;margin-bottom:0.5rem">${d.symbol}</div>
    <div class="ath-price ${dd!=null&&dd>=0?'up':'down'}">${d.price?Number(d.price).toLocaleString('en-GB',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div>
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    <div class="ath-row">
      <div><div class="ath-stat-label">ALL-TIME HIGH</div>
        <div class="ath-stat-val">${d.ath?Number(d.ath).toLocaleString('en-GB',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div>
        <div style="color:var(--muted);font-size:0.7rem">${d.ath_date||''}</div></div>
      <div><div class="ath-stat-label">FROM ATH</div>
        <div class="ath-stat-val ${dd!=null&&dd>=0?'up':'down'}">${dd!=null?(dd>=0?'+':'')+dd.toFixed(2)+'%':'—'}</div></div>
      <div><div class="ath-stat-label">52W HIGH</div>
        <div class="ath-stat-val">${d.high_52w?Number(d.high_52w).toLocaleString('en-GB',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}</div></div>
      <div><div class="ath-stat-label">FROM 52W HIGH</div>
        <div class="ath-stat-val ${p52!=null&&p52>=0?'up':'down'}">${p52!=null?(p52>=0?'+':'')+p52.toFixed(2)+'%':'—'}</div></div>
    </div>
    ${delBtn}
  </div>`;
}

async function refreshATH() {
  document.getElementById('athGrid').innerHTML=
    '<div class="loading"><span class="dot-pulse">Loading</span></div>';
  try {
    const res  = await fetch('/api/ath');
    const data = await res.json();
    document.getElementById('athGrid').innerHTML = data.map(d=>athCardHTML(d,false)).join('');
    document.getElementById('athUpdated').textContent='Updated: '+new Date().toLocaleTimeString('en-GB');
  } catch(e) {
    document.getElementById('athGrid').innerHTML='<div class="loading">Error</div>';
  }
  loadCustomATH();
}

async function loadCustomATH() {
  const grid = document.getElementById('customAthGrid');
  if (!grid) return;
  grid.innerHTML = '<div class="loading"><span class="dot-pulse">Loading</span></div>';
  try {
    const res  = await fetch('/api/ath/custom');
    const data = await res.json();
    if (!data.length) {
      grid.innerHTML = '<div style="color:var(--muted);font-size:0.85rem;padding:0.5rem 0;">No custom trackers yet — add one above.</div>';
      return;
    }
    grid.innerHTML = data.map(d=>athCardHTML(d,true)).join('');
  } catch(e) {
    grid.innerHTML = '<div class="loading">Error loading custom trackers</div>';
  }
}

async function addCustomATH() {
  const sym   = document.getElementById('customATHTicker').value.trim().toUpperCase();
  const label = document.getElementById('customATHLabel').value.trim();
  if (!sym) { toast('Enter a ticker symbol'); return; }
  toast('Adding '+sym+'...');
  const res  = await fetch('/api/ath/custom/add', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({symbol: sym, label: label || sym})
  });
  const data = await res.json();
  if (data.ok) {
    document.getElementById('customATHTicker').value = '';
    document.getElementById('customATHLabel').value  = '';
    toast(sym+' added!');
    loadCustomATH();
  } else {
    toast('Error: '+(data.error||'Unknown'));
  }
}

async function removeCustomATH(sym) {
  await fetch('/api/ath/custom/remove', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({symbol: sym})
  });
  toast(sym+' removed');
  loadCustomATH();
}

// ── DRAWDOWN ──────────────────────────────────────────────────────────────────
function loadDrawdownPreset(ticker) {
  document.getElementById('ddTicker').value = ticker;
  loadDrawdown();
}

async function loadDrawdown() {
  const ticker = document.getElementById('ddTicker').value.trim().toUpperCase();
  const period = document.getElementById('ddPeriod').value;
  if (!ticker) { toast('Enter a ticker first'); return; }

  document.getElementById('ddChartDiv').innerHTML =
    '<div class="loading"><span class="dot-pulse">Loading</span></div>';
  document.getElementById('ddStatsCard').style.display = 'none';

  try {
    const res  = await fetch('/api/drawdown?ticker='+encodeURIComponent(ticker)+'&period='+period);
    const data = await res.json();
    if (data.error) {
      document.getElementById('ddChartDiv').innerHTML =
        '<div class="loading">'+data.error+'</div>'; return;
    }

    const bg     = '#080d14', grid = '#1a2535';

    // Dynamic Y-axis — must be defined before layout
    const ddMin   = Math.min(...data.drawdowns);
    const ddRange = [ddMin * 1.08, Math.max(...data.drawdowns) + 0.005];

    const layout = {
      paper_bgcolor:bg, plot_bgcolor:bg,
      font:{family:'DM Mono, monospace', color:'#94a3b8', size:11},
      margin:{l:60,r:30,t:50,b:50},
      title:{
        text:`<b>${ticker}</b>  Drawdown from Peak`,
        font:{size:15, color:'#e2e8f0'}, x:0, xanchor:'left'
      },
      xaxis:{gridcolor:grid, linecolor:grid, tickcolor:grid, zeroline:false},
      yaxis:{
        gridcolor:grid, linecolor:grid, tickcolor:grid,
        zeroline:true, zerolinecolor:'#334455', zerolinewidth:1,
        tickformat:'.1%', range:ddRange
      },
      hovermode:'x unified', showlegend:false,
      shapes:[{
        type:'line', x0:data.dates[0], x1:data.dates.slice(-1)[0],
        y0:0, y1:0, line:{color:'#334455', width:1, dash:'dot'}
      }]
    };

    const trace = {
      x: data.dates,
      y: data.drawdowns,
      type: 'scatter', mode: 'lines',
      line: {color:'#ef4444', width:1.5},
      fill: 'tozeroy',
      fillcolor: 'rgba(239,68,68,0.15)',
      hovertemplate: '%{y:.2%}<extra></extra>',
      name: 'Drawdown'
    };

    Plotly.newPlot('ddChartDiv', [trace], layout, {responsive:true, displayModeBar:false});

    // Stats
    document.getElementById('ddStatsCard').style.display = 'block';
    const stats = data.stats;
    document.getElementById('ddStats').innerHTML = [
      ['Max Drawdown',     (stats.max_dd*100).toFixed(2)+'%',     'down'],
      ['Max DD Date',      stats.max_dd_date,                      'neu'],
      ['Current Drawdown', (stats.current_dd*100).toFixed(2)+'%', stats.current_dd < -0.01 ? 'down' : 'up'],
      ['Days > -10%',      stats.days_over_10+'d',                 'neu'],
      ['Days > -20%',      stats.days_over_20+'d',                 'neu'],
      ['Avg Drawdown',     (stats.avg_dd*100).toFixed(2)+'%',      'down'],
    ].map(([label, val, cls]) => `
      <div>
        <div style="color:var(--muted);font-size:0.72rem;margin-bottom:0.3rem;">${label}</div>
        <div style="font-size:1rem;font-weight:500;" class="${cls}">${val}</div>
      </div>`).join('');

  } catch(e) {
    document.getElementById('ddChartDiv').innerHTML =
      '<div class="loading">Error loading drawdown</div>';
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadWatchlists();
</script>
</body>
</html>
"""

# ── API Routes ────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/watchlists')
def get_watchlists():
    return jsonify(get_watchlists_dict())

@app.route('/api/watchlist')
def get_watchlist():
    name = request.args.get('name', '')
    conn = get_db()
    rows = conn.execute(
        "SELECT ticker FROM watchlist_tickers WHERE watchlist=? ORDER BY position",
        (name,)
    ).fetchall()
    conn.close()
    tickers = [r['ticker'] for r in rows]
    results = []
    for ticker in tickers:
        try:
            info  = ensure_ticker_info(ticker)
            price = get_live_price(ticker)
            prev  = info.get('prev_close')
            change     = (price - prev) if price and prev else None
            change_pct = (change / prev * 100) if change and prev else None
            high_52w   = info.get('high_52w')
            low_52w    = info.get('low_52w')
            pct_52h    = ((price - high_52w) / high_52w * 100) if price and high_52w else None
            pct_52l    = ((price - low_52w)  / low_52w  * 100) if price and low_52w  else None
            results.append({
                'ticker':     ticker,
                'name':       info.get('name', ticker),
                'price':      round(price, 2)      if price      else None,
                'change':     round(change, 2)     if change     else None,
                'change_pct': round(change_pct, 2) if change_pct else None,
                'high_52w':   round(high_52w, 2)   if high_52w   else None,
                'low_52w':    round(low_52w, 2)    if low_52w    else None,
                'pct_52h':    round(pct_52h, 2)    if pct_52h    else None,
                'pct_52l':    round(pct_52l, 2)    if pct_52l    else None,
                'market_cap': info.get('market_cap'),
                'pe':         info.get('pe'),
                'div_yield':  info.get('div_yield'),
            })
        except Exception as e:
            results.append({'ticker': ticker, 'name': ticker, 'error': str(e)})
    return jsonify(results)

@app.route('/api/watchlist/import', methods=['POST'])
def import_watchlist():
    data    = request.json
    name    = data.get('name', '').strip()
    tickers = [t.strip().upper() for t in data.get('tickers', []) if t.strip()]
    if not name:
        return jsonify({'ok': False, 'error': 'No watchlist name'})
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO watchlists VALUES (?)", (name,))
    conn.execute("DELETE FROM watchlist_tickers WHERE watchlist=?", (name,))
    for i, t in enumerate(tickers):
        conn.execute("INSERT OR IGNORE INTO watchlist_tickers VALUES (?,?,?)", (name, t, i))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/watchlist/delete', methods=['POST'])
def delete_watchlist():
    name = request.json.get('name', '')
    conn = get_db()
    conn.execute("DELETE FROM watchlist_tickers WHERE watchlist=?", (name,))
    conn.execute("DELETE FROM watchlists WHERE name=?", (name,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/tickers/add', methods=['POST'])
def add_ticker():
    data    = request.json
    wl_name = data.get('watchlist', '')
    ticker  = data.get('ticker', '').strip().upper()
    if not ticker:
        return jsonify({'ok': False, 'error': 'No ticker'})
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO watchlists VALUES (?)", (wl_name,))
    pos  = conn.execute(
        "SELECT COUNT(*) FROM watchlist_tickers WHERE watchlist=?", (wl_name,)
    ).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO watchlist_tickers VALUES (?,?,?)", (wl_name, ticker, pos))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/tickers/remove', methods=['POST'])
def remove_ticker():
    data    = request.json
    wl_name = data.get('watchlist', '')
    ticker  = data.get('ticker', '').strip().upper()
    conn    = get_db()
    conn.execute("DELETE FROM watchlist_tickers WHERE watchlist=? AND ticker=?", (wl_name, ticker))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/all-tickers')
def all_tickers():
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT ticker FROM watchlist_tickers ORDER BY ticker").fetchall()
    conn.close()
    return jsonify([r['ticker'] for r in rows])

@app.route('/api/chart')
def get_chart():
    ticker    = request.args.get('ticker', '').upper()
    period    = request.args.get('period', '5y')
    index     = request.args.get('index', '')
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')
    if not ticker:
        return jsonify({'error': 'No ticker'})
    try:
        ensure_price_history(ticker, 'max')
        rows = get_price_history_from_db(ticker, period)
        if not rows:
            return jsonify({'error': f'No data for {ticker}'})
        # Apply custom date filter if provided
        if date_from:
            rows = [r for r in rows if r[0] >= date_from]
        if date_to:
            rows = [r for r in rows if r[0] <= date_to]
        if not rows:
            return jsonify({'error': 'No data in selected date range'})
        dates  = [r[0] for r in rows]
        prices = [r[1] for r in rows]
        chg    = (prices[-1] - prices[0]) / prices[0] * 100
        # Get company name from DB cache
        conn = get_db()
        info_row = conn.execute("SELECT name FROM ticker_info WHERE ticker=?", (ticker,)).fetchone()
        conn.close()
        ticker_name = info_row['name'] if info_row else ticker
        result = {'dates': dates, 'prices': prices, 'change_pct': round(chg, 2), 'name': ticker_name}
        if index:
            ensure_price_history(index, 'max')
            irows = get_price_history_from_db(index, period)
            if date_from: irows = [r for r in irows if r[0] >= date_from]
            if date_to:   irows = [r for r in irows if r[0] <= date_to]
            if irows:
                s = pd.Series([r[1] for r in rows],  index=pd.to_datetime([r[0] for r in rows]))
                i = pd.Series([r[1] for r in irows], index=pd.to_datetime([r[0] for r in irows]))
                common = s.index.intersection(i.index)
                s, i   = s.loc[common], i.loc[common]
                rel    = (s / s.iloc[0]) / (i / i.iloc[0])
                result['rel_dates']  = [str(d.date()) for d in rel.index]
                result['rel_values'] = [round(float(v), 6) for v in rel.values]
                result['rel_final']  = round(float(rel.iloc[-1]), 4)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/ath')
def get_ath():
    results = []
    for symbol, name in ATH_SYMBOLS.items():
        try:
            t    = yf.Ticker(symbol)
            info = t.info
            try:    price = t.fast_info.last_price
            except: price = info.get('regularMarketPrice') or info.get('currentPrice')
            hist = t.history(period='max')['High']
            if not hist.empty:
                ath_val      = float(hist.max())
                ath_idx      = hist.idxmax()
                ath_date_str = ath_idx.strftime('%b %d, %Y') if hasattr(ath_idx,'strftime') else str(ath_idx)
            else:
                ath_val, ath_date_str = info.get('fiftyTwoWeekHigh'), '—'
            results.append({
                'symbol':   symbol, 'name': name,
                'ath':      round(ath_val, 2) if ath_val else None,
                'ath_date': ath_date_str,
                'price':    round(float(price), 2) if price else None,
                'high_52w': round(float(info.get('fiftyTwoWeekHigh')), 2) if info.get('fiftyTwoWeekHigh') else None,
            })
        except Exception as e:
            results.append({'symbol': symbol, 'name': name,
                            'price': None, 'high_52w': None, 'ath': None, 'ath_date': '—'})
    return jsonify(results)

@app.route('/api/drawdown')
def get_drawdown():
    ticker = request.args.get('ticker','').upper()
    period = request.args.get('period','5y')
    if not ticker:
        return jsonify({'error': 'No ticker'})
    try:
        ensure_price_history(ticker, 'max')
        rows = get_price_history_from_db(ticker, period)
        if not rows:
            return jsonify({'error': f'No data for {ticker}'})

        dates  = [r[0] for r in rows]
        prices = [r[1] for r in rows]

        # Rolling drawdown from peak
        peak      = prices[0]
        drawdowns = []
        max_dd    = 0.0
        max_dd_idx= 0
        for i, p in enumerate(prices):
            if p > peak:
                peak = p
            dd = (p - peak) / peak if peak > 0 else 0
            drawdowns.append(round(dd, 6))
            if dd < max_dd:
                max_dd     = dd
                max_dd_idx = i

        current_dd   = drawdowns[-1]
        days_over_10 = sum(1 for d in drawdowns if d < -0.10)
        days_over_20 = sum(1 for d in drawdowns if d < -0.20)
        avg_dd       = sum(d for d in drawdowns if d < 0) / max(1, sum(1 for d in drawdowns if d < 0))

        return jsonify({
            'dates':     dates,
            'drawdowns': drawdowns,
            'stats': {
                'max_dd':      round(max_dd, 6),
                'max_dd_date': dates[max_dd_idx],
                'current_dd':  round(current_dd, 6),
                'days_over_10': days_over_10,
                'days_over_20': days_over_20,
                'avg_dd':      round(avg_dd, 6),
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/ath/custom')
def get_custom_ath():
    conn = get_db()
    rows = conn.execute("SELECT symbol, label FROM custom_ath ORDER BY label").fetchall()
    conn.close()
    results = []
    for row in rows:
        symbol = row['symbol']
        name   = row['label']
        try:
            t    = yf.Ticker(symbol)
            info = t.info
            try:    price = t.fast_info.last_price
            except: price = info.get('regularMarketPrice') or info.get('currentPrice')
            hist = t.history(period='max')['High']
            if not hist.empty:
                ath_val      = float(hist.max())
                ath_idx      = hist.idxmax()
                ath_date_str = ath_idx.strftime('%b %d, %Y') if hasattr(ath_idx,'strftime') else str(ath_idx)
            else:
                ath_val, ath_date_str = info.get('fiftyTwoWeekHigh'), '—'
            results.append({
                'symbol':   symbol, 'name': name,
                'ath':      round(ath_val, 2) if ath_val else None,
                'ath_date': ath_date_str,
                'price':    round(float(price), 2) if price else None,
                'high_52w': round(float(info.get('fiftyTwoWeekHigh')), 2) if info.get('fiftyTwoWeekHigh') else None,
            })
        except Exception as e:
            results.append({'symbol': symbol, 'name': name,
                            'price': None, 'high_52w': None, 'ath': None, 'ath_date': '—'})
    return jsonify(results)

@app.route('/api/ath/custom/add', methods=['POST'])
def add_custom_ath():
    data   = request.json
    symbol = data.get('symbol','').strip().upper()
    label  = data.get('label','').strip() or symbol
    if not symbol:
        return jsonify({'ok': False, 'error': 'No symbol'})
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO custom_ath VALUES (?,?)", (symbol, label))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/ath/custom/remove', methods=['POST'])
def remove_custom_ath():
    symbol = request.json.get('symbol','').strip().upper()
    conn   = get_db()
    conn.execute("DELETE FROM custom_ath WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Launch ────────────────────────────────────────────────────────────────────
def open_browser():
    time.sleep(1.2)
    webbrowser.open('http://127.0.0.1:5000')

if __name__ == '__main__':
    print("=" * 50)
    print("  SOS Tracker")
    print("=" * 50)
    print("\n  Starting at http://127.0.0.1:5000")
    print("  Press Ctrl+C to stop.\n")
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(debug=False, port=5000)
