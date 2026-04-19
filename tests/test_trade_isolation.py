"""Tests for MT5 magic number trade isolation.

Covers:
- Executor passes magic number and comment in order options
- get_positions filters by symbol AND magic number
- close_all only closes positions matching this bot's magic
- Two bots on same account/symbol don't interfere
- User's manual trades (magic=0) are never touched
- Dry-run mode bypasses all magic logic
- Error handling: timeouts, failures, partial closes
- _order_options() formatting
- Bot model magic_number generation
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pineforge.live.executor import Executor


# ═══════════════════════════════════════════════════════════════════
# Mock MetaAPI connection
# ═══════════════════════════════════════════════════════════════════


def _make_conn(positions=None, buy_result=None, sell_result=None):
    """Create a mock MetaAPI RPC connection."""
    conn = AsyncMock()

    # Default successful order result
    default_order = {
        "orderId": "12345678",
        "price": 2350.50,
        "openPrice": 2350.50,
    }

    conn.create_market_buy_order = AsyncMock(return_value=buy_result or default_order)
    conn.create_market_sell_order = AsyncMock(return_value=sell_result or default_order)
    conn.get_positions = AsyncMock(return_value=positions or [])
    conn.close_position = AsyncMock(return_value={"status": "ok"})
    conn.close_positions_by_symbol = AsyncMock(return_value={"status": "ok"})
    conn.get_account_information = AsyncMock(return_value={
        "balance": 10000.0, "equity": 10050.0, "currency": "USD",
    })

    return conn


# Sample positions representing a mixed account
BOT_A_MAGIC = 1234567
BOT_B_MAGIC = 7654321
MANUAL_MAGIC = 0

MIXED_POSITIONS = [
    # Bot A's position
    {"id": "pos-1", "symbol": "XAUUSD", "magic": BOT_A_MAGIC, "type": "POSITION_TYPE_BUY",
     "volume": 0.01, "profit": 5.20, "openPrice": 2340.0},
    # Bot B's position (same symbol!)
    {"id": "pos-2", "symbol": "XAUUSD", "magic": BOT_B_MAGIC, "type": "POSITION_TYPE_SELL",
     "volume": 0.02, "profit": -3.10, "openPrice": 2355.0},
    # User's manual trade (no magic / magic=0)
    {"id": "pos-3", "symbol": "XAUUSD", "magic": MANUAL_MAGIC, "type": "POSITION_TYPE_BUY",
     "volume": 0.1, "profit": 15.00, "openPrice": 2330.0},
    # Different symbol entirely
    {"id": "pos-4", "symbol": "EURUSD", "magic": BOT_A_MAGIC, "type": "POSITION_TYPE_BUY",
     "volume": 0.05, "profit": 2.00, "openPrice": 1.0850},
    # Another user's manual trade on different symbol
    {"id": "pos-5", "symbol": "EURUSD", "magic": 0, "type": "POSITION_TYPE_SELL",
     "volume": 0.1, "profit": -1.50, "openPrice": 1.0900},
]


# ═══════════════════════════════════════════════════════════════════
# 1. _order_options() tests
# ═══════════════════════════════════════════════════════════════════


class TestOrderOptions:
    """Tests for magic number and comment in order options."""

    def test_with_magic_number(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        opts = exe._order_options()
        assert opts["magic"] == BOT_A_MAGIC
        assert opts["comment"] == f"pf-{BOT_A_MAGIC}"

    def test_without_magic_number(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=0)
        opts = exe._order_options()
        assert "magic" not in opts
        assert opts["comment"] == "pineforge"

    def test_magic_is_integer(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=999999)
        opts = exe._order_options()
        assert isinstance(opts["magic"], int)

    def test_comment_format(self):
        """Comment should be pf-{magic} for easy identification in MT5."""
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=42)
        opts = exe._order_options()
        assert opts["comment"] == "pf-42"


# ═══════════════════════════════════════════════════════════════════
# 2. open_buy / open_sell — magic passed to MetaAPI
# ═══════════════════════════════════════════════════════════════════


class TestOpenOrders:
    """Tests for order placement with magic numbers."""

    @pytest.mark.asyncio
    async def test_buy_passes_magic_in_options(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        await exe.open_buy(0.01)

        conn.create_market_buy_order.assert_called_once()
        args, kwargs = conn.create_market_buy_order.call_args
        assert args == ("XAUUSD", 0.01)
        assert kwargs["options"]["magic"] == BOT_A_MAGIC
        assert kwargs["options"]["comment"] == f"pf-{BOT_A_MAGIC}"

    @pytest.mark.asyncio
    async def test_sell_passes_magic_in_options(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_B_MAGIC)
        await exe.open_sell(0.02)

        conn.create_market_sell_order.assert_called_once()
        args, kwargs = conn.create_market_sell_order.call_args
        assert args == ("XAUUSD", 0.02)
        assert kwargs["options"]["magic"] == BOT_B_MAGIC

    @pytest.mark.asyncio
    async def test_buy_no_magic_passes_pineforge_comment(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=0)
        await exe.open_buy(0.01)

        _, kwargs = conn.create_market_buy_order.call_args
        assert "magic" not in kwargs["options"]
        assert kwargs["options"]["comment"] == "pineforge"

    @pytest.mark.asyncio
    async def test_sell_no_magic_passes_pineforge_comment(self):
        conn = _make_conn()
        exe = Executor(conn, "EURUSD", is_live=True, magic=0)
        await exe.open_sell(0.05)

        _, kwargs = conn.create_market_sell_order.call_args
        assert kwargs["options"]["comment"] == "pineforge"

    @pytest.mark.asyncio
    async def test_buy_returns_result(self):
        result = {"orderId": "99", "price": 2350.0, "openPrice": 2350.0}
        conn = _make_conn(buy_result=result)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        ret = await exe.open_buy(0.01)
        assert ret == result

    @pytest.mark.asyncio
    async def test_sell_returns_result(self):
        result = {"orderId": "100", "price": 1.0855, "openPrice": 1.0855}
        conn = _make_conn(sell_result=result)
        exe = Executor(conn, "EURUSD", is_live=True, magic=BOT_A_MAGIC)
        ret = await exe.open_sell(0.05)
        assert ret == result

    @pytest.mark.asyncio
    async def test_buy_dry_run_skips_metaapi(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=False, magic=BOT_A_MAGIC)
        result = await exe.open_buy(0.01)

        assert result["dry_run"] is True
        conn.create_market_buy_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_dry_run_skips_metaapi(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=False, magic=BOT_A_MAGIC)
        result = await exe.open_sell(0.01)

        assert result["dry_run"] is True
        conn.create_market_sell_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_buy_timeout_returns_none(self):
        conn = _make_conn()
        conn.create_market_buy_order = AsyncMock(side_effect=asyncio.TimeoutError)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.open_buy(0.01)
        assert result is None

    @pytest.mark.asyncio
    async def test_sell_exception_returns_none(self):
        conn = _make_conn()
        conn.create_market_sell_order = AsyncMock(side_effect=Exception("Network error"))
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.open_sell(0.01)
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# 3. get_positions — filtered by symbol + magic
# ═══════════════════════════════════════════════════════════════════


class TestGetPositions:
    """Tests for position filtering by symbol and magic number."""

    @pytest.mark.asyncio
    async def test_filters_by_symbol_and_magic(self):
        """Bot A on XAUUSD should only see its own position."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()

        assert len(positions) == 1
        assert positions[0]["id"] == "pos-1"
        assert positions[0]["magic"] == BOT_A_MAGIC

    @pytest.mark.asyncio
    async def test_bot_b_sees_only_its_position(self):
        """Bot B on XAUUSD should only see its own position."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_B_MAGIC)
        positions = await exe.get_positions()

        assert len(positions) == 1
        assert positions[0]["id"] == "pos-2"
        assert positions[0]["magic"] == BOT_B_MAGIC

    @pytest.mark.asyncio
    async def test_manual_trades_invisible_to_bot(self):
        """Bot A should NOT see the user's manual trade (magic=0)."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()

        position_ids = [p["id"] for p in positions]
        assert "pos-3" not in position_ids  # manual trade

    @pytest.mark.asyncio
    async def test_different_symbol_filtered_out(self):
        """Bot A on XAUUSD should NOT see its EURUSD position."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()

        position_ids = [p["id"] for p in positions]
        assert "pos-4" not in position_ids  # EURUSD, same magic but wrong symbol

    @pytest.mark.asyncio
    async def test_bot_a_on_eurusd(self):
        """Bot A on EURUSD should see only its EURUSD position."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "EURUSD", is_live=True, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()

        assert len(positions) == 1
        assert positions[0]["id"] == "pos-4"

    @pytest.mark.asyncio
    async def test_no_magic_sees_all_symbol_positions(self):
        """Executor with magic=0 should see all positions for its symbol (backwards compat)."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=0)
        positions = await exe.get_positions()

        # magic=0 means no filtering by magic — sees all XAUUSD positions
        assert len(positions) == 3  # pos-1, pos-2, pos-3
        ids = {p["id"] for p in positions}
        assert ids == {"pos-1", "pos-2", "pos-3"}

    @pytest.mark.asyncio
    async def test_empty_positions(self):
        conn = _make_conn(positions=[])
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_none_positions(self):
        conn = _make_conn(positions=None)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_dry_run_returns_empty(self):
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=False, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()
        assert positions == []
        conn.get_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        conn = _make_conn()
        conn.get_positions = AsyncMock(side_effect=asyncio.TimeoutError)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        conn = _make_conn()
        conn.get_positions = AsyncMock(side_effect=Exception("API error"))
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        positions = await exe.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_position_without_magic_field(self):
        """Positions missing the 'magic' key should be excluded when bot has magic."""
        positions = [
            {"id": "pos-no-magic", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY",
             "volume": 0.01, "profit": 1.0},
        ]
        conn = _make_conn(positions=positions)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.get_positions()

        # p.get("magic") returns None, which != BOT_A_MAGIC → filtered out
        assert len(result) == 0


# ═══════════════════════════════════════════════════════════════════
# 4. close_all — only closes this bot's positions
# ═══════════════════════════════════════════════════════════════════


class TestCloseAll:
    """Tests for close_all with magic number isolation."""

    @pytest.mark.asyncio
    async def test_closes_only_matching_positions(self):
        """Bot A close_all should only close pos-1, not pos-2 or pos-3."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.close_all()

        assert result is True
        # Should close only pos-1 (Bot A's XAUUSD position)
        conn.close_position.assert_called_once_with("pos-1")
        # Should NOT use close_positions_by_symbol (that closes everything)
        conn.close_positions_by_symbol.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_b_closes_only_its_position(self):
        """Bot B close_all should only close pos-2."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_B_MAGIC)
        result = await exe.close_all()

        assert result is True
        conn.close_position.assert_called_once_with("pos-2")

    @pytest.mark.asyncio
    async def test_manual_trades_untouched(self):
        """No bot should ever close the user's manual trade (pos-3)."""
        conn = _make_conn(positions=MIXED_POSITIONS)

        # Bot A closes
        exe_a = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        await exe_a.close_all()
        closed_ids_a = [call.args[0] for call in conn.close_position.call_args_list]
        assert "pos-3" not in closed_ids_a

        conn.close_position.reset_mock()

        # Bot B closes
        exe_b = Executor(conn, "XAUUSD", is_live=True, magic=BOT_B_MAGIC)
        await exe_b.close_all()
        closed_ids_b = [call.args[0] for call in conn.close_position.call_args_list]
        assert "pos-3" not in closed_ids_b

    @pytest.mark.asyncio
    async def test_no_positions_to_close(self):
        """Should return True and not call close_position when no matching positions."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        # Use a magic that has no positions
        exe = Executor(conn, "XAUUSD", is_live=True, magic=9999999)
        result = await exe.close_all()

        assert result is True
        conn.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_closes_multiple_positions(self):
        """Should close all positions matching magic, not just the first."""
        positions = [
            {"id": "pos-a1", "symbol": "XAUUSD", "magic": BOT_A_MAGIC,
             "type": "POSITION_TYPE_BUY", "volume": 0.01, "profit": 2.0},
            {"id": "pos-a2", "symbol": "XAUUSD", "magic": BOT_A_MAGIC,
             "type": "POSITION_TYPE_BUY", "volume": 0.02, "profit": 3.0},
            {"id": "pos-other", "symbol": "XAUUSD", "magic": BOT_B_MAGIC,
             "type": "POSITION_TYPE_SELL", "volume": 0.01, "profit": -1.0},
        ]
        conn = _make_conn(positions=positions)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.close_all()

        assert result is True
        assert conn.close_position.call_count == 2
        closed_ids = {call.args[0] for call in conn.close_position.call_args_list}
        assert closed_ids == {"pos-a1", "pos-a2"}

    @pytest.mark.asyncio
    async def test_partial_close_failure(self):
        """If one position fails to close, should return False."""
        positions = [
            {"id": "pos-ok", "symbol": "XAUUSD", "magic": BOT_A_MAGIC,
             "type": "POSITION_TYPE_BUY", "volume": 0.01, "profit": 1.0},
            {"id": "pos-fail", "symbol": "XAUUSD", "magic": BOT_A_MAGIC,
             "type": "POSITION_TYPE_BUY", "volume": 0.01, "profit": 2.0},
        ]
        conn = _make_conn(positions=positions)

        # First close succeeds, second fails
        call_count = [0]
        async def mock_close(pos_id):
            call_count[0] += 1
            if pos_id == "pos-fail":
                raise Exception("Close rejected")
            return {"status": "ok"}
        conn.close_position = AsyncMock(side_effect=mock_close)

        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.close_all()

        assert result is False  # partial failure
        assert conn.close_position.call_count == 2

    @pytest.mark.asyncio
    async def test_dry_run_close_all(self):
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe = Executor(conn, "XAUUSD", is_live=False, magic=BOT_A_MAGIC)
        result = await exe.close_all()

        assert result is True
        conn.close_position.assert_not_called()
        conn.get_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_pnl_calculated_from_matched_positions(self):
        """PnL in log output should only sum matching positions."""
        positions = [
            {"id": "pos-1", "symbol": "XAUUSD", "magic": BOT_A_MAGIC,
             "type": "POSITION_TYPE_BUY", "volume": 0.01, "profit": 5.20},
            {"id": "pos-2", "symbol": "XAUUSD", "magic": BOT_B_MAGIC,
             "type": "POSITION_TYPE_SELL", "volume": 0.01, "profit": -10.0},
        ]
        conn = _make_conn(positions=positions)
        printed = []
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        exe._print_fn = lambda *args: printed.append(" ".join(str(a) for a in args))

        await exe.close_all()

        # PnL should be 5.20, not 5.20 + (-10.0)
        assert any("pnl=5.20" in msg for msg in printed)


# ═══════════════════════════════════════════════════════════════════
# 5. Two bots same account — full isolation scenario
# ═══════════════════════════════════════════════════════════════════


class TestTwoBotsSameAccount:
    """Integration-style tests: two bots trading same symbol on same account."""

    @pytest.mark.asyncio
    async def test_both_bots_open_trades_independently(self):
        """Both bots should be able to open trades with their own magic."""
        conn = _make_conn()
        exe_a = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        exe_b = Executor(conn, "XAUUSD", is_live=True, magic=BOT_B_MAGIC)

        await exe_a.open_buy(0.01)
        await exe_b.open_sell(0.02)

        # Both called, each with own magic
        buy_opts = conn.create_market_buy_order.call_args[1]["options"]
        sell_opts = conn.create_market_sell_order.call_args[1]["options"]
        assert buy_opts["magic"] == BOT_A_MAGIC
        assert sell_opts["magic"] == BOT_B_MAGIC

    @pytest.mark.asyncio
    async def test_bot_a_close_doesnt_touch_bot_b(self):
        """When Bot A closes, Bot B's positions remain untouched."""
        conn = _make_conn(positions=MIXED_POSITIONS)
        exe_a = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)

        await exe_a.close_all()

        closed = {call.args[0] for call in conn.close_position.call_args_list}
        assert "pos-1" in closed      # Bot A's position
        assert "pos-2" not in closed   # Bot B's position
        assert "pos-3" not in closed   # Manual trade

    @pytest.mark.asyncio
    async def test_each_bot_sees_own_position_count(self):
        """Bot A sees 1 position, Bot B sees 1 position, neither sees the other's."""
        conn = _make_conn(positions=MIXED_POSITIONS)

        exe_a = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        exe_b = Executor(conn, "XAUUSD", is_live=True, magic=BOT_B_MAGIC)

        pos_a = await exe_a.get_positions()
        pos_b = await exe_b.get_positions()

        assert len(pos_a) == 1
        assert len(pos_b) == 1
        assert pos_a[0]["id"] != pos_b[0]["id"]

    @pytest.mark.asyncio
    async def test_bot_a_has_position_bot_b_does_not(self):
        """If only Bot A has a position, Bot B should see none."""
        positions = [
            {"id": "pos-a", "symbol": "XAUUSD", "magic": BOT_A_MAGIC,
             "type": "POSITION_TYPE_BUY", "volume": 0.01, "profit": 1.0},
        ]
        conn = _make_conn(positions=positions)

        exe_b = Executor(conn, "XAUUSD", is_live=True, magic=BOT_B_MAGIC)
        pos_b = await exe_b.get_positions()
        assert len(pos_b) == 0


# ═══════════════════════════════════════════════════════════════════
# 6. Bot model magic_number
# ═══════════════════════════════════════════════════════════════════


class TestBotMagicNumber:
    """Tests for magic_number on the Bot model."""

    def test_bot_model_has_magic_number_field(self):
        from api.models.bot import Bot
        assert hasattr(Bot, "magic_number")

    def test_magic_number_in_valid_range(self):
        """Generated magic numbers should be positive 32-bit integers."""
        import random
        for _ in range(100):
            magic = random.randint(100_000, 2_147_483_647)
            assert 100_000 <= magic <= 2_147_483_647

    def test_live_config_has_magic_number(self):
        from pineforge.live.config import LiveConfig
        cfg = LiveConfig(magic_number=BOT_A_MAGIC)
        assert cfg.magic_number == BOT_A_MAGIC

    def test_live_config_default_magic_is_zero(self):
        from pineforge.live.config import LiveConfig
        cfg = LiveConfig()
        assert cfg.magic_number == 0


# ═══════════════════════════════════════════════════════════════════
# 7. close_position (single) tests
# ═══════════════════════════════════════════════════════════════════


class TestClosePosition:
    """Tests for closing a single position by ID."""

    @pytest.mark.asyncio
    async def test_close_specific_position(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.close_position("pos-123")

        assert result is True
        conn.close_position.assert_called_once_with("pos-123")

    @pytest.mark.asyncio
    async def test_close_position_dry_run(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=False, magic=BOT_A_MAGIC)
        result = await exe.close_position("pos-123")

        assert result is True
        conn.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_position_timeout(self):
        conn = _make_conn()
        conn.close_position = AsyncMock(side_effect=asyncio.TimeoutError)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.close_position("pos-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_close_position_exception(self):
        conn = _make_conn()
        conn.close_position = AsyncMock(side_effect=Exception("Rejected"))
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        result = await exe.close_position("pos-123")

        assert result is False


# ═══════════════════════════════════════════════════════════════════
# 8. get_account_info tests
# ═══════════════════════════════════════════════════════════════════


class TestGetAccountInfo:
    """Tests for account info retrieval."""

    @pytest.mark.asyncio
    async def test_live_returns_account_info(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        info = await exe.get_account_info()

        assert info["balance"] == 10000.0
        assert info["equity"] == 10050.0

    @pytest.mark.asyncio
    async def test_dry_run_returns_mock_info(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=False, magic=BOT_A_MAGIC)
        info = await exe.get_account_info()

        assert info["dry_run"] is True
        conn.get_account_information.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        conn = _make_conn()
        conn.get_account_information = AsyncMock(side_effect=asyncio.TimeoutError)
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        info = await exe.get_account_info()
        assert info is None


# ═══════════════════════════════════════════════════════════════════
# 9. Print output isolation
# ═══════════════════════════════════════════════════════════════════


class TestPrintOutput:
    """Tests for per-bot print function isolation."""

    @pytest.mark.asyncio
    async def test_uses_custom_print_fn(self):
        conn = _make_conn()
        exe = Executor(conn, "XAUUSD", is_live=False, magic=BOT_A_MAGIC)
        messages = []
        exe._print_fn = lambda *args: messages.append(" ".join(str(a) for a in args))

        await exe.open_buy(0.01)
        assert len(messages) == 1
        assert "DRY RUN" in messages[0]
        assert "BUY" in messages[0]

    @pytest.mark.asyncio
    async def test_close_all_empty_prints_magic(self):
        """When no positions match, should mention magic in output."""
        conn = _make_conn(positions=[])
        exe = Executor(conn, "XAUUSD", is_live=True, magic=BOT_A_MAGIC)
        messages = []
        exe._print_fn = lambda *args: messages.append(" ".join(str(a) for a in args))

        await exe.close_all()
        assert any(str(BOT_A_MAGIC) in msg for msg in messages)
