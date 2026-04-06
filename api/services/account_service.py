"""Broker account service — MetaAPI provisioning and account management."""

import asyncio
import logging

logger = logging.getLogger(__name__)


class ProvisioningError(Exception):
    """Raised when MT5 account provisioning or verification fails."""
    pass


async def provision_account(
    metaapi_token: str,
    mt5_login: str,
    mt5_password: str,
    mt5_server: str,
    label: str,
) -> str:
    """Provision and verify an MT5 account via MetaAPI.

    Steps:
    1. Create the account on MetaAPI
    2. Deploy it (starts the cloud MT5 terminal)
    3. Wait for it to connect to the broker
    4. Verify credentials by fetching account info
    5. If verification fails, remove the account from MetaAPI

    Returns the metaapi_account_id on success.
    Raises ProvisioningError with a user-friendly message on failure.
    """
    from metaapi_cloud_sdk import MetaApi

    api = MetaApi(token=metaapi_token)
    account = None

    try:
        # Step 1: Create
        account = await api.metatrader_account_api.create_account({
            "name": label,
            "type": "cloud",
            "login": mt5_login,
            "password": mt5_password,
            "server": mt5_server,
            "platform": "mt5",
            "magic": 0,
        })
        logger.info("Created MetaAPI account %s for login %s", account.id, mt5_login)

        # Step 2: Deploy
        if account.state not in ("DEPLOYING", "DEPLOYED"):
            await account.deploy()

        # Step 3: Wait for connection to broker (timeout = 90s)
        try:
            await account.wait_connected(timeout_in_seconds=90)
        except Exception:
            raise ProvisioningError(
                f"Could not connect to broker server '{mt5_server}'. "
                "Please check the server name is correct."
            )

        # Step 4: Verify credentials by opening RPC connection and fetching info
        connection = account.get_rpc_connection()
        await connection.connect()
        try:
            await connection.wait_synchronized(timeout_in_seconds=60)
            info = await connection.get_account_information()
        except Exception:
            raise ProvisioningError(
                "Connected to the server but authentication failed. "
                "Please check your MT5 login number and password are correct."
            )
        finally:
            try:
                await connection.close()
            except Exception:
                pass

        # Verification passed
        login_from_broker = info.get("login", mt5_login)
        balance = info.get("balance", 0)
        currency = info.get("currency", "USD")
        logger.info(
            "Verified account %s: login=%s balance=%s %s",
            account.id, login_from_broker, balance, currency,
        )

        return account.id

    except ProvisioningError:
        # Clean up the provisioned account on MetaAPI
        if account:
            try:
                await account.remove()
                logger.info("Cleaned up failed account %s", account.id)
            except Exception as cleanup_err:
                logger.warning("Failed to clean up account %s: %s", account.id, cleanup_err)
        raise

    except Exception as e:
        # Unexpected error — clean up and raise with friendly message
        if account:
            try:
                await account.remove()
            except Exception:
                pass
        raise ProvisioningError(
            f"Failed to provision account: {str(e)}. "
            "Please verify your MT5 login, password, and server name."
        )


async def get_account_info(metaapi_token: str, metaapi_account_id: str) -> dict:
    """Get live account balance/equity from MetaAPI."""
    from metaapi_cloud_sdk import MetaApi

    api = MetaApi(token=metaapi_token)
    account = await api.metatrader_account_api.get_account(metaapi_account_id)

    if account.state not in ("DEPLOYING", "DEPLOYED"):
        await account.deploy()

    await account.wait_connected(timeout_in_seconds=60)
    connection = account.get_rpc_connection()
    await connection.connect()

    try:
        await connection.wait_synchronized(timeout_in_seconds=60)
        info = await connection.get_account_information()
    finally:
        try:
            await connection.close()
        except Exception:
            pass

    return {
        "balance": info.get("balance", 0),
        "equity": info.get("equity", 0),
        "margin": info.get("margin", 0),
        "freeMargin": info.get("freeMargin", 0),
        "marginLevel": info.get("marginLevel", 0),
        "currency": info.get("currency", "USD"),
    }


async def get_account_positions(metaapi_token: str, metaapi_account_id: str) -> list:
    """Get open positions from MetaAPI."""
    from metaapi_cloud_sdk import MetaApi

    api = MetaApi(token=metaapi_token)
    account = await api.metatrader_account_api.get_account(metaapi_account_id)

    if account.state not in ("DEPLOYING", "DEPLOYED"):
        await account.deploy()

    await account.wait_connected(timeout_in_seconds=60)
    connection = account.get_rpc_connection()
    await connection.connect()

    try:
        await connection.wait_synchronized(timeout_in_seconds=60)
        positions = await connection.get_positions()
    finally:
        try:
            await connection.close()
        except Exception:
            pass

    return positions or []


async def get_history_deals(
    metaapi_token: str,
    metaapi_account_id: str,
    start_time,
    end_time,
    symbol: str = None,
) -> list:
    """Get closed trade history (deals) from MetaAPI.

    Returns list of deals with type, symbol, volume, price, profit, etc.
    Entry deals have profit=0, exit deals have the actual P&L.
    """
    from metaapi_cloud_sdk import MetaApi

    api = MetaApi(token=metaapi_token)
    account = await api.metatrader_account_api.get_account(metaapi_account_id)

    if account.state not in ("DEPLOYING", "DEPLOYED"):
        await account.deploy()

    await account.wait_connected(timeout_in_seconds=60)
    connection = account.get_rpc_connection()
    await connection.connect()

    try:
        await connection.wait_synchronized(timeout_in_seconds=60)
        deals = await connection.get_deals_by_time_range(start_time, end_time)
    finally:
        try:
            await connection.close()
        except Exception:
            pass

    if not deals:
        return []

    # Filter by symbol if specified, and only real trades (not balance/commission)
    trade_types = {"DEAL_TYPE_BUY", "DEAL_TYPE_SELL"}
    result = []
    for d in deals:
        if d.get("type") not in trade_types:
            continue
        if symbol and d.get("symbol") != symbol:
            continue
        result.append(d)

    return result
