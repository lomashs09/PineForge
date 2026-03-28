"""Broker account service — MetaAPI provisioning and account management."""

import logging

logger = logging.getLogger(__name__)


async def provision_account(
    metaapi_token: str,
    mt5_login: str,
    mt5_password: str,
    mt5_server: str,
    label: str,
) -> str:
    """Provision an MT5 account via MetaAPI. Returns the metaapi_account_id.

    The MT5 password is passed to MetaAPI for encrypted storage and is NOT
    stored in our database.
    """
    from metaapi_cloud_sdk import MetaApi

    api = MetaApi(token=metaapi_token)

    account = await api.metatrader_account_api.create_account({
        "name": label,
        "type": "cloud",
        "login": mt5_login,
        "password": mt5_password,
        "server": mt5_server,
        "platform": "mt5",
        "magic": 0,
    })

    logger.info("Provisioned MetaAPI account: %s for login %s", account.id, mt5_login)
    return account.id


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
    await connection.wait_synchronized(timeout_in_seconds=60)

    info = await connection.get_account_information()
    await connection.close()

    return {
        "balance": info.get("balance", 0),
        "equity": info.get("equity", 0),
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
    await connection.wait_synchronized(timeout_in_seconds=60)

    positions = await connection.get_positions()
    await connection.close()

    return positions or []
