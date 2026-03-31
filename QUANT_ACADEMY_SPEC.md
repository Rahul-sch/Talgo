# The Quant Academy — Technical Specification
## `st.session_state` Architecture & Anti-Reload Strategy

---

## 1. The Core Problem

Streamlit re-executes the entire script top-to-bottom on every user interaction (button click, slider change, widget toggle). Without careful state management, this means:

- Historical data gets re-fetched from yfinance on every click of "Next Candle."
- The current replay index resets to 0.
- Open paper trades, P&L history, and pattern alerts vanish.

The solution is a **single-initialization, mutation-only** pattern: we write to `st.session_state` once during the first run, then every subsequent re-run only *reads and mutates* existing state — never re-initializes it.

---

## 2. The `st.session_state` Dictionary Schema

Every key below is initialized exactly once, gated behind an `if "key" not in st.session_state:` check at the top of the script.

```python
# ─── DATA LAYER ───────────────────────────────────────────────
st.session_state["df"]              # pd.DataFrame — full OHLCV history (HIDDEN from chart)
st.session_state["ticker"]          # str — current ticker symbol (e.g., "AAPL")
st.session_state["timeframe"]       # str — candle interval (e.g., "1d", "1h")
st.session_state["data_loaded"]     # bool — flag: True once yfinance fetch completes

# ─── TIME MACHINE ─────────────────────────────────────────────
st.session_state["current_index"]   # int — pointer into df; chart renders df[:current_index+1]
st.session_state["max_index"]       # int — len(df) - 1; upper bound for navigation

# ─── PAPER TRADING LEDGER ─────────────────────────────────────
st.session_state["positions"]       # list[dict] — open positions (see §5 for schema)
st.session_state["closed_trades"]   # list[dict] — completed trades with realized P&L
st.session_state["balance"]         # float — starting paper balance (default 100,000)
st.session_state["floating_pnl"]    # float — unrealized P&L of all open positions

# ─── TUTOR / PATTERN ENGINE ──────────────────────────────────
st.session_state["detected_patterns"]  # list[dict] — patterns found on visible candles
st.session_state["pattern_alerts"]     # list[str] — human-readable alerts for sidebar

# ─── UI PREFERENCES ──────────────────────────────────────────
st.session_state["show_tutor"]      # bool — toggle pattern overlay on/off
st.session_state["show_volume"]     # bool — toggle volume sub-chart
```

---

## 3. Initialization Flow (The "Gate" Pattern)

This is the single most important architectural decision. The top of `market_replay.py` will follow this exact sequence:

```
┌─────────────────────────────────────────────┐
│  Script starts (Streamlit re-run)           │
│                                             │
│  if "data_loaded" not in st.session_state:  │
│      ├── Initialize ALL keys to defaults    │
│      ├── Show ticker input + "Load" button  │
│      └── st.stop()  ← halts first render   │
│                                             │
│  if not st.session_state["data_loaded"]:    │
│      ├── User clicks "Load Data"            │
│      ├── yfinance.download(ticker, ...)     │
│      ├── Store df in session_state["df"]    │
│      ├── Set current_index = 50 (warm-up)   │
│      ├── Set data_loaded = True             │
│      └── st.rerun()                         │
│                                             │
│  ── FROM HERE: data is guaranteed loaded ── │
│  ── Every re-run skips both gates above ──  │
│                                             │
│  Render chart with df[:current_index+1]     │
│  Render sidebar controls                    │
│  Process button clicks (mutate state only)  │
└─────────────────────────────────────────────┘
```

**Why `current_index = 50`?** Technical indicators (moving averages, RSI) need lookback periods. Starting at index 50 ensures at least 50 candles of warm-up data are visible on the initial chart render, giving indicators valid values from the start.

---

## 4. Replay Controls — State Mutations Only

Each navigation button mutates `current_index` and triggers a Streamlit re-run. No data re-fetch occurs.

```python
# Button handlers — all are pure state mutations
def advance(n=1):
    """Move forward by n candles, clamped to max_index."""
    st.session_state["current_index"] = min(
        st.session_state["current_index"] + n,
        st.session_state["max_index"]
    )
    _check_stop_loss_take_profit()  # evaluate SL/TP against new candle
    _run_pattern_detection()        # scan visible data for patterns

# UI Layout:
# [ ◀ Prev ] [ Next Candle ▶ ] [ Jump +5 ▶▶ ] [ Jump +10 ▶▶▶ ]
# Plus a slider showing current position within the full dataset
```

**State machine for controls:**

```
                 ┌──────────────┐
         Load ──▶│ idx = 50     │
                 │ (warm-up)    │
                 └──────┬───────┘
                        │
            ┌───────────▼───────────┐
            │  REPLAY ACTIVE        │
            │                       │
            │  Next    → idx += 1   │
            │  Jump +5 → idx += 5   │
            │  Jump +10→ idx += 10  │
            │  Prev    → idx -= 1   │
            │  Slider  → idx = val  │
            │                       │
            │  ALL clamped to       │
            │  [0, max_index]       │
            └───────────┬───────────┘
                        │
              idx == max_index?
                   │
                   ▼
            ┌──────────────┐
            │ END OF DATA  │
            │ (disable fwd │
            │  buttons)    │
            └──────────────┘
```

---

## 5. Paper Trading Ledger — Position Schema

Each open position is a dictionary:

```python
{
    "id":           str,      # uuid4 — unique trade identifier
    "side":         str,      # "long" or "short"
    "entry_price":  float,    # price at time of execution
    "entry_index":  int,      # candle index when trade was opened
    "quantity":     float,    # number of units (derived from risk sizing)
    "stop_loss":    float,    # price level — None if not set
    "take_profit":  float,    # price level — None if not set
    "status":       str,      # "open" → moved to closed_trades when resolved
}
```

Each closed trade adds:

```python
{
    ...position_fields,
    "exit_price":   float,
    "exit_index":   int,
    "exit_reason":  str,      # "manual_close" | "stop_loss" | "take_profit"
    "realized_pnl": float,
    "status":       "closed",
}
```

**P&L recalculation** happens inside `advance()` on every candle step:

```
For each open position:
    current_price = df.iloc[current_index]["Close"]
    if side == "long":
        floating_pnl += (current_price - entry_price) * quantity
    elif side == "short":
        floating_pnl += (entry_price - current_price) * quantity

    # Check SL/TP against the new candle's High/Low (not just Close)
    # to simulate realistic fills on intra-candle wicks
    candle_high = df.iloc[current_index]["High"]
    candle_low  = df.iloc[current_index]["Low"]

    if stop_loss and candle_low <= stop_loss (for longs):
        → close at stop_loss price, move to closed_trades
    if take_profit and candle_high >= take_profit (for longs):
        → close at take_profit price, move to closed_trades
```

---

## 6. Data Isolation — The "Fog of War"

The chart rendering function **never** receives the full dataframe. It always receives a slice:

```python
visible_df = st.session_state["df"].iloc[:st.session_state["current_index"] + 1]
```

This is the fundamental rule that makes the replay feel authentic. The user cannot peek ahead. All indicator calculations, pattern detection, and chart rendering operate exclusively on `visible_df`.

---

## 7. Pattern Detection — Tutor State

Pattern detection runs on `visible_df` after every candle advance. Results are stored in session state so they persist across re-runs without re-computation (unless the index changes).

```python
st.session_state["detected_patterns"] = [
    {
        "index":       int,      # candle index where pattern occurs
        "name":        str,      # e.g., "Bullish Engulfing"
        "type":        str,      # "bullish" | "bearish" | "neutral"
        "description": str,      # educational explanation
    },
    ...
]
```

Detection is **memoized by index**: if `current_index` hasn't changed, skip re-detection. This is tracked with:

```python
st.session_state["last_detected_index"]  # int — index of last pattern scan
```

---

## 8. Preventing Common Streamlit Pitfalls

| Pitfall | Our Mitigation |
|---|---|
| Data re-fetched every re-run | Single-init gate + `data_loaded` flag; df lives in session_state |
| State reset on widget interaction | Every key initialized with `if key not in st.session_state`; never overwritten unconditionally |
| Button callbacks fire on page load | Use `st.button()` return value inside `if` blocks, never as default state |
| Chart flickers on re-render | `streamlit-lightweight-charts` handles incremental updates; we pass the full visible slice each time |
| Slider + button conflict | Slider uses `on_change` callback that sets `current_index` directly; buttons use separate handlers |
| Large dataset memory bloat | yfinance data for typical ranges (1-5 years daily) is <50KB; no concern at this scale |

---

## 9. Module Build Order (Proposed)

Once this spec is approved, we build in this order — each module is testable independently:

1. **Module A — Data Loader & State Init:** The gate pattern, yfinance fetch, session_state initialization.
2. **Module B — Chart Renderer:** `streamlit-lightweight-charts` candlestick chart consuming `visible_df`.
3. **Module C — Replay Controls:** Next/Prev/Jump buttons + progress slider.
4. **Module D — Paper Trading Engine:** Buy/Sell/Close buttons, position management, P&L recalculation.
5. **Module E — Tutor Overlay:** Pattern detection functions + chart markers/sidebar alerts.
6. **Module F — Dashboard Polish:** Trade history table, equity curve, win-rate stats, export.

---

## 10. Key Dependencies

```
streamlit >= 1.30
streamlit-lightweight-charts >= 0.7
yfinance >= 0.2.30
pandas >= 2.0
```

---

*Awaiting your approval to proceed with Module A.*
