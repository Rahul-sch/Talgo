"""
The Quant Academy — Market Replay Simulator
A flight simulator for discretionary quantitative trading.

Module A: Data Loader & State Initialization
Module B: Chart Renderer (streamlit-lightweight-charts)
Module C: Replay Controls (Next/Prev/Jump/Slider)
"""

import streamlit as st
import pandas as pd
import yfinance as yf
import uuid
from datetime import datetime, timedelta
from streamlit_lightweight_charts import renderLightweightCharts

# ─── PAGE CONFIG ──────────────────────────────────────────────
st.set_page_config(
    page_title="The Quant Academy",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CONSTANTS ────────────────────────────────────────────────
WARMUP_CANDLES = 50
DEFAULT_BALANCE = 100_000.0
VALID_INTERVALS = ["1d", "1h", "5m", "15m", "30m"]
VALID_PERIODS = {
    "1d": ["6mo", "1y", "2y", "5y"],
    "1h": ["1mo", "3mo", "6mo"],
    "5m": ["5d", "1mo"],
    "15m": ["5d", "1mo"],
    "30m": ["5d", "1mo"],
}


# ═══════════════════════════════════════════════════════════════
# MODULE A: STATE INITIALIZATION (The Gate Pattern)
# ═══════════════════════════════════════════════════════════════

def init_session_state():
    """
    Initialize ALL session_state keys exactly once.
    This is the 'gate' — subsequent re-runs skip this entirely.
    """
    defaults = {
        # Data layer
        "df": pd.DataFrame(),
        "ticker": "",
        "timeframe": "1d",
        "period": "1y",
        "data_loaded": False,

        # Time machine
        "current_index": WARMUP_CANDLES,
        "max_index": 0,

        # Paper trading ledger
        "positions": [],
        "closed_trades": [],
        "balance": DEFAULT_BALANCE,
        "floating_pnl": 0.0,

        # Tutor / pattern engine
        "detected_patterns": [],
        "pattern_alerts": [],
        "last_detected_index": -1,
        "show_tutor": True,

        # UI preferences
        "show_volume": True,

        # Replay state
        "replay_active": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def fetch_market_data(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """
    Fetch OHLCV data from yfinance. Returns a clean DataFrame or empty on failure.
    """
    try:
        data = yf.download(
            ticker,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
        if data.empty:
            return pd.DataFrame()

        # Flatten MultiIndex columns if present (yfinance sometimes returns them)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        # Ensure standard column names
        data = data.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })

        # Keep only what we need, drop NaN rows
        data = data[["open", "high", "low", "close", "volume"]].dropna()
        data = data.reset_index()

        # Normalize the date/datetime column
        if "Date" in data.columns:
            data = data.rename(columns={"Date": "time"})
        elif "Datetime" in data.columns:
            data = data.rename(columns={"Datetime": "time"})

        # Convert time to string for lightweight-charts compatibility
        data["time"] = pd.to_datetime(data["time"]).dt.strftime("%Y-%m-%d")

        return data

    except Exception as e:
        st.error(f"Failed to fetch data for {ticker}: {e}")
        return pd.DataFrame()


def render_data_loader():
    """
    Render the data loading UI in the sidebar.
    Returns True if data was just loaded (triggers rerun).
    """
    st.sidebar.markdown("## 📊 Data Loader")

    ticker = st.sidebar.text_input(
        "Ticker Symbol",
        value="AAPL",
        placeholder="e.g., AAPL, MSFT, TSLA",
        key="ticker_input",
    )

    interval = st.sidebar.selectbox(
        "Candle Interval",
        VALID_INTERVALS,
        index=0,
        key="interval_input",
    )

    # Dynamic period options based on interval
    available_periods = VALID_PERIODS.get(interval, ["1y"])
    period = st.sidebar.selectbox(
        "History Period",
        available_periods,
        index=0,
        key="period_input",
    )

    if st.sidebar.button("🚀 Load Data & Start Replay", use_container_width=True):
        with st.spinner(f"Fetching {ticker} data..."):
            df = fetch_market_data(ticker.upper().strip(), interval, period)

        if df.empty:
            st.sidebar.error("No data returned. Check the ticker symbol and try again.")
            return False

        if len(df) <= WARMUP_CANDLES:
            st.sidebar.error(
                f"Only {len(df)} candles fetched — need at least {WARMUP_CANDLES + 1} "
                f"for warm-up. Try a longer period."
            )
            return False

        # Store data and activate replay
        st.session_state["df"] = df
        st.session_state["ticker"] = ticker.upper().strip()
        st.session_state["timeframe"] = interval
        st.session_state["period"] = period
        st.session_state["current_index"] = WARMUP_CANDLES
        st.session_state["max_index"] = len(df) - 1
        st.session_state["data_loaded"] = True
        st.session_state["replay_active"] = True

        # Reset trading state for new dataset
        st.session_state["positions"] = []
        st.session_state["closed_trades"] = []
        st.session_state["balance"] = DEFAULT_BALANCE
        st.session_state["floating_pnl"] = 0.0
        st.session_state["detected_patterns"] = []
        st.session_state["pattern_alerts"] = []
        st.session_state["last_detected_index"] = -1

        st.rerun()

    return False


# ═══════════════════════════════════════════════════════════════
# MODULE B: CHART RENDERER
# ═══════════════════════════════════════════════════════════════

def get_visible_data() -> pd.DataFrame:
    """
    Return ONLY the data up to current_index (fog of war).
    This is the single source of truth for all rendering.
    """
    idx = st.session_state["current_index"]
    return st.session_state["df"].iloc[:idx + 1].copy()


def render_chart(visible_df: pd.DataFrame):
    """
    Render the candlestick chart using streamlit-lightweight-charts.
    """
    if visible_df.empty:
        st.warning("No data to display.")
        return

    # Prepare candlestick data
    candle_data = visible_df[["time", "open", "high", "low", "close"]].to_dict("records")

    # Prepare volume data with color coding
    volume_data = []
    for _, row in visible_df.iterrows():
        color = "rgba(38, 166, 154, 0.5)" if row["close"] >= row["open"] else "rgba(239, 83, 80, 0.5)"
        volume_data.append({
            "time": row["time"],
            "value": float(row["volume"]),
            "color": color,
        })

    # Build chart options
    chart_options = {
        "layout": {
            "background": {"type": "solid", "color": "#131722"},
            "textColor": "#d1d4dc",
        },
        "grid": {
            "vertLines": {"color": "#1e222d"},
            "horzLines": {"color": "#1e222d"},
        },
        "crosshair": {"mode": 0},
        "rightPriceScale": {
            "borderColor": "#2B2B43",
            "visible": True,
        },
        "timeScale": {
            "borderColor": "#2B2B43",
            "timeVisible": True,
        },
        "width": 0,  # auto-width
        "height": 500,
    }

    # Build series list
    series = [
        {
            "type": "Candlestick",
            "data": candle_data,
            "options": {
                "upColor": "#26a69a",
                "downColor": "#ef5350",
                "borderVisible": True,
                "wickUpColor": "#26a69a",
                "wickDownColor": "#ef5350",
            },
        }
    ]

    if st.session_state["show_volume"]:
        series.append({
            "type": "Histogram",
            "data": volume_data,
            "options": {
                "priceFormat": {"type": "volume"},
                "priceScaleId": "volume",
            },
            "priceScale": {
                "scaleMargins": {"top": 0.8, "bottom": 0},
                "alignLabels": False,
            },
        })

    # Add pattern markers if tutor is enabled
    markers = []
    if st.session_state["show_tutor"] and st.session_state["detected_patterns"]:
        for pattern in st.session_state["detected_patterns"]:
            p_idx = pattern["index"]
            if p_idx < len(visible_df):
                marker_color = "#26a69a" if pattern["type"] == "bullish" else "#ef5350" if pattern["type"] == "bearish" else "#ffeb3b"
                position = "belowBar" if pattern["type"] == "bullish" else "aboveBar"
                shape = "arrowUp" if pattern["type"] == "bullish" else "arrowDown" if pattern["type"] == "bearish" else "circle"
                markers.append({
                    "time": visible_df.iloc[p_idx]["time"],
                    "position": position,
                    "color": marker_color,
                    "shape": shape,
                    "text": pattern["name"],
                })

    if markers:
        series[0]["markers"] = markers

    # Render
    renderLightweightCharts([
        {"chart": chart_options, "series": series}
    ], key=f"chart_{st.session_state['current_index']}")


# ═══════════════════════════════════════════════════════════════
# MODULE C: REPLAY CONTROLS
# ═══════════════════════════════════════════════════════════════

def check_stop_loss_take_profit():
    """
    Evaluate SL/TP for all open positions against the current candle.
    Uses High/Low for realistic intra-candle fill simulation.
    """
    idx = st.session_state["current_index"]
    df = st.session_state["df"]
    candle = df.iloc[idx]
    candle_high = float(candle["high"])
    candle_low = float(candle["low"])

    positions_to_close = []

    for i, pos in enumerate(st.session_state["positions"]):
        exit_price = None
        exit_reason = None

        if pos["side"] == "long":
            if pos["stop_loss"] is not None and candle_low <= pos["stop_loss"]:
                exit_price = pos["stop_loss"]
                exit_reason = "stop_loss"
            elif pos["take_profit"] is not None and candle_high >= pos["take_profit"]:
                exit_price = pos["take_profit"]
                exit_reason = "take_profit"
        elif pos["side"] == "short":
            if pos["stop_loss"] is not None and candle_high >= pos["stop_loss"]:
                exit_price = pos["stop_loss"]
                exit_reason = "stop_loss"
            elif pos["take_profit"] is not None and candle_low <= pos["take_profit"]:
                exit_price = pos["take_profit"]
                exit_reason = "take_profit"

        if exit_price is not None:
            positions_to_close.append((i, exit_price, exit_reason))

    # Close positions in reverse order to preserve indices
    for i, exit_price, exit_reason in reversed(positions_to_close):
        pos = st.session_state["positions"].pop(i)
        if pos["side"] == "long":
            realized_pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        else:
            realized_pnl = (pos["entry_price"] - exit_price) * pos["quantity"]

        closed_trade = {
            **pos,
            "exit_price": exit_price,
            "exit_index": idx,
            "exit_reason": exit_reason,
            "realized_pnl": realized_pnl,
            "status": "closed",
        }
        st.session_state["closed_trades"].append(closed_trade)
        st.session_state["balance"] += realized_pnl


def recalculate_floating_pnl():
    """Recalculate unrealized P&L for all open positions."""
    idx = st.session_state["current_index"]
    current_price = float(st.session_state["df"].iloc[idx]["close"])
    total_pnl = 0.0

    for pos in st.session_state["positions"]:
        if pos["side"] == "long":
            total_pnl += (current_price - pos["entry_price"]) * pos["quantity"]
        elif pos["side"] == "short":
            total_pnl += (pos["entry_price"] - current_price) * pos["quantity"]

    st.session_state["floating_pnl"] = total_pnl


def advance(n: int = 1):
    """Move forward by n candles, clamped to max_index."""
    new_idx = min(
        st.session_state["current_index"] + n,
        st.session_state["max_index"],
    )
    if new_idx != st.session_state["current_index"]:
        # Step one candle at a time so SL/TP is checked on every candle
        start = st.session_state["current_index"] + 1
        for step_idx in range(start, new_idx + 1):
            st.session_state["current_index"] = step_idx
            check_stop_loss_take_profit()

        recalculate_floating_pnl()
        run_pattern_detection()


def go_back(n: int = 1):
    """Move backward by n candles, clamped to 0."""
    new_idx = max(
        st.session_state["current_index"] - n,
        WARMUP_CANDLES,  # Never go below warm-up
    )
    st.session_state["current_index"] = new_idx
    recalculate_floating_pnl()
    run_pattern_detection()


def render_replay_controls():
    """Render the replay navigation controls."""
    idx = st.session_state["current_index"]
    max_idx = st.session_state["max_index"]

    st.markdown("---")

    # Progress info
    col_info1, col_info2, col_info3 = st.columns(3)
    visible_df = get_visible_data()
    current_candle = visible_df.iloc[-1]

    with col_info1:
        st.metric("Current Date", current_candle["time"])
    with col_info2:
        st.metric("Candle", f"{idx + 1} / {max_idx + 1}")
    with col_info3:
        pct = ((idx + 1) / (max_idx + 1)) * 100
        st.metric("Progress", f"{pct:.1f}%")

    # Navigation buttons
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    with col1:
        if st.button("⏪ -10", use_container_width=True, disabled=(idx <= WARMUP_CANDLES)):
            go_back(10)
            st.rerun()
    with col2:
        if st.button("◀ -5", use_container_width=True, disabled=(idx <= WARMUP_CANDLES)):
            go_back(5)
            st.rerun()
    with col3:
        if st.button("◀ Prev", use_container_width=True, disabled=(idx <= WARMUP_CANDLES)):
            go_back(1)
            st.rerun()
    with col4:
        if st.button("Next ▶", use_container_width=True, disabled=(idx >= max_idx), type="primary"):
            advance(1)
            st.rerun()
    with col5:
        if st.button("+5 ▶", use_container_width=True, disabled=(idx >= max_idx)):
            advance(5)
            st.rerun()
    with col6:
        if st.button("+10 ⏩", use_container_width=True, disabled=(idx >= max_idx)):
            advance(10)
            st.rerun()

    # Slider for direct navigation
    new_idx = st.slider(
        "Jump to candle",
        min_value=WARMUP_CANDLES,
        max_value=max_idx,
        value=idx,
        key="nav_slider",
    )
    if new_idx != idx:
        if new_idx > idx:
            advance(new_idx - idx)
        else:
            go_back(idx - new_idx)
        st.rerun()


# ═══════════════════════════════════════════════════════════════
# MODULE D: PAPER TRADING ENGINE
# ═══════════════════════════════════════════════════════════════

def execute_trade(side: str, stop_loss: float | None, take_profit: float | None, quantity: float):
    """Open a new paper trade at the current candle's close price."""
    idx = st.session_state["current_index"]
    entry_price = float(st.session_state["df"].iloc[idx]["close"])

    position = {
        "id": str(uuid.uuid4())[:8],
        "side": side,
        "entry_price": entry_price,
        "entry_index": idx,
        "entry_time": st.session_state["df"].iloc[idx]["time"],
        "quantity": quantity,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "status": "open",
    }

    st.session_state["positions"].append(position)
    recalculate_floating_pnl()


def close_position(position_id: str):
    """Close a specific position at the current candle's close price."""
    idx = st.session_state["current_index"]
    exit_price = float(st.session_state["df"].iloc[idx]["close"])

    for i, pos in enumerate(st.session_state["positions"]):
        if pos["id"] == position_id:
            pos_removed = st.session_state["positions"].pop(i)

            if pos_removed["side"] == "long":
                realized_pnl = (exit_price - pos_removed["entry_price"]) * pos_removed["quantity"]
            else:
                realized_pnl = (pos_removed["entry_price"] - exit_price) * pos_removed["quantity"]

            closed_trade = {
                **pos_removed,
                "exit_price": exit_price,
                "exit_index": idx,
                "exit_reason": "manual_close",
                "realized_pnl": realized_pnl,
                "status": "closed",
            }
            st.session_state["closed_trades"].append(closed_trade)
            st.session_state["balance"] += realized_pnl
            break

    recalculate_floating_pnl()


def close_all_positions():
    """Close all open positions at current price."""
    position_ids = [p["id"] for p in st.session_state["positions"]]
    for pid in position_ids:
        close_position(pid)


def render_trading_panel():
    """Render the paper trading controls in the sidebar."""
    st.sidebar.markdown("---")
    st.sidebar.markdown("## 💰 Paper Trading")

    current_price = float(
        st.session_state["df"].iloc[st.session_state["current_index"]]["close"]
    )
    st.sidebar.metric("Current Price", f"${current_price:,.2f}")

    # Account summary
    balance = st.session_state["balance"]
    floating = st.session_state["floating_pnl"]
    equity = balance + floating

    col_b, col_f = st.sidebar.columns(2)
    with col_b:
        st.metric("Balance", f"${balance:,.2f}")
    with col_f:
        delta_color = "normal" if floating >= 0 else "inverse"
        st.metric("Floating P&L", f"${floating:,.2f}",
                  delta=f"${floating:,.2f}", delta_color=delta_color)

    st.sidebar.metric("Total Equity", f"${equity:,.2f}")

    # Trade inputs
    st.sidebar.markdown("### New Order")
    quantity = st.sidebar.number_input("Quantity (shares)", min_value=1, value=100, step=10, key="qty_input")

    use_sl = st.sidebar.checkbox("Set Stop Loss", key="use_sl")
    stop_loss = None
    if use_sl:
        stop_loss = st.sidebar.number_input(
            "Stop Loss Price", value=round(current_price * 0.98, 2),
            step=0.01, format="%.2f", key="sl_input"
        )

    use_tp = st.sidebar.checkbox("Set Take Profit", key="use_tp")
    take_profit = None
    if use_tp:
        take_profit = st.sidebar.number_input(
            "Take Profit Price", value=round(current_price * 1.02, 2),
            step=0.01, format="%.2f", key="tp_input"
        )

    col_buy, col_sell = st.sidebar.columns(2)
    with col_buy:
        if st.button("🟢 BUY", use_container_width=True, key="buy_btn"):
            execute_trade("long", stop_loss, take_profit, quantity)
            st.rerun()
    with col_sell:
        if st.button("🔴 SELL", use_container_width=True, key="sell_btn"):
            execute_trade("short", stop_loss, take_profit, quantity)
            st.rerun()

    if st.session_state["positions"]:
        if st.sidebar.button("❌ Close All Positions", use_container_width=True, key="close_all_btn"):
            close_all_positions()
            st.rerun()

    # Open positions display
    if st.session_state["positions"]:
        st.sidebar.markdown("### Open Positions")
        for pos in st.session_state["positions"]:
            side_emoji = "🟢" if pos["side"] == "long" else "🔴"
            if pos["side"] == "long":
                pos_pnl = (current_price - pos["entry_price"]) * pos["quantity"]
            else:
                pos_pnl = (pos["entry_price"] - current_price) * pos["quantity"]

            pnl_color = "green" if pos_pnl >= 0 else "red"
            st.sidebar.markdown(
                f"{side_emoji} **{pos['side'].upper()}** {pos['quantity']} @ "
                f"${pos['entry_price']:.2f} | "
                f"P&L: :{pnl_color}[${pos_pnl:,.2f}]"
            )
            sl_str = f"${pos['stop_loss']:.2f}" if pos['stop_loss'] else "None"
            tp_str = f"${pos['take_profit']:.2f}" if pos['take_profit'] else "None"
            st.sidebar.caption(f"SL: {sl_str} | TP: {tp_str} | ID: {pos['id']}")

            if st.sidebar.button(f"Close {pos['id']}", key=f"close_{pos['id']}"):
                close_position(pos["id"])
                st.rerun()


# ═══════════════════════════════════════════════════════════════
# MODULE E: TUTOR OVERLAY (Pattern Detection)
# ═══════════════════════════════════════════════════════════════

def detect_doji(o, h, l, c):
    """Doji: body is very small relative to the total range."""
    body = abs(c - o)
    total_range = h - l
    if total_range == 0:
        return False
    return body / total_range < 0.1


def detect_hammer(o, h, l, c):
    """Hammer: small body at top, long lower shadow, little/no upper shadow."""
    body = abs(c - o)
    total_range = h - l
    if total_range == 0:
        return False
    body_top = max(o, c)
    body_bottom = min(o, c)
    upper_shadow = h - body_top
    lower_shadow = body_bottom - l
    return (lower_shadow >= 2 * body and upper_shadow <= body * 0.5 and body / total_range < 0.4)


def detect_inverted_hammer(o, h, l, c):
    """Inverted Hammer: small body at bottom, long upper shadow."""
    body = abs(c - o)
    total_range = h - l
    if total_range == 0:
        return False
    body_top = max(o, c)
    body_bottom = min(o, c)
    upper_shadow = h - body_top
    lower_shadow = body_bottom - l
    return (upper_shadow >= 2 * body and lower_shadow <= body * 0.5 and body / total_range < 0.4)


def detect_bullish_engulfing(prev_o, prev_c, curr_o, curr_c):
    """Bullish Engulfing: previous bearish candle fully engulfed by current bullish candle."""
    prev_bearish = prev_c < prev_o
    curr_bullish = curr_c > curr_o
    engulfs = curr_o <= prev_c and curr_c >= prev_o
    return prev_bearish and curr_bullish and engulfs


def detect_bearish_engulfing(prev_o, prev_c, curr_o, curr_c):
    """Bearish Engulfing: previous bullish candle fully engulfed by current bearish candle."""
    prev_bullish = prev_c > prev_o
    curr_bearish = curr_c < curr_o
    engulfs = curr_o >= prev_c and curr_c <= prev_o
    return prev_bullish and curr_bearish and engulfs


def detect_morning_star(candles_3):
    """Morning Star: 3-candle bullish reversal — big bear, small body, big bull."""
    if len(candles_3) < 3:
        return False
    c0_o, c0_c = candles_3[0]["open"], candles_3[0]["close"]
    c1_o, c1_c = candles_3[1]["open"], candles_3[1]["close"]
    c2_o, c2_c = candles_3[2]["open"], candles_3[2]["close"]

    first_bearish = c0_c < c0_o and abs(c0_c - c0_o) > 0
    small_body = abs(c1_c - c1_o) < abs(c0_c - c0_o) * 0.3
    third_bullish = c2_c > c2_o and c2_c > (c0_o + c0_c) / 2
    return first_bearish and small_body and third_bullish


def detect_evening_star(candles_3):
    """Evening Star: 3-candle bearish reversal — big bull, small body, big bear."""
    if len(candles_3) < 3:
        return False
    c0_o, c0_c = candles_3[0]["open"], candles_3[0]["close"]
    c1_o, c1_c = candles_3[1]["open"], candles_3[1]["close"]
    c2_o, c2_c = candles_3[2]["open"], candles_3[2]["close"]

    first_bullish = c0_c > c0_o and abs(c0_c - c0_o) > 0
    small_body = abs(c1_c - c1_o) < abs(c0_c - c0_o) * 0.3
    third_bearish = c2_c < c2_o and c2_c < (c0_o + c0_c) / 2
    return first_bullish and small_body and third_bearish


PATTERN_DESCRIPTIONS = {
    "Doji": "Indecision candle — open and close are nearly equal. The market is undecided; watch for a breakout in either direction.",
    "Hammer": "Bullish reversal signal after a downtrend. The long lower wick shows buyers stepped in aggressively.",
    "Inverted Hammer": "Potential bullish reversal. Buyers pushed price up during the session but sellers brought it back down. Needs confirmation.",
    "Bullish Engulfing": "Strong bullish reversal — the current candle completely engulfs the previous bearish candle, showing a shift in momentum to buyers.",
    "Bearish Engulfing": "Strong bearish reversal — the current candle completely engulfs the previous bullish candle, showing a shift in momentum to sellers.",
    "Morning Star": "Three-candle bullish reversal pattern. A large bearish candle, followed by indecision, followed by a strong bullish candle. Signals a potential bottom.",
    "Evening Star": "Three-candle bearish reversal pattern. A large bullish candle, followed by indecision, followed by a strong bearish candle. Signals a potential top.",
}


def run_pattern_detection():
    """
    Scan visible data for candlestick patterns on the current and recent candles.
    Memoized by current_index to avoid redundant computation.
    """
    idx = st.session_state["current_index"]

    # Skip if already detected for this index
    if st.session_state["last_detected_index"] == idx:
        return

    visible_df = get_visible_data()
    patterns = []

    if len(visible_df) < 2:
        st.session_state["detected_patterns"] = patterns
        st.session_state["pattern_alerts"] = []
        st.session_state["last_detected_index"] = idx
        return

    # Current candle
    curr = visible_df.iloc[-1]
    curr_o, curr_h, curr_l, curr_c = float(curr["open"]), float(curr["high"]), float(curr["low"]), float(curr["close"])

    # Previous candle
    prev = visible_df.iloc[-2]
    prev_o, prev_h, prev_l, prev_c = float(prev["open"]), float(prev["high"]), float(prev["low"]), float(prev["close"])

    current_abs_idx = len(visible_df) - 1

    # Single-candle patterns
    if detect_doji(curr_o, curr_h, curr_l, curr_c):
        patterns.append({"index": current_abs_idx, "name": "Doji", "type": "neutral",
                         "description": PATTERN_DESCRIPTIONS["Doji"]})

    if detect_hammer(curr_o, curr_h, curr_l, curr_c):
        patterns.append({"index": current_abs_idx, "name": "Hammer", "type": "bullish",
                         "description": PATTERN_DESCRIPTIONS["Hammer"]})

    if detect_inverted_hammer(curr_o, curr_h, curr_l, curr_c):
        patterns.append({"index": current_abs_idx, "name": "Inverted Hammer", "type": "bullish",
                         "description": PATTERN_DESCRIPTIONS["Inverted Hammer"]})

    # Two-candle patterns
    if detect_bullish_engulfing(prev_o, prev_c, curr_o, curr_c):
        patterns.append({"index": current_abs_idx, "name": "Bullish Engulfing", "type": "bullish",
                         "description": PATTERN_DESCRIPTIONS["Bullish Engulfing"]})

    if detect_bearish_engulfing(prev_o, prev_c, curr_o, curr_c):
        patterns.append({"index": current_abs_idx, "name": "Bearish Engulfing", "type": "bearish",
                         "description": PATTERN_DESCRIPTIONS["Bearish Engulfing"]})

    # Three-candle patterns
    if len(visible_df) >= 3:
        last_3 = [
            {"open": float(visible_df.iloc[-3]["open"]), "close": float(visible_df.iloc[-3]["close"])},
            {"open": float(visible_df.iloc[-2]["open"]), "close": float(visible_df.iloc[-2]["close"])},
            {"open": float(visible_df.iloc[-1]["open"]), "close": float(visible_df.iloc[-1]["close"])},
        ]
        if detect_morning_star(last_3):
            patterns.append({"index": current_abs_idx, "name": "Morning Star", "type": "bullish",
                             "description": PATTERN_DESCRIPTIONS["Morning Star"]})
        if detect_evening_star(last_3):
            patterns.append({"index": current_abs_idx, "name": "Evening Star", "type": "bearish",
                             "description": PATTERN_DESCRIPTIONS["Evening Star"]})

    st.session_state["detected_patterns"] = patterns
    st.session_state["pattern_alerts"] = [
        f"{'🟢' if p['type'] == 'bullish' else '🔴' if p['type'] == 'bearish' else '🟡'} "
        f"**{p['name']}** — {p['description']}"
        for p in patterns
    ]
    st.session_state["last_detected_index"] = idx


def render_tutor_panel():
    """Render pattern alerts in the sidebar."""
    st.sidebar.markdown("---")
    st.sidebar.markdown("## 🎓 Tutor")

    st.session_state["show_tutor"] = st.sidebar.checkbox(
        "Enable Pattern Detection", value=st.session_state["show_tutor"], key="tutor_toggle"
    )
    st.session_state["show_volume"] = st.sidebar.checkbox(
        "Show Volume", value=st.session_state["show_volume"], key="volume_toggle"
    )

    if st.session_state["show_tutor"] and st.session_state["pattern_alerts"]:
        st.sidebar.markdown("### Patterns Detected")
        for alert in st.session_state["pattern_alerts"]:
            st.sidebar.markdown(alert)
    elif st.session_state["show_tutor"]:
        st.sidebar.caption("No patterns detected on the current candle.")


# ═══════════════════════════════════════════════════════════════
# MODULE F: DASHBOARD (Trade History, Stats, Equity Curve)
# ═══════════════════════════════════════════════════════════════

def render_trade_history():
    """Render closed trades table and performance stats."""
    if not st.session_state["closed_trades"]:
        return

    st.markdown("### 📋 Trade History")

    trades_data = []
    for t in st.session_state["closed_trades"]:
        trades_data.append({
            "ID": t["id"],
            "Side": t["side"].upper(),
            "Entry": f"${t['entry_price']:.2f}",
            "Exit": f"${t['exit_price']:.2f}",
            "Qty": t["quantity"],
            "P&L": f"${t['realized_pnl']:,.2f}",
            "Reason": t["exit_reason"],
            "Entry Time": t.get("entry_time", ""),
        })

    st.dataframe(pd.DataFrame(trades_data), use_container_width=True, hide_index=True)


def render_performance_stats():
    """Render key performance metrics."""
    closed = st.session_state["closed_trades"]
    if not closed:
        return

    st.markdown("### 📊 Performance")

    total_trades = len(closed)
    winners = [t for t in closed if t["realized_pnl"] > 0]
    losers = [t for t in closed if t["realized_pnl"] < 0]
    win_rate = len(winners) / total_trades * 100 if total_trades > 0 else 0
    total_pnl = sum(t["realized_pnl"] for t in closed)
    avg_win = sum(t["realized_pnl"] for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t["realized_pnl"] for t in losers) / len(losers) if losers else 0
    profit_factor = abs(sum(t["realized_pnl"] for t in winners) / sum(t["realized_pnl"] for t in losers)) if losers and sum(t["realized_pnl"] for t in losers) != 0 else float('inf')

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Trades", total_trades)
    with col2:
        st.metric("Win Rate", f"{win_rate:.1f}%")
    with col3:
        st.metric("Total P&L", f"${total_pnl:,.2f}")
    with col4:
        pf_str = f"{profit_factor:.2f}" if profit_factor != float('inf') else "∞"
        st.metric("Profit Factor", pf_str)

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        st.metric("Winners", len(winners))
    with col6:
        st.metric("Losers", len(losers))
    with col7:
        st.metric("Avg Win", f"${avg_win:,.2f}")
    with col8:
        st.metric("Avg Loss", f"${avg_loss:,.2f}")


# ═══════════════════════════════════════════════════════════════
# MAIN APP
# ═══════════════════════════════════════════════════════════════

def main():
    # Gate 1: Initialize state
    init_session_state()

    # Header
    st.title("📈 The Quant Academy")
    st.caption("A flight simulator for discretionary trading")

    # Sidebar: Data loader (always visible)
    render_data_loader()

    # Gate 2: If data not loaded, show landing page
    if not st.session_state["data_loaded"]:
        st.markdown("---")
        st.markdown(
            """
            ### Welcome to The Quant Academy

            **How it works:**
            1. Enter a ticker symbol and select your candle interval in the sidebar
            2. Click **Load Data & Start Replay** to begin
            3. Use the replay controls to step through candles one at a time
            4. Practice buying and selling with the paper trading panel
            5. Learn from the Tutor's candlestick pattern detection

            *Select a ticker in the sidebar to get started.*
            """
        )
        st.stop()

    # ── DATA IS LOADED — RENDER THE SIMULATOR ──

    # Sidebar: trading panel and tutor
    render_trading_panel()
    render_tutor_panel()

    # Ticker info bar
    ticker = st.session_state["ticker"]
    timeframe = st.session_state["timeframe"]
    st.markdown(f"**{ticker}** | {timeframe} | Replay Mode")

    # Run pattern detection (memoized)
    run_pattern_detection()

    # Chart
    visible_df = get_visible_data()
    render_chart(visible_df)

    # Replay controls
    render_replay_controls()

    # Trade history and stats (below chart)
    render_trade_history()
    render_performance_stats()


if __name__ == "__main__":
    main()
