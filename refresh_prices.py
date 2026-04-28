#!/usr/bin/env python3
"""
Watchlist — Per-Stock Chart Generator (with local Index data)
--------------------------------------------------------------
1. Reads tickers from the Watchlist tab
2. Reads indices from the Indices tab (you type them in)
3. Fetches 5-year price history for all stocks AND all indices from Yahoo
4. Stores index data locally in the Indices tab
5. Creates one chart tab per stock — absolute price always shown
6. If you type an index ticker in the yellow box on a stock tab,
   the relative chart is built using LOCAL index data (no re-fetch needed)

Usage:
    py build_stock_charts.py

Requirements:
    py -m pip install yfinance matplotlib openpyxl pandas
"""

import sys, io, datetime, subprocess

def ensure(pkg):
    try: __import__(pkg)
    except ImportError:
        print(f"  Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

for p in ["yfinance", "matplotlib", "pandas", "openpyxl"]:
    ensure(p)

import yfinance as yf
import pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.drawing.image import Image as XLImage

SPREADSHEET      = "Watchlist_Tracker.xlsx"
WATCHLIST_SHEET  = "Watchlist"
INDICES_SHEET    = "Indices"
TICKER_COL       = 2
DATA_START_ROW   = 7
PERIOD           = "5y"
INDEX_DATA_START = 30   # row in Indices tab where price history is stored

C_DARK   = "0D1B2A"
C_MID    = "1B4965"
C_LIGHT  = "CAE9FF"
C_WHITE  = "FFFFFF"
C_GREY   = "F0F4F8"
C_YELLOW = "FFFDE7"
BG       = "#0D1B2A"

DEFAULT_INDICES = ["^GSPC", "^IXIC", "^FTSE", "^DJI", "^RUT"]

def fill(c):
    return PatternFill("solid", fgColor=c)

def xfont(bold=False, color=C_DARK, size=10, italic=False):
    return Font(name="Arial", bold=bold, color=color, size=size, italic=italic)

def xalign(h="left", v="center"):
    return Alignment(horizontal=h, vertical=v, wrap_text=False)

def xborder():
    s = Side(style="thin", color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)


# ── Indices tab setup ─────────────────────────────────────────────────────────

def ensure_indices_sheet(wb):
    if INDICES_SHEET in wb.sheetnames:
        return
    ws = wb.create_sheet(INDICES_SHEET, 1)
    ws.sheet_view.showGridLines = False

    for col, w in [("A",2),("B",18),("C",28),("D",2)]:
        ws.column_dimensions[col].width = w
    ws.row_dimensions[2].height = 30
    ws.row_dimensions[3].height = 20
    ws.row_dimensions[4].height = 20
    ws.row_dimensions[6].height = 24

    ws.merge_cells("B2:C2")
    ws["B2"] = "Indices"
    ws["B2"].font = xfont(bold=True, color=C_WHITE, size=16)
    ws["B2"].fill = fill(C_DARK)
    ws["B2"].alignment = xalign("center")

    ws.merge_cells("B3:C3")
    ws["B3"] = "Add index tickers below. Run build_stock_charts.py to fetch data."
    ws["B3"].font = xfont(italic=True, color=C_WHITE, size=10)
    ws["B3"].fill = fill(C_MID)
    ws["B3"].alignment = xalign("center")

    ws.merge_cells("B4:C4")
    ws["B4"] = "Last updated: —"
    ws["B4"].font = xfont(italic=True, color="888888", size=9)
    ws["B4"].fill = fill(C_GREY)
    ws["B4"].alignment = xalign("right")

    for col, label in [("B","Index Ticker"), ("C","Full Name")]:
        ws[f"{col}6"] = label
        ws[f"{col}6"].font = xfont(bold=True, color=C_WHITE, size=10)
        ws[f"{col}6"].fill = fill(C_MID)
        ws[f"{col}6"].alignment = xalign("center")
        ws[f"{col}6"].border = xborder()

    for i, idx in enumerate(DEFAULT_INDICES):
        r = 7 + i
        ws.row_dimensions[r].height = 20
        bg = C_GREY if i % 2 == 0 else C_WHITE
        ws[f"B{r}"] = idx
        ws[f"B{r}"].font = xfont(bold=True, color="0D47A1", size=10)
        ws[f"B{r}"].fill = fill(C_YELLOW)
        ws[f"B{r}"].alignment = xalign("center")
        ws[f"B{r}"].border = xborder()
        ws[f"C{r}"].fill = fill(bg)
        ws[f"C{r}"].border = xborder()

    for i in range(len(DEFAULT_INDICES), 20):
        r = 7 + i
        ws.row_dimensions[r].height = 20
        ws[f"B{r}"].fill = fill(C_YELLOW)
        ws[f"B{r}"].alignment = xalign("center")
        ws[f"B{r}"].border = xborder()
        ws[f"C{r}"].fill = fill(C_WHITE if i % 2 else C_GREY)
        ws[f"C{r}"].border = xborder()


def read_index_tickers(wb):
    ws = wb[INDICES_SHEET]
    indices = []
    for row in range(7, 27):
        val = ws[f"B{row}"].value
        if val and str(val).strip() and str(val).strip() not in ("Index Ticker",):
            indices.append(str(val).strip().upper())
    return indices


def fetch_and_store_index_data(wb, indices):
    ws = wb[INDICES_SHEET]
    if not indices:
        return {}

    print(f"\n  Fetching index history: {', '.join(indices)}")

    all_data = {}
    for idx in indices:
        try:
            t    = yf.Ticker(idx)
            hist = t.history(period=PERIOD)["Close"]
            if hist.empty:
                print(f"  ! {idx} — no data returned")
                continue
            hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
            all_data[idx] = hist
            info = t.info
            name = info.get("shortName") or info.get("longName") or idx
            # Write name back into Indices tab
            for row in range(7, 27):
                if ws[f"B{row}"].value and str(ws[f"B{row}"].value).strip().upper() == idx:
                    ws[f"C{row}"] = name
                    ws[f"C{row}"].font = xfont(color=C_DARK, size=10)
                    break
            print(f"  + {idx}  ({len(hist)} trading days)")
        except Exception as e:
            print(f"  ! {idx} — {e}")

    if not all_data:
        return {}

    df = pd.DataFrame(all_data).sort_index()

    # Store in sheet: col B=Date, col C onwards = index prices
    # Header at INDEX_DATA_START
    r = INDEX_DATA_START
    ws.cell(row=r, column=2).value = "Price History (auto-generated, do not edit)"
    ws.cell(row=r, column=2).font  = xfont(italic=True, color="AAAAAA", size=8)

    r += 1
    ws.cell(row=r, column=2).value = "Date"
    ws.cell(row=r, column=2).font  = xfont(bold=True, color=C_WHITE, size=8)
    ws.cell(row=r, column=2).fill  = fill(C_MID)

    col_map = {}
    for i, idx in enumerate(df.columns):
        col = 3 + i
        col_map[idx] = col
        ws.cell(row=r, column=col).value = idx
        ws.cell(row=r, column=col).font  = xfont(bold=True, color=C_WHITE, size=8)
        ws.cell(row=r, column=col).fill  = fill(C_MID)
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(col)].width = 13

    for r_off, (date, row_data) in enumerate(df.iterrows()):
        row_r = INDEX_DATA_START + 2 + r_off
        ws.cell(row=row_r, column=2).value = date.strftime("%Y-%m-%d")
        ws.cell(row=row_r, column=2).font  = xfont(color="AAAAAA", size=7)
        for idx, col in col_map.items():
            val = row_data.get(idx)
            if pd.notna(val):
                ws.cell(row=row_r, column=col).value = round(float(val), 4)
                ws.cell(row=row_r, column=col).font  = xfont(color=C_DARK, size=7)
                ws.cell(row=row_r, column=col).number_format = "#,##0.00"

    now = datetime.datetime.now().strftime("%B %d, %Y at %I:%M %p")
    ws["B4"] = f"Last updated: {now}"
    return df


def load_index_data_from_sheet(wb):
    ws = wb[INDICES_SHEET]
    header_row = INDEX_DATA_START + 1
    col_map = {}
    for col in range(3, 30):
        val = ws.cell(row=header_row, column=col).value
        if val and str(val).strip():
            col_map[col] = str(val).strip().upper()
        elif col > 5 and not val:
            break

    if not col_map:
        return pd.DataFrame()

    rows = []
    for r in range(header_row + 1, header_row + 2000):
        date_val = ws.cell(row=r, column=2).value
        if not date_val:
            break
        try:
            row = {"Date": pd.to_datetime(str(date_val))}
        except Exception:
            break
        for col, ticker in col_map.items():
            row[ticker] = ws.cell(row=r, column=col).value
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("Date")
    df.index = pd.to_datetime(df.index).normalize()
    return df


# ── Chart builder ─────────────────────────────────────────────────────────────

def style_ax(ax):
    ax.set_facecolor(BG)
    for sp in ["top","right"]: ax.spines[sp].set_visible(False)
    for sp in ["left","bottom"]: ax.spines[sp].set_color("#1B4965")
    ax.tick_params(colors="#8AAABB", labelsize=9)
    ax.grid(True, color="#112233", linewidth=0.6, zorder=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")


def build_chart(ticker, stock_series, index_ticker, index_df):
    idx_key = index_ticker.strip().upper() if index_ticker else None
    show_rel = (idx_key and not index_df.empty and idx_key in index_df.columns)

    if show_rel:
        idx_s  = index_df[idx_key].dropna()
        s_norm = stock_series.copy()
        s_norm.index = s_norm.index.normalize()
        common = s_norm.index.intersection(idx_s.index)
        s_c    = s_norm.loc[common]
        i_c    = idx_s.loc[common]
        rel    = (s_c / s_c.iloc[0]) / (i_c / i_c.iloc[0])
    else:
        s_c = stock_series

    rows = 2 if show_rel else 1
    fig, axes = plt.subplots(rows, 1, figsize=(13, 5.5*rows),
                             facecolor=BG, gridspec_kw={"hspace":0.5})
    if rows == 1:
        axes = [axes]

    ax1 = axes[0]
    style_ax(ax1)
    start_p  = float(s_c.iloc[0])
    end_p    = float(s_c.iloc[-1])
    chg_pct  = (end_p - start_p) / start_p * 100
    lc       = "#2DC653" if chg_pct >= 0 else "#E63946"
    sign     = "+" if chg_pct >= 0 else ""
    ax1.plot(s_c.index, s_c.values, color=lc, linewidth=1.8, zorder=3)
    ax1.fill_between(s_c.index, s_c.values, s_c.min(), alpha=0.12, color=lc)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:,.2f}"))
    ax1.set_title(f"{ticker}  —  {end_p:,.2f}  ({sign}{chg_pct:.1f}%  over 5 years)",
                  color=lc, fontsize=13, fontweight="bold", pad=10, loc="left")
    ax1.set_ylabel("Price", color="#8AAABB", fontsize=9)
    ax1.annotate(f"{end_p:,.2f}", xy=(s_c.index[-1], end_p),
                 xytext=(8,0), textcoords="offset points",
                 color=lc, fontsize=9, fontweight="bold", va="center")

    if show_rel:
        ax2 = axes[1]
        style_ax(ax2)
        re     = float(rel.iloc[-1])
        rc_pct = (re - 1)*100
        rc     = "#2DC653" if rc_pct >= 0 else "#E63946"
        rs     = "+" if rc_pct >= 0 else ""
        ax2.plot(rel.index, rel.values, color=rc, linewidth=1.8, zorder=3)
        ax2.fill_between(rel.index, rel.values, rel.min(), alpha=0.12, color=rc)
        ax2.axhline(1.0, color="#334455", linewidth=0.9, linestyle="--", zorder=2)
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.2f}x"))
        ax2.set_title(
            f"Relative to {idx_key}  —  {rs}{rc_pct:.1f}%  vs index over 5 years",
            color=rc, fontsize=11, fontweight="bold", pad=10, loc="left")
        ax2.set_ylabel("Ratio  (1.0 = same as index)", color="#8AAABB", fontsize=9)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Stock sheet builder ───────────────────────────────────────────────────────

def make_stock_sheet(wb, ticker, index_ticker, available_indices):
    name = ticker[:31]
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False

    for col, w in [("A",2),("B",22),("C",18),("D",44),("E",2)]:
        ws.column_dimensions[col].width = w
    ws.row_dimensions[2].height = 30
    ws.row_dimensions[3].height = 20
    ws.row_dimensions[4].height = 20
    ws.row_dimensions[6].height = 22

    ws.merge_cells("B2:D2")
    ws["B2"] = f"  {ticker}  —  5-Year Price History"
    ws["B2"].font = xfont(bold=True, color=C_WHITE, size=14)
    ws["B2"].fill = fill(C_DARK); ws["B2"].alignment = xalign("center")

    ws.merge_cells("B3:D3")
    ws["B3"] = "Type an index in the yellow cell and re-run build_stock_charts.py for relative performance."
    ws["B3"].font = xfont(italic=True, color=C_WHITE, size=9)
    ws["B3"].fill = fill(C_MID); ws["B3"].alignment = xalign("center")

    now = datetime.datetime.now().strftime("%B %d, %Y at %I:%M %p")
    ws.merge_cells("B4:D4")
    ws["B4"] = f"Last updated: {now}"
    ws["B4"].font = xfont(italic=True, color="888888", size=9)
    ws["B4"].fill = fill(C_GREY); ws["B4"].alignment = xalign("right")

    ws["B6"] = "Compare vs Index"
    ws["B6"].font = xfont(bold=True, color=C_DARK, size=10)
    ws["B6"].fill = fill(C_GREY); ws["B6"].alignment = xalign("left")
    ws["B6"].border = xborder()

    ws["C6"] = index_ticker if index_ticker else ""
    ws["C6"].font = xfont(bold=True, color="0D47A1", size=11)
    ws["C6"].fill = fill(C_YELLOW); ws["C6"].alignment = xalign("center")
    ws["C6"].border = xborder()

    hint = "  Available: " + "  |  ".join(available_indices[:8])
    ws["D6"] = hint
    ws["D6"].font = xfont(italic=True, color="888888", size=9)
    ws["D6"].fill = fill(C_WHITE); ws["D6"].alignment = xalign("left")
    ws["D6"].border = xborder()

    for r in range(7, 70):
        ws.row_dimensions[r].height = 18

    return ws


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Watchlist — Per-Stock Chart Generator")
    print("=" * 60)

    try:
        wb = load_workbook(SPREADSHEET)
    except FileNotFoundError:
        print(f"\n  ERROR: '{SPREADSHEET}' not found.")
        sys.exit(1)

    ensure_indices_sheet(wb)
    wb.save(SPREADSHEET)
    wb = load_workbook(SPREADSHEET)

    indices = read_index_tickers(wb)
    print(f"\n  Indices: {', '.join(indices) if indices else 'none found in Indices tab'}")

    index_df_fetched = fetch_and_store_index_data(wb, indices)
    wb.save(SPREADSHEET)
    wb = load_workbook(SPREADSHEET)

    index_df = load_index_data_from_sheet(wb)
    available = list(index_df.columns) if not index_df.empty else indices

    ws_main = wb[WATCHLIST_SHEET]
    tickers = []
    for row in range(DATA_START_ROW, ws_main.max_row + 1):
        val = ws_main.cell(row=row, column=TICKER_COL).value
        if val and str(val).strip():
            tickers.append(str(val).strip().upper())

    if not tickers:
        print("\n  No tickers found in Watchlist tab.")
        sys.exit(0)

    print(f"\n  Building charts for: {', '.join(tickers)}\n")
    ok, failed = 0, 0

    for ticker in tickers:
        sheet_name = ticker[:31]
        # Use tab-specific index if set, otherwise fall back to default_index
        index_ticker = default_index
        if sheet_name in wb.sheetnames:
            val = wb[sheet_name]["C6"].value
            if val and str(val).strip():
                index_ticker = str(val).strip().upper()

        print(f"  {ticker}" + (f"  vs {index_ticker}" if index_ticker else "") + "...")

        try:
            hist = yf.Ticker(ticker).history(period=PERIOD)["Close"]
            if hist.empty:
                raise ValueError("No data returned")
            hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()

            buf = build_chart(ticker, hist, index_ticker, index_df)
            ws  = make_stock_sheet(wb, ticker, index_ticker, available)
            # Explicitly write index_ticker into yellow cell C6
            if index_ticker:
                ws["C6"].value = index_ticker
            img = XLImage(buf)
            img.anchor = "B8"
            ws.add_image(img)
            print(f"  + {ticker}")
            ok += 1

        except Exception as e:
            print(f"  ! {ticker}  —  {e}")
            failed += 1

    wb.save(SPREADSHEET)
    print()
    print(f"  Done:  {ok} chart(s) built,  {failed} failed.")
    print(f"  Saved: {SPREADSHEET}")
    print()
    print("  To overlay relative performance:")
    print("  1. Open the spreadsheet, go to a stock tab")
    print("  2. Type an index into the yellow cell (e.g. ^GSPC)")
    print("  3. Close the file, re-run:  py build_stock_charts.py")
    print("  (Index data is stored locally — no extra Yahoo fetch!)")
    print("=" * 60)

if __name__ == "__main__":
    main()
