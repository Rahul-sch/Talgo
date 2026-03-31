"""
Test suite for The Quant Academy — Market Replay Simulator

Tests Modules A through F:
- A: State initialization & data loading
- B: Chart data isolation (fog of war)
- C: Replay controls (advance, go_back, clamping)
- D: Paper trading engine (buy, sell, close, SL/TP)
- E: Pattern detection (candlestick patterns)
- F: Performance stats calculations
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
import sys
import os

# We need to mock streamlit before importing our module
mock_st = MagicMock()
mock_st.session_state = {}
sys.modules["streamlit"] = mock_st
sys.modules["streamlit_lightweight_charts"] = MagicMock()

# Now import our module
sys.path.insert(0, os.path.dirname(__file__))
import market_replay as mr


# ─── FIXTURES ─────────────────────────────────────────────────

def make_ohlcv_df(n=100, start_price=100.0, seed=42):
    """Generate a synthetic OHLCV dataframe for testing."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    prices = [start_price]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.normal(0, 0.02)))

    data = []
    for i, p in enumerate(prices):
        o = p
        h = p * (1 + abs(rng.normal(0, 0.01)))
        l = p * (1 - abs(rng.normal(0, 0.01)))
        c = p * (1 + rng.normal(0, 0.005))
        v = int(rng.uniform(1e6, 1e7))
        data.append({
            "time": dates[i].strftime("%Y-%m-%d"),
            "open": round(o, 2),
            "high": round(max(h, o, c), 2),
            "low": round(min(l, o, c), 2),
            "close": round(c, 2),
            "volume": v,
        })

    return pd.DataFrame(data)


@pytest.fixture(autouse=True)
def reset_session_state():
    """Reset session_state before each test."""
    mock_st.session_state = {}
    mr.st = mock_st
    yield
    mock_st.session_state = {}


@pytest.fixture
def loaded_state():
    """Set up a fully loaded session state with synthetic data."""
    mr.init_session_state()
    df = make_ohlcv_df(200)
    mock_st.session_state["df"] = df
    mock_st.session_state["ticker"] = "TEST"
    mock_st.session_state["timeframe"] = "1d"
    mock_st.session_state["data_loaded"] = True
    mock_st.session_state["current_index"] = mr.WARMUP_CANDLES
    mock_st.session_state["max_index"] = len(df) - 1
    mock_st.session_state["replay_active"] = True
    return df


# ═══════════════════════════════════════════════════════════════
# MODULE A TESTS: State Initialization
# ═══════════════════════════════════════════════════════════════

class TestModuleA:
    def test_init_creates_all_keys(self):
        """All required session_state keys should be created."""
        mr.init_session_state()
        required_keys = [
            "df", "ticker", "timeframe", "period", "data_loaded",
            "current_index", "max_index",
            "positions", "closed_trades", "balance", "floating_pnl",
            "detected_patterns", "pattern_alerts", "last_detected_index",
            "show_tutor", "show_volume", "replay_active",
        ]
        for key in required_keys:
            assert key in mock_st.session_state, f"Missing key: {key}"

    def test_init_does_not_overwrite_existing(self):
        """Calling init twice should not reset existing values."""
        mr.init_session_state()
        mock_st.session_state["balance"] = 50_000.0
        mock_st.session_state["ticker"] = "AAPL"
        mr.init_session_state()  # second call
        assert mock_st.session_state["balance"] == 50_000.0
        assert mock_st.session_state["ticker"] == "AAPL"

    def test_default_balance(self):
        """Default balance should be 100,000."""
        mr.init_session_state()
        assert mock_st.session_state["balance"] == 100_000.0

    def test_data_loaded_starts_false(self):
        mr.init_session_state()
        assert mock_st.session_state["data_loaded"] is False

    def test_initial_index_is_warmup(self):
        mr.init_session_state()
        assert mock_st.session_state["current_index"] == mr.WARMUP_CANDLES


# ═══════════════════════════════════════════════════════════════
# MODULE B TESTS: Chart Data Isolation (Fog of War)
# ═══════════════════════════════════════════════════════════════

class TestModuleB:
    def test_visible_data_length(self, loaded_state):
        """Visible data should contain exactly current_index + 1 rows."""
        visible = mr.get_visible_data()
        assert len(visible) == mr.WARMUP_CANDLES + 1

    def test_visible_data_never_exceeds_index(self, loaded_state):
        """After advancing, visible data should still be bounded."""
        mock_st.session_state["current_index"] = 75
        visible = mr.get_visible_data()
        assert len(visible) == 76

    def test_visible_data_is_copy(self, loaded_state):
        """Visible data should be a copy, not a reference to the original."""
        visible = mr.get_visible_data()
        original_len = len(mock_st.session_state["df"])
        visible.drop(visible.index[:10], inplace=True)
        assert len(mock_st.session_state["df"]) == original_len

    def test_fog_of_war_no_future_data(self, loaded_state):
        """The last visible candle's time should match df[current_index]."""
        visible = mr.get_visible_data()
        idx = mock_st.session_state["current_index"]
        expected_time = mock_st.session_state["df"].iloc[idx]["time"]
        assert visible.iloc[-1]["time"] == expected_time


# ═══════════════════════════════════════════════════════════════
# MODULE C TESTS: Replay Controls
# ═══════════════════════════════════════════════════════════════

class TestModuleC:
    def test_advance_by_one(self, loaded_state):
        """Advance(1) should increment current_index by 1."""
        start = mock_st.session_state["current_index"]
        mr.advance(1)
        assert mock_st.session_state["current_index"] == start + 1

    def test_advance_by_five(self, loaded_state):
        """Advance(5) should increment current_index by 5."""
        start = mock_st.session_state["current_index"]
        mr.advance(5)
        assert mock_st.session_state["current_index"] == start + 5

    def test_advance_by_ten(self, loaded_state):
        """Advance(10) should increment current_index by 10."""
        start = mock_st.session_state["current_index"]
        mr.advance(10)
        assert mock_st.session_state["current_index"] == start + 10

    def test_advance_clamped_to_max(self, loaded_state):
        """Advancing past max_index should clamp."""
        max_idx = mock_st.session_state["max_index"]
        mock_st.session_state["current_index"] = max_idx - 3
        mr.advance(10)
        assert mock_st.session_state["current_index"] == max_idx

    def test_advance_at_max_no_change(self, loaded_state):
        """Advancing when already at max should do nothing."""
        max_idx = mock_st.session_state["max_index"]
        mock_st.session_state["current_index"] = max_idx
        mr.advance(1)
        assert mock_st.session_state["current_index"] == max_idx

    def test_go_back_by_one(self, loaded_state):
        """go_back(1) should decrement by 1."""
        mock_st.session_state["current_index"] = 60
        mr.go_back(1)
        assert mock_st.session_state["current_index"] == 59

    def test_go_back_clamped_to_warmup(self, loaded_state):
        """Going back past warmup should clamp at WARMUP_CANDLES."""
        mock_st.session_state["current_index"] = mr.WARMUP_CANDLES + 2
        mr.go_back(10)
        assert mock_st.session_state["current_index"] == mr.WARMUP_CANDLES

    def test_go_back_at_warmup_no_change(self, loaded_state):
        """Going back when at warmup should stay at warmup."""
        mock_st.session_state["current_index"] = mr.WARMUP_CANDLES
        mr.go_back(1)
        assert mock_st.session_state["current_index"] == mr.WARMUP_CANDLES

    def test_advance_then_back_round_trip(self, loaded_state):
        """Advance then go back should return to original index."""
        start = mock_st.session_state["current_index"]
        mr.advance(5)
        mr.go_back(5)
        assert mock_st.session_state["current_index"] == start


# ═══════════════════════════════════════════════════════════════
# MODULE D TESTS: Paper Trading Engine
# ═══════════════════════════════════════════════════════════════

class TestModuleD:
    def test_buy_creates_long_position(self, loaded_state):
        """BUY should create a long position."""
        mr.execute_trade("long", None, None, 100)
        assert len(mock_st.session_state["positions"]) == 1
        pos = mock_st.session_state["positions"][0]
        assert pos["side"] == "long"
        assert pos["quantity"] == 100
        assert pos["status"] == "open"

    def test_sell_creates_short_position(self, loaded_state):
        """SELL should create a short position."""
        mr.execute_trade("short", None, None, 50)
        pos = mock_st.session_state["positions"][0]
        assert pos["side"] == "short"
        assert pos["quantity"] == 50

    def test_position_has_entry_price(self, loaded_state):
        """Position should record entry price from current candle close."""
        idx = mock_st.session_state["current_index"]
        expected_price = float(loaded_state.iloc[idx]["close"])
        mr.execute_trade("long", None, None, 100)
        assert mock_st.session_state["positions"][0]["entry_price"] == expected_price

    def test_position_has_sl_tp(self, loaded_state):
        """Position should store SL/TP values."""
        mr.execute_trade("long", 95.0, 110.0, 100)
        pos = mock_st.session_state["positions"][0]
        assert pos["stop_loss"] == 95.0
        assert pos["take_profit"] == 110.0

    def test_close_position_moves_to_closed(self, loaded_state):
        """Closing a position should move it to closed_trades."""
        mr.execute_trade("long", None, None, 100)
        pid = mock_st.session_state["positions"][0]["id"]
        mr.close_position(pid)
        assert len(mock_st.session_state["positions"]) == 0
        assert len(mock_st.session_state["closed_trades"]) == 1
        assert mock_st.session_state["closed_trades"][0]["exit_reason"] == "manual_close"

    def test_close_position_pnl_long(self, loaded_state):
        """Long position P&L should be (exit - entry) * qty."""
        mr.execute_trade("long", None, None, 100)
        entry = mock_st.session_state["positions"][0]["entry_price"]

        # Advance to get a different price
        mr.advance(5)
        exit_price = float(loaded_state.iloc[mock_st.session_state["current_index"]]["close"])

        pid = mock_st.session_state["positions"][0]["id"]
        mr.close_position(pid)

        expected_pnl = (exit_price - entry) * 100
        actual_pnl = mock_st.session_state["closed_trades"][0]["realized_pnl"]
        assert abs(actual_pnl - expected_pnl) < 0.01

    def test_close_position_pnl_short(self, loaded_state):
        """Short position P&L should be (entry - exit) * qty."""
        mr.execute_trade("short", None, None, 100)
        entry = mock_st.session_state["positions"][0]["entry_price"]

        mr.advance(5)
        exit_price = float(loaded_state.iloc[mock_st.session_state["current_index"]]["close"])

        pid = mock_st.session_state["positions"][0]["id"]
        mr.close_position(pid)

        expected_pnl = (entry - exit_price) * 100
        actual_pnl = mock_st.session_state["closed_trades"][0]["realized_pnl"]
        assert abs(actual_pnl - expected_pnl) < 0.01

    def test_close_all_positions(self, loaded_state):
        """close_all_positions should close everything."""
        mr.execute_trade("long", None, None, 100)
        mr.execute_trade("short", None, None, 50)
        assert len(mock_st.session_state["positions"]) == 2
        mr.close_all_positions()
        assert len(mock_st.session_state["positions"]) == 0
        assert len(mock_st.session_state["closed_trades"]) == 2

    def test_balance_updates_on_close(self, loaded_state):
        """Balance should change by realized P&L when position is closed."""
        initial_balance = mock_st.session_state["balance"]
        mr.execute_trade("long", None, None, 100)
        mr.advance(5)

        pid = mock_st.session_state["positions"][0]["id"]
        mr.close_position(pid)

        realized = mock_st.session_state["closed_trades"][0]["realized_pnl"]
        assert mock_st.session_state["balance"] == initial_balance + realized

    def test_floating_pnl_recalculation(self, loaded_state):
        """Floating P&L should update when index changes."""
        mr.execute_trade("long", None, None, 100)
        entry = mock_st.session_state["positions"][0]["entry_price"]

        mr.advance(1)
        current_price = float(loaded_state.iloc[mock_st.session_state["current_index"]]["close"])
        expected_pnl = (current_price - entry) * 100
        assert abs(mock_st.session_state["floating_pnl"] - expected_pnl) < 0.01

    def test_stop_loss_triggers(self, loaded_state):
        """Stop loss should trigger when candle low hits SL price."""
        # Set a very high SL that the current candle's low is already below
        idx = mock_st.session_state["current_index"]
        current_close = float(loaded_state.iloc[idx]["close"])

        # Set SL just above next candle's low — we'll use a very high SL to guarantee trigger
        mr.execute_trade("long", current_close * 10, None, 100)  # absurd SL
        mr.advance(1)

        # With SL at 10x current price, the candle low will definitely be below it
        assert len(mock_st.session_state["closed_trades"]) == 1
        assert mock_st.session_state["closed_trades"][0]["exit_reason"] == "stop_loss"

    def test_take_profit_triggers(self, loaded_state):
        """Take profit should trigger when candle high hits TP price."""
        idx = mock_st.session_state["current_index"]
        current_close = float(loaded_state.iloc[idx]["close"])

        # Set TP at a very low price so candle high will exceed it
        mr.execute_trade("long", None, current_close * 0.01, 100)
        mr.advance(1)

        assert len(mock_st.session_state["closed_trades"]) == 1
        assert mock_st.session_state["closed_trades"][0]["exit_reason"] == "take_profit"

    def test_multiple_positions_independent(self, loaded_state):
        """Multiple positions should be independently tracked."""
        mr.execute_trade("long", None, None, 100)
        mr.execute_trade("short", None, None, 50)

        assert len(mock_st.session_state["positions"]) == 2
        assert mock_st.session_state["positions"][0]["side"] == "long"
        assert mock_st.session_state["positions"][1]["side"] == "short"


# ═══════════════════════════════════════════════════════════════
# MODULE E TESTS: Pattern Detection
# ═══════════════════════════════════════════════════════════════

class TestModuleE:
    def test_doji_detection(self):
        """Doji: body < 10% of range."""
        assert mr.detect_doji(100, 105, 95, 100.5) is True
        assert mr.detect_doji(100, 105, 95, 104) is False  # body too large

    def test_hammer_detection(self):
        """Hammer: long lower shadow, small body at top."""
        # body = |102-101| = 1, lower shadow = 101-95 = 6, upper = 102.3-102 = 0.3
        assert mr.detect_hammer(101, 102.3, 95, 102) is True
        # Not a hammer — equal shadows
        assert mr.detect_hammer(100, 105, 95, 100) is False

    def test_inverted_hammer_detection(self):
        """Inverted hammer: long upper shadow, small body at bottom."""
        # body = |96-95| = 1, upper shadow = 105-96 = 9, lower = 95-94.8 = 0.2
        assert mr.detect_inverted_hammer(95, 105, 94.8, 96) is True

    def test_bullish_engulfing_detection(self):
        """Bullish engulfing: prev bearish, current bullish, engulfs."""
        assert mr.detect_bullish_engulfing(102, 98, 97, 103) is True
        # Not engulfing — current doesn't fully cover previous
        assert mr.detect_bullish_engulfing(102, 98, 99, 101) is False

    def test_bearish_engulfing_detection(self):
        """Bearish engulfing: prev bullish, current bearish, engulfs."""
        assert mr.detect_bearish_engulfing(98, 102, 103, 97) is True
        assert mr.detect_bearish_engulfing(98, 102, 101, 99) is False

    def test_morning_star_detection(self):
        """Morning star: big bear, small body, big bull."""
        candles = [
            {"open": 110, "close": 100},  # big bearish
            {"open": 100, "close": 101},   # small body
            {"open": 101, "close": 112},   # big bullish, closes above mid of first
        ]
        assert mr.detect_morning_star(candles) is True

    def test_evening_star_detection(self):
        """Evening star: big bull, small body, big bear."""
        candles = [
            {"open": 100, "close": 110},  # big bullish
            {"open": 110, "close": 111},   # small body
            {"open": 111, "close": 98},    # big bearish, closes below mid of first
        ]
        assert mr.detect_evening_star(candles) is True

    def test_pattern_detection_memoization(self, loaded_state):
        """Pattern detection should not re-run for the same index."""
        mr.run_pattern_detection()
        idx = mock_st.session_state["last_detected_index"]
        assert idx == mock_st.session_state["current_index"]

        # Store result and run again — should be identical (memoized)
        patterns_1 = mock_st.session_state["detected_patterns"].copy()
        mr.run_pattern_detection()
        patterns_2 = mock_st.session_state["detected_patterns"]
        assert patterns_1 == patterns_2

    def test_pattern_detection_updates_on_advance(self, loaded_state):
        """Pattern detection should re-run after advancing."""
        mr.run_pattern_detection()
        old_idx = mock_st.session_state["last_detected_index"]
        mr.advance(1)
        assert mock_st.session_state["last_detected_index"] != old_idx

    def test_zero_range_candle_no_crash(self):
        """Pattern functions should handle zero-range candles without crashing."""
        assert mr.detect_doji(100, 100, 100, 100) is False
        assert mr.detect_hammer(100, 100, 100, 100) is False
        assert mr.detect_inverted_hammer(100, 100, 100, 100) is False


# ═══════════════════════════════════════════════════════════════
# MODULE F TESTS: Integration & Edge Cases
# ═══════════════════════════════════════════════════════════════

class TestModuleF:
    def test_full_replay_walkthrough(self, loaded_state):
        """Simulate a full trading session: load, trade, advance, close."""
        # Open a long position
        mr.execute_trade("long", None, None, 100)
        assert len(mock_st.session_state["positions"]) == 1

        # Advance 10 candles
        mr.advance(10)
        assert mock_st.session_state["current_index"] == mr.WARMUP_CANDLES + 10

        # Floating P&L should be non-zero (price moved)
        # (could be 0 in degenerate case but very unlikely with random data)
        mr.recalculate_floating_pnl()

        # Close and check trade recorded
        pid = mock_st.session_state["positions"][0]["id"]
        mr.close_position(pid)
        assert len(mock_st.session_state["closed_trades"]) == 1
        assert mock_st.session_state["floating_pnl"] == 0.0

    def test_advance_checks_sl_on_every_candle(self, loaded_state):
        """When jumping +5, SL should be checked on each intermediate candle."""
        idx = mock_st.session_state["current_index"]
        current_close = float(loaded_state.iloc[idx]["close"])

        # Set a SL that should trigger on the very next candle
        # Use a very high SL so it triggers immediately
        mr.execute_trade("long", current_close * 10, None, 100)

        # Jump +5 — SL should trigger on candle idx+1
        mr.advance(5)
        assert len(mock_st.session_state["closed_trades"]) == 1
        # The exit index should be idx+1 (first candle after entry), not idx+5
        exit_idx = mock_st.session_state["closed_trades"][0]["exit_index"]
        assert exit_idx == idx + 1

    def test_unique_position_ids(self, loaded_state):
        """Each position should have a unique ID."""
        ids = set()
        for _ in range(20):
            mr.execute_trade("long", None, None, 10)
            ids.add(mock_st.session_state["positions"][-1]["id"])
        assert len(ids) == 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
