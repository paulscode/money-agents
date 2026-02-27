"""
LND Lightning Node Service

Communicates with an LND node via its REST API to provide:
- Wallet balance (on-chain + lightning)
- Channel information
- Node status
- Recent transactions and payments

Authentication: hex-encoded macaroon in Grpc-Metadata-macaroon header
TLS: self-signed cert support (verification disabled by default)
Tor: .onion addresses routed via SOCKS5 proxy (tor-proxy container)
"""

import asyncio
import logging
import ssl
import base64
import tempfile
import os
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _is_onion_url(url: str) -> bool:
    """Check if the URL is a Tor .onion address."""
    try:
        parsed = urlparse(url)
        return parsed.hostname.endswith(".onion") if parsed.hostname else False
    except Exception:
        return False


def _sanitize_lnd_error(error_str: str) -> str:
    """Remove any macaroon hex values that may appear in error context.

    httpx exceptions can include request headers in their string
    representation.  Strip the macaroon so it never reaches logs or
    agent-facing error messages.
    """
    mac = settings.lnd_macaroon_hex.get_secret_value()
    if mac and mac in error_str:
        error_str = error_str.replace(mac, "[REDACTED]")
    return error_str


# ──────────────────────────────────────────────────────────────────
# Least-privilege macaroon scopes
# ──────────────────────────────────────────────────────────────────

_SCOPE_READONLY = "readonly"
_SCOPE_WRITE = "write"

# Permissions for read-only operations (balance, info, history)
_READONLY_PERMISSIONS = [
    {"entity": "info", "action": "read"},
    {"entity": "onchain", "action": "read"},
    {"entity": "offchain", "action": "read"},
    {"entity": "invoices", "action": "read"},
]

# Permissions for payment/write operations — deliberately excludes
# macaroon:generate, info:write, signer:*, and message:* so a
# compromised write macaroon cannot escalate privileges or export
# the wallet seed.
_WRITE_PERMISSIONS = [
    {"entity": "offchain", "action": "write"},
    {"entity": "onchain", "action": "write"},
    {"entity": "invoices", "action": "write"},
    {"entity": "invoices", "action": "read"},
    {"entity": "address", "action": "write"},
    {"entity": "address", "action": "read"},
    {"entity": "peers", "action": "write"},
    {"entity": "peers", "action": "read"},
]


class LNDService:
    """Service for communicating with LND REST API."""
    
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()  # Prevent race on lazy init (GAP: LOW-2)
        # Baked scoped macaroons (populated on first request)
        self._readonly_macaroon_hex: Optional[str] = None
        self._write_macaroon_hex: Optional[str] = None
        self._bake_lock = asyncio.Lock()
        self._bake_attempted: bool = False
    
    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Build SSL context for TLS certificate verification."""
        if settings.lnd_tls_cert:
            # Decode base64 cert and write to temp file for SSL context
            try:
                cert_bytes = base64.b64decode(settings.lnd_tls_cert)
                ctx = ssl.create_default_context()
                # Write cert to unique temp file (avoid predictable path / symlink attack)
                fd, cert_path = tempfile.mkstemp(suffix=".cert", prefix="lnd_tls_")
                try:
                    os.write(fd, cert_bytes)
                finally:
                    os.close(fd)
                ctx.load_verify_locations(cert_path)
                # Clean up temp file after loading
                try:
                    os.unlink(cert_path)
                except OSError:
                    pass
                return ctx
            except Exception as e:
                logger.warning(f"Failed to load LND TLS cert: {e}")
                return None
        return None
    
    def _get_tor_proxy(self) -> Optional[str]:
        """Get SOCKS5 proxy URL for Tor routing.
        
        Returns the proxy URL if the LND REST URL is a .onion address,
        None otherwise. Uses socksio package (httpx auto-detects).
        """
        if _is_onion_url(settings.lnd_rest_url):
            proxy = settings.lnd_tor_proxy
            if proxy:
                logger.info(f"LND .onion address detected — routing via Tor proxy: {proxy}")
                return proxy
            else:
                logger.warning(
                    "LND REST URL is a .onion address but LND_TOR_PROXY is not set. "
                    "Connections will fail. Set LND_TOR_PROXY=socks5://tor-proxy:9050"
                )
        return None

    async def _bake_scoped_macaroons(self) -> None:
        """Bake least-privilege macaroons from the admin macaroon.

        Uses LND's BakeMacaroon RPC (``POST /v1/macaroon``) to create
        a read-only macaroon and a payment-scoped macaroon.  The admin
        macaroon is used only for this one-time bake; all subsequent
        requests use the scoped macaroons.

        If baking fails (older LND, network error, etc.), the service
        falls back to the admin macaroon for all requests — identical
        to the previous behaviour.
        """
        async with self._bake_lock:
            if self._bake_attempted:
                return
            self._bake_attempted = True

            admin_mac = settings.lnd_macaroon_hex.get_secret_value()
            if not admin_mac:
                return

            client = await self._get_client()
            admin_headers = {"Grpc-Metadata-macaroon": admin_mac}

            try:
                # Bake read-only macaroon
                resp = await client.request(
                    "POST", "/v1/macaroon",
                    headers=admin_headers,
                    json={"permissions": _READONLY_PERMISSIONS},
                )
                resp.raise_for_status()
                ro_mac = resp.json().get("macaroon", "")

                # Bake payment/write macaroon
                resp = await client.request(
                    "POST", "/v1/macaroon",
                    headers=admin_headers,
                    json={"permissions": _WRITE_PERMISSIONS},
                )
                resp.raise_for_status()
                wr_mac = resp.json().get("macaroon", "")

                if ro_mac and wr_mac:
                    self._readonly_macaroon_hex = ro_mac
                    self._write_macaroon_hex = wr_mac
                    logger.info(
                        "Baked scoped LND macaroons — readonly (%d chars), "
                        "write (%d chars).  Admin macaroon will not be used "
                        "for runtime requests.",
                        len(ro_mac), len(wr_mac),
                    )
                else:
                    logger.warning(
                        "LND BakeMacaroon returned empty — "
                        "falling back to admin macaroon"
                    )
            except Exception as e:
                safe = _sanitize_lnd_error(str(e))
                logger.warning(
                    "Failed to bake scoped macaroons — falling back to "
                    "admin macaroon: %s", safe
                )

    def _sanitize_error(self, error_str: str) -> str:
        """Strip all known macaroon values from an error string.

        Extends the module-level ``_sanitize_lnd_error`` to also cover
        the baked scoped macaroons held on this instance.
        """
        result = _sanitize_lnd_error(error_str)
        for mac in (self._readonly_macaroon_hex, self._write_macaroon_hex):
            if mac and mac in result:
                result = result.replace(mac, "[REDACTED]")
        return result

    def _get_auth_headers(self, scope: str = _SCOPE_READONLY) -> dict:
        """Build authentication headers with the appropriate scoped macaroon.

        After ``_bake_scoped_macaroons`` succeeds, this returns the
        least-privilege macaroon for the requested scope.  If baking
        has not occurred or failed, falls back to the admin macaroon.
        """
        headers: dict = {}

        if scope == _SCOPE_READONLY and self._readonly_macaroon_hex:
            headers["Grpc-Metadata-macaroon"] = self._readonly_macaroon_hex
        elif scope == _SCOPE_WRITE and self._write_macaroon_hex:
            headers["Grpc-Metadata-macaroon"] = self._write_macaroon_hex
        else:
            # Fallback: admin macaroon (pre-bake or bake failure)
            mac = settings.lnd_macaroon_hex.get_secret_value()
            if mac:
                headers["Grpc-Metadata-macaroon"] = mac

        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.

        NOTE: The client is created WITHOUT authentication headers.
        Auth headers are injected per-request in ``_request`` /
        ``_request_with_error`` so the long-lived client object
        never holds the macaroon.

        Uses ``_client_lock`` to prevent duplicate clients when
        multiple coroutines race during lazy init (GAP: LOW-2).
        """
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            # Double-check after acquiring lock
            if self._client is not None and not self._client.is_closed:
                return self._client

            is_onion = _is_onion_url(settings.lnd_rest_url)
            
            if is_onion:
                # .onion: LND serves HTTPS with a self-signed cert whose
                # CN/SAN won't match the .onion hostname.  If the user
                # provided a TLS cert (e.g. via lndconnect URI), verify
                # the cert identity but skip hostname checking.
                if settings.lnd_tls_cert:
                    ssl_ctx = self._get_ssl_context()
                    if ssl_ctx:
                        ssl_ctx.check_hostname = False
                        verify = ssl_ctx
                        logger.info(
                            "LND .onion: TLS cert verification enabled "
                            "(hostname check disabled)"
                        )
                    else:
                        verify = False
                        logger.warning(
                            "LND .onion: TLS cert provided but failed to "
                            "load — verification disabled"
                        )
                else:
                    verify = False
                    logger.warning(
                        "LND .onion: No TLS cert configured — verification "
                        "disabled. Provide LND_TLS_CERT for defense-in-depth "
                        "TLS verification over Tor."
                    )
            else:
                verify: bool | ssl.SSLContext = settings.lnd_tls_verify
                if settings.lnd_tls_cert and not settings.lnd_tls_verify:
                    # If cert is provided but verify is False, use the cert context anyway
                    ssl_ctx = self._get_ssl_context()
                    if ssl_ctx:
                        verify = ssl_ctx
            
            # SOCKS5 proxy for Tor .onion addresses
            proxy = self._get_tor_proxy()
            
            self._client = httpx.AsyncClient(
                base_url=settings.lnd_rest_url.rstrip("/"),
                # No headers= here — auth injected per-request
                verify=verify,
                proxy=proxy,
                timeout=httpx.Timeout(30.0, connect=20.0) if is_onion else httpx.Timeout(15.0, connect=10.0),
            )
            return self._client
    
    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def _request(self, method: str, path: str, **kwargs) -> Optional[dict]:
        """Make an authenticated request to LND REST API.
        
        Returns parsed JSON on success, None on connection/HTTP errors.
        For write operations that need error details, use _request_with_error() instead.
        
        Pass ``_scope=_SCOPE_WRITE`` for write operations to use the
        payment-scoped macaroon instead of the read-only one.
        """
        scope = kwargs.pop("_scope", _SCOPE_READONLY)
        try:
            if not self._bake_attempted:
                await self._bake_scoped_macaroons()
            client = await self._get_client()
            # Inject scoped auth headers per-request (not stored on client)
            headers = {**kwargs.pop("headers", {}), **self._get_auth_headers(scope)}
            response = await client.request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"LND API error {e.response.status_code}: {e.response.text}")
            return None
        except httpx.ConnectError as e:
            logger.error("LND connection error: %s", self._sanitize_error(str(e)))
            return None
        except Exception as e:
            logger.error("LND request failed: %s", self._sanitize_error(str(e)))
            return None
    
    async def _request_with_error(self, method: str, path: str, **kwargs) -> tuple[Optional[dict], Optional[str]]:
        """Make an authenticated request, returning (data, error).
        
        Unlike _request(), this returns error details so callers can
        distinguish between connection failures and LND-level errors
        (e.g., insufficient balance, invalid payment request).
        
        Error strings are sanitized to ensure the macaroon never
        appears in return values (which may reach agent LLM context).
        
        Pass ``_scope=_SCOPE_WRITE`` for write operations.
        """
        scope = kwargs.pop("_scope", _SCOPE_READONLY)
        try:
            if not self._bake_attempted:
                await self._bake_scoped_macaroons()
            client = await self._get_client()
            # Inject scoped auth headers per-request (not stored on client)
            headers = {**kwargs.pop("headers", {}), **self._get_auth_headers(scope)}
            response = await client.request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json(), None
        except httpx.HTTPStatusError as e:
            error_text = e.response.text
            try:
                error_json = e.response.json()
                error_text = error_json.get("message", error_json.get("error", error_text))
            except Exception:
                pass
            safe_text = self._sanitize_error(error_text)
            logger.error(f"LND API error {e.response.status_code}: {safe_text}")
            return None, f"LND error ({e.response.status_code}): {safe_text}"
        except httpx.ConnectError as e:
            safe = self._sanitize_error(str(e))
            logger.error("LND connection error: %s", safe)
            return None, f"Connection failed: {safe}"
        except Exception as e:
            safe = self._sanitize_error(str(e))
            logger.error("LND request failed: %s", safe)
            return None, f"Request failed: {safe}"
    
    async def get_info(self) -> Optional[dict]:
        """Get node info (alias, pubkey, synced status, etc).
        
        LND endpoint: GET /v1/getinfo
        """
        data = await self._request("GET", "/v1/getinfo")
        if not data:
            return None
        
        return {
            "alias": data.get("alias", ""),
            "identity_pubkey": data.get("identity_pubkey", ""),
            "num_active_channels": data.get("num_active_channels", 0),
            "num_inactive_channels": data.get("num_inactive_channels", 0),
            "num_pending_channels": data.get("num_pending_channels", 0),
            "num_peers": data.get("num_peers", 0),
            "block_height": data.get("block_height", 0),
            "synced_to_chain": data.get("synced_to_chain", False),
            "synced_to_graph": data.get("synced_to_graph", False),
            "version": data.get("version", ""),
            "commit_hash": data.get("commit_hash", ""),
            "uris": data.get("uris", []),
        }
    
    async def get_wallet_balance(self) -> Optional[dict]:
        """Get on-chain wallet balance.
        
        LND endpoint: GET /v1/balance/blockchain
        """
        data = await self._request("GET", "/v1/balance/blockchain")
        if not data:
            return None
        
        return {
            "total_balance": int(data.get("total_balance", 0)),
            "confirmed_balance": int(data.get("confirmed_balance", 0)),
            "unconfirmed_balance": int(data.get("unconfirmed_balance", 0)),
            "locked_balance": int(data.get("locked_balance", 0)),
            "reserved_balance_anchor_chan": int(data.get("reserved_balance_anchor_chan", 0)),
        }
    
    async def get_channel_balance(self) -> Optional[dict]:
        """Get lightning channel balance.
        
        LND endpoint: GET /v1/balance/channels
        """
        data = await self._request("GET", "/v1/balance/channels")
        if not data:
            return None
        
        local_balance = data.get("local_balance", {})
        remote_balance = data.get("remote_balance", {})
        pending_open_local = data.get("pending_open_local_balance", {})
        pending_open_remote = data.get("pending_open_remote_balance", {})
        unsettled_local = data.get("unsettled_local_balance", {})
        unsettled_remote = data.get("unsettled_remote_balance", {})
        
        return {
            "local_balance_sat": int(local_balance.get("sat", 0)),
            "remote_balance_sat": int(remote_balance.get("sat", 0)),
            "pending_open_local_sat": int(pending_open_local.get("sat", 0)),
            "pending_open_remote_sat": int(pending_open_remote.get("sat", 0)),
            "unsettled_local_sat": int(unsettled_local.get("sat", 0)),
            "unsettled_remote_sat": int(unsettled_remote.get("sat", 0)),
        }
    
    async def get_channels(self) -> Optional[list]:
        """Get list of open channels.
        
        LND endpoint: GET /v1/channels
        """
        data = await self._request("GET", "/v1/channels")
        if not data:
            return None
        
        channels = []
        for ch in data.get("channels", []):
            channels.append({
                "chan_id": ch.get("chan_id", ""),
                "remote_pubkey": ch.get("remote_pubkey", ""),
                "channel_point": ch.get("channel_point", ""),
                "capacity": int(ch.get("capacity", 0)),
                "local_balance": int(ch.get("local_balance", 0)),
                "remote_balance": int(ch.get("remote_balance", 0)),
                "commit_fee": int(ch.get("commit_fee", 0)),
                "total_satoshis_sent": int(ch.get("total_satoshis_sent", 0)),
                "total_satoshis_received": int(ch.get("total_satoshis_received", 0)),
                "num_updates": int(ch.get("num_updates", 0)),
                "active": ch.get("active", False),
                "private": ch.get("private", False),
                "initiator": ch.get("initiator", False),
                "peer_alias": ch.get("peer_alias", ""),
                "uptime": int(ch.get("uptime", 0)),
                "lifetime": int(ch.get("lifetime", 0)),
            })
        
        return channels
    
    async def get_pending_channels(self) -> Optional[dict]:
        """Get pending channels (opening, closing, force-closing).
        
        LND endpoint: GET /v1/channels/pending
        """
        data = await self._request("GET", "/v1/channels/pending")
        if not data:
            return None
        
        return {
            "pending_open_channels": len(data.get("pending_open_channels", [])),
            "pending_closing_channels": len(data.get("pending_closing_channels", [])),
            "pending_force_closing_channels": len(data.get("pending_force_closing_channels", [])),
            "waiting_close_channels": len(data.get("waiting_close_channels", [])),
            "total_limbo_balance": int(data.get("total_limbo_balance", 0)),
        }
    
    async def get_recent_payments(self, max_payments: int = 20) -> Optional[list]:
        """Get recent outgoing lightning payments.
        
        LND endpoint: GET /v1/payments
        """
        data = await self._request(
            "GET", "/v1/payments",
            params={"reversed": "true", "max_payments": str(max_payments), "include_incomplete": "true"}
        )
        if not data:
            return None
        
        payments = []
        for p in data.get("payments", []):
            payments.append({
                "payment_hash": p.get("payment_hash", ""),
                "value_sat": int(p.get("value_sat", 0)),
                "fee_sat": int(p.get("fee_sat", 0)),
                "status": p.get("status", "UNKNOWN"),
                "creation_date": int(p.get("creation_date", 0)),
                "payment_request": p.get("payment_request", ""),
                "failure_reason": p.get("failure_reason", ""),
            })
        
        return payments
    
    async def get_recent_invoices(self, num_max_invoices: int = 20) -> Optional[list]:
        """Get recent incoming lightning invoices.
        
        LND endpoint: GET /v1/invoices
        """
        data = await self._request(
            "GET", "/v1/invoices",
            params={"reversed": "true", "num_max_invoices": str(num_max_invoices)}
        )
        if not data:
            return None
        
        invoices = []
        for inv in data.get("invoices", []):
            invoices.append({
                "memo": inv.get("memo", ""),
                "r_hash": inv.get("r_hash", ""),
                "value": int(inv.get("value", 0)),
                "settled": inv.get("settled", False),
                "creation_date": int(inv.get("creation_date", 0)),
                "settle_date": int(inv.get("settle_date", 0)),
                "amt_paid_sat": int(inv.get("amt_paid_sat", 0)),
                "state": inv.get("state", "OPEN"),
                "is_keysend": inv.get("is_keysend", False),
                "payment_request": inv.get("payment_request", ""),
            })
        
        return invoices
    
    async def get_onchain_transactions(self, max_txns: int = 20) -> Optional[list]:
        """Get recent on-chain transactions.
        
        LND endpoint: GET /v1/transactions
        """
        data = await self._request("GET", "/v1/transactions")
        if not data:
            return None
        
        txns = []
        for tx in data.get("transactions", [])[:max_txns]:
            txns.append({
                "tx_hash": tx.get("tx_hash", ""),
                "amount": int(tx.get("amount", 0)),
                "num_confirmations": int(tx.get("num_confirmations", 0)),
                "block_height": int(tx.get("block_height", 0)),
                "time_stamp": int(tx.get("time_stamp", 0)),
                "total_fees": int(tx.get("total_fees", 0)),
                "label": tx.get("label", ""),
            })
        
        return txns
    
    async def get_wallet_summary(self) -> Optional[dict]:
        """Get a combined wallet summary for the dashboard widget.
        
        Fetches balance, channel balance, and node info in parallel.
        """
        import asyncio
        
        # Fetch all data concurrently
        info_task = asyncio.create_task(self.get_info())
        wallet_task = asyncio.create_task(self.get_wallet_balance())
        channel_task = asyncio.create_task(self.get_channel_balance())
        pending_task = asyncio.create_task(self.get_pending_channels())
        
        info, wallet, channel, pending = await asyncio.gather(
            info_task, wallet_task, channel_task, pending_task
        )
        
        if not any([info, wallet, channel]):
            return None
        
        # Calculate totals
        onchain_sats = wallet.get("confirmed_balance", 0) if wallet else 0
        lightning_local_sats = channel.get("local_balance_sat", 0) if channel else 0
        lightning_remote_sats = channel.get("remote_balance_sat", 0) if channel else 0
        total_sats = onchain_sats + lightning_local_sats
        
        return {
            "connected": True,
            "node_info": info,
            "onchain": wallet,
            "lightning": channel,
            "pending_channels": pending,
            "totals": {
                "total_balance_sats": total_sats,
                "onchain_sats": onchain_sats,
                "lightning_local_sats": lightning_local_sats,
                "lightning_remote_sats": lightning_remote_sats,
                "unconfirmed_sats": wallet.get("unconfirmed_balance", 0) if wallet else 0,
                "num_active_channels": info.get("num_active_channels", 0) if info else 0,
                "num_pending_channels": info.get("num_pending_channels", 0) if info else 0,
                "synced": info.get("synced_to_chain", False) if info else False,
            },
        }

    # ──────────────────────────────────────────────────────────────────
    # Address generation
    # ──────────────────────────────────────────────────────────────────

    async def new_address(
        self,
        address_type: str = "p2tr",
    ) -> tuple[Optional[dict], Optional[str]]:
        """Generate a new on-chain receive address.

        LND endpoint: GET /v1/newaddress

        Args:
            address_type: Address type — "p2wkh" (native segwit),
                          "np2wkh" (nested segwit), or "p2tr" (taproot).
                          Default is taproot (most modern, lowest fees).

        Returns:
            (address_data, error) — address_data has address and address_type
        """
        # LND uses integer enum: 0=WITNESS_PUBKEY_HASH (p2wkh),
        # 1=NESTED_PUBKEY_HASH (np2wkh), 4=TAPROOT_PUBKEY (p2tr)
        type_map = {
            "p2wkh": "0",
            "np2wkh": "1",
            "p2tr": "4",
        }
        lnd_type = type_map.get(address_type, "4")

        data, error = await self._request_with_error(
            "GET", "/v1/newaddress",
            params={"type": lnd_type},
            _scope=_SCOPE_WRITE,
        )
        if error:
            return None, error

        return {
            "address": data.get("address", ""),
            "address_type": address_type,
        }, None

    # ──────────────────────────────────────────────────────────────────
    # Payment operations (Phase 2)
    # ──────────────────────────────────────────────────────────────────

    async def create_invoice(
        self,
        amount_sats: int,
        memo: str = "",
        expiry: int = 3600,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Create a Lightning invoice (BOLT11 payment request).
        
        LND endpoint: POST /v1/invoices
        
        Args:
            amount_sats: Invoice amount in satoshis (0 = any-amount invoice)
            memo: Description attached to the invoice
            expiry: Seconds until invoice expires (default 1 hour)
        
        Returns:
            (invoice_data, error) — invoice_data has r_hash, payment_request, add_index
        """
        body = {
            "value": str(amount_sats),
            "memo": memo,
            "expiry": str(expiry),
        }
        
        data, error = await self._request_with_error("POST", "/v1/invoices", json=body, _scope=_SCOPE_WRITE)
        if error:
            return None, error
        
        # r_hash comes as base64 from REST API — convert to hex for consistency
        r_hash_b64 = data.get("r_hash", "")
        try:
            import base64 as b64
            r_hash_hex = b64.b64decode(r_hash_b64).hex() if r_hash_b64 else ""
        except Exception:
            r_hash_hex = r_hash_b64
        
        return {
            "r_hash": r_hash_hex,
            "payment_request": data.get("payment_request", ""),
            "add_index": data.get("add_index", ""),
        }, None

    async def decode_payment_request(
        self, payment_request: str
    ) -> tuple[Optional[dict], Optional[str]]:
        """Decode a BOLT11 Lightning payment request.
        
        LND endpoint: GET /v1/payreq/{pay_req}
        
        Returns decoded invoice details: destination, amount, description, expiry, etc.
        """
        # URL-encode the payment request (it contains special chars)
        from urllib.parse import quote
        pay_req_encoded = quote(payment_request, safe="")
        
        data, error = await self._request_with_error(
            "GET", f"/v1/payreq/{pay_req_encoded}"
        )
        if error:
            return None, error
        
        return {
            "destination": data.get("destination", ""),
            "payment_hash": data.get("payment_hash", ""),
            "num_satoshis": int(data.get("num_satoshis", 0)),
            "timestamp": int(data.get("timestamp", 0)),
            "expiry": int(data.get("expiry", 0)),
            "description": data.get("description", ""),
            "description_hash": data.get("description_hash", ""),
            "cltv_expiry": int(data.get("cltv_expiry", 0)),
            "num_msat": int(data.get("num_msat", 0)),
            "features": data.get("features", {}),
        }, None

    async def send_payment_sync(
        self,
        payment_request: str,
        fee_limit_sats: Optional[int] = None,
        timeout_seconds: int = 60,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Pay a Lightning invoice (synchronous — blocks until settled or failed).
        
        LND endpoint: POST /v1/channels/transactions
        
        For hold invoices (e.g., Boltz swaps), this blocks until the payee
        settles, which can take minutes. If the HTTP request times out,
        the payment may still be in-flight — callers should check via
        lookup_payment() rather than assuming failure.
        
        Args:
            payment_request: BOLT11 payment request string
            fee_limit_sats: Maximum routing fee in sats (None = LND default)
            timeout_seconds: HTTP timeout (payment continues in LND regardless)
        
        Returns:
            (payment_result, error) — payment_result has payment_hash, fee, status
        """
        body: dict = {
            "payment_request": payment_request,
        }
        if fee_limit_sats is not None:
            body["fee_limit"] = {"fixed": str(fee_limit_sats)}
        
        data, error = await self._request_with_error(
            "POST", "/v1/channels/transactions", json=body,
            timeout=float(timeout_seconds),
            _scope=_SCOPE_WRITE,
        )
        if error:
            return None, error
        
        # Check for payment-level error (LND returns 200 but with payment_error)
        payment_error = data.get("payment_error", "")
        if payment_error:
            return None, f"Payment failed: {payment_error}"
        
        # Extract payment hash (base64 → hex)
        payment_hash_b64 = data.get("payment_hash", "")
        try:
            import base64 as b64
            payment_hash_hex = b64.b64decode(payment_hash_b64).hex() if payment_hash_b64 else ""
        except Exception:
            payment_hash_hex = payment_hash_b64
        
        # Extract preimage (proof of payment)
        payment_preimage_b64 = data.get("payment_preimage", "")
        try:
            import base64 as b64
            payment_preimage_hex = b64.b64decode(payment_preimage_b64).hex() if payment_preimage_b64 else ""
        except Exception:
            payment_preimage_hex = payment_preimage_b64
        
        return {
            "payment_hash": payment_hash_hex,
            "payment_preimage": payment_preimage_hex,
            "payment_route": {
                "total_amt": int(data.get("payment_route", {}).get("total_amt", 0)),
                "total_fees": int(data.get("payment_route", {}).get("total_fees", 0)),
                "total_amt_msat": int(data.get("payment_route", {}).get("total_amt_msat", 0)),
                "total_fees_msat": int(data.get("payment_route", {}).get("total_fees_msat", 0)),
                "hops": len(data.get("payment_route", {}).get("hops", [])),
            } if data.get("payment_route") else None,
        }, None

    async def lookup_payment(self, payment_hash_hex: str) -> tuple[Optional[dict], Optional[str]]:
        """Look up an outgoing payment by its payment hash.

        LND endpoint: GET /v2/router/track/{payment_hash}
        Fallback:     GET /v1/payments (filtered)

        Returns:
            (payment_info, error) — payment_info has status, payment_hash, fee, preimage
            status is one of: "IN_FLIGHT", "SUCCEEDED", "FAILED", "INITIATED", "UNKNOWN"
        """
        # Try listing recent payments and filtering by hash
        data = await self._request(
            "GET", "/v1/payments",
            params={"include_incomplete": "true", "max_payments": "100", "reversed": "true"},
        )
        if data and "payments" in data:
            for p in data["payments"]:
                if p.get("payment_hash") == payment_hash_hex:
                    status = p.get("status", "UNKNOWN")
                    fee_sat = int(p.get("fee_sat", 0))
                    preimage = p.get("payment_preimage", "")
                    return {
                        "status": status,
                        "payment_hash": payment_hash_hex,
                        "fee_sat": fee_sat,
                        "payment_preimage": preimage,
                        "value_sat": int(p.get("value_sat", 0)),
                    }, None
            return {"status": "UNKNOWN", "payment_hash": payment_hash_hex}, None

        return None, "Failed to query payments from LND"

    async def send_coins(
        self,
        address: str,
        amount_sats: int,
        sat_per_vbyte: Optional[int] = None,
        label: str = "",
    ) -> tuple[Optional[dict], Optional[str]]:
        """Send on-chain Bitcoin to an address.
        
        LND endpoint: POST /v1/transactions
        
        Args:
            address: Bitcoin address (bech32, p2sh, p2pkh)
            amount_sats: Amount in satoshis
            sat_per_vbyte: Fee rate (None = LND default/automatic)
            label: Optional label for the transaction
        
        Returns:
            (tx_result, error) — tx_result has txid
        """
        body: dict = {
            "addr": address,
            "amount": str(amount_sats),
            "label": label,
        }
        if sat_per_vbyte is not None:
            body["sat_per_vbyte"] = str(sat_per_vbyte)
        
        data, error = await self._request_with_error(
            "POST", "/v1/transactions", json=body,
            _scope=_SCOPE_WRITE,
        )
        if error:
            return None, error
        
        return {
            "txid": data.get("txid", ""),
        }, None

    async def estimate_fee(
        self,
        address: str,
        amount_sats: int,
        target_conf: int = 6,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Estimate on-chain transaction fee.
        
        LND endpoint: GET /v1/transactions/fee
        
        Args:
            address: Target Bitcoin address
            amount_sats: Amount to send
            target_conf: Target number of confirmations (affects fee rate)
        
        Returns:
            (fee_data, error) — fee_data has fee_sat, feerate_sat_per_byte, sat_per_vbyte
        """
        # SA2-23: Validate Bitcoin address format before using in query param key
        import re
        if not address or not re.fullmatch(r'[a-zA-Z0-9]{25,90}', address):
            return None, "Invalid Bitcoin address format"
        
        params = {
            f"AddrToAmount[{address}]": str(amount_sats),
            "target_conf": str(target_conf),
        }
        
        data, error = await self._request_with_error(
            "GET", "/v1/transactions/fee", params=params
        )
        if error:
            return None, error
        
        return {
            "fee_sat": int(data.get("fee_sat", 0)),
            "feerate_sat_per_byte": int(data.get("feerate_sat_per_byte", 0)),
            "sat_per_vbyte": int(data.get("sat_per_vbyte", 0)),
        }, None

    async def lookup_invoice(
        self, r_hash_hex: str
    ) -> tuple[Optional[dict], Optional[str]]:
        """Look up a specific invoice by its payment hash.
        
        LND endpoint: GET /v1/invoice/{r_hash_str}
        
        Args:
            r_hash_hex: Payment hash in hex encoding
        
        Returns:
            (invoice_data, error) — invoice details with settlement status
        """
        # SA2-21: Validate r_hash_hex is actually hex to prevent path injection
        import re
        if not r_hash_hex or not re.fullmatch(r'[0-9a-fA-F]+', r_hash_hex):
            return None, "Invalid r_hash format: must be hex-encoded"
        
        data, error = await self._request_with_error(
            "GET", f"/v1/invoice/{r_hash_hex}"
        )
        if error:
            return None, error
        
        return {
            "memo": data.get("memo", ""),
            "r_hash": r_hash_hex,
            "value": int(data.get("value", 0)),
            "settled": data.get("settled", False),
            "creation_date": int(data.get("creation_date", 0)),
            "settle_date": int(data.get("settle_date", 0)),
            "amt_paid_sat": int(data.get("amt_paid_sat", 0)),
            "state": data.get("state", "OPEN"),
            "payment_request": data.get("payment_request", ""),
            "is_keysend": data.get("is_keysend", False),
        }, None

    # ──────────────────────────────────────────────────────────────────
    # Channel management
    # ──────────────────────────────────────────────────────────────────

    async def connect_peer(
        self,
        pubkey: str,
        host: str,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Connect to a Lightning Network peer.

        LND endpoint: POST /v1/peers

        Must be connected before opening a channel. Idempotent — if
        already connected, LND returns an error we can safely ignore.

        Args:
            pubkey: Hex-encoded public key of the peer
            host: Host address (ip:port or [ipv6]:port)

        Returns:
            (result, error) — result is {} on success
        """
        body = {
            "addr": {
                "pubkey": pubkey,
                "host": host,
            },
            "perm": True,  # persistent connection
        }

        data, error = await self._request_with_error("POST", "/v1/peers", json=body, _scope=_SCOPE_WRITE)
        if error:
            # "already connected" is not a real error
            if "already connected" in (error or "").lower():
                logger.info(f"Peer {pubkey[:12]}... already connected")
                return {}, None
            return None, error

        return data or {}, None

    async def open_channel(
        self,
        node_pubkey_hex: str,
        local_funding_amount: int,
        sat_per_vbyte: Optional[int] = None,
        push_sat: int = 0,
        private: bool = False,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Open a new Lightning channel.

        LND endpoint: POST /v1/channels  (synchronous open)

        This is the *synchronous* variant — it returns once the funding
        transaction has been broadcast (not when the channel is confirmed).

        Args:
            node_pubkey_hex: Hex public key of the remote node
            local_funding_amount: Total channel capacity in sats (our side funds it)
            sat_per_vbyte: Fee rate for the funding tx (None = LND default)
            push_sat: Amount to push to the remote side on open (default 0)
            private: Whether the channel should be private

        Returns:
            (channel_data, error) — channel_data has funding_txid and output_index
        """
        import base64 as b64

        # LND REST API expects node_pubkey as base64
        pubkey_bytes = bytes.fromhex(node_pubkey_hex)
        pubkey_b64 = b64.b64encode(pubkey_bytes).decode()

        body: dict = {
            "node_pubkey": pubkey_b64,
            "local_funding_amount": str(local_funding_amount),
            "push_sat": str(push_sat),
            "private": private,
            "spend_unconfirmed": False,
        }
        if sat_per_vbyte is not None:
            body["sat_per_vbyte"] = str(sat_per_vbyte)

        data, error = await self._request_with_error(
            "POST", "/v1/channels", json=body,
            _scope=_SCOPE_WRITE,
        )
        if error:
            return None, error

        # Extract funding txid — LND returns as base64-encoded bytes (reversed)
        funding_txid_bytes_b64 = data.get("funding_txid_bytes", "")
        try:
            txid_bytes = b64.b64decode(funding_txid_bytes_b64)
            # LND returns txid bytes in reverse order
            funding_txid = txid_bytes[::-1].hex()
        except Exception:
            funding_txid = data.get("funding_txid_str", "")

        return {
            "funding_txid": funding_txid,
            "output_index": data.get("output_index", 0),
        }, None

    async def get_pending_channels_detail(self) -> Optional[list]:
        """Get detailed pending channel info (opening, closing).

        LND endpoint: GET /v1/channels/pending

        Returns a list of pending channels with capacity, peer, and status.
        """
        data = await self._request("GET", "/v1/channels/pending")
        if not data:
            return None

        result = []
        for pch in data.get("pending_open_channels", []):
            ch = pch.get("channel", {})
            result.append({
                "type": "pending_open",
                "remote_node_pub": ch.get("remote_node_pub", ""),
                "channel_point": ch.get("channel_point", ""),
                "capacity": int(ch.get("capacity", 0)),
                "local_balance": int(ch.get("local_balance", 0)),
                "remote_balance": int(ch.get("remote_balance", 0)),
                "commit_fee": int(pch.get("commit_fee", 0)),
                "confirmation_height": int(pch.get("confirmation_height", 0)),
            })

        for pch in data.get("pending_closing_channels", []):
            ch = pch.get("channel", {})
            result.append({
                "type": "pending_close",
                "remote_node_pub": ch.get("remote_node_pub", ""),
                "channel_point": ch.get("channel_point", ""),
                "capacity": int(ch.get("capacity", 0)),
                "local_balance": int(ch.get("local_balance", 0)),
                "remote_balance": int(ch.get("remote_balance", 0)),
                "closing_txid": pch.get("closing_txid", ""),
            })

        for pch in data.get("pending_force_closing_channels", []):
            ch = pch.get("channel", {})
            result.append({
                "type": "force_closing",
                "remote_node_pub": ch.get("remote_node_pub", ""),
                "channel_point": ch.get("channel_point", ""),
                "capacity": int(ch.get("capacity", 0)),
                "local_balance": int(ch.get("local_balance", 0)),
                "remote_balance": int(ch.get("remote_balance", 0)),
                "closing_txid": pch.get("closing_txid", ""),
                "blocks_til_maturity": int(pch.get("blocks_til_maturity", 0)),
            })

        return result


# Singleton instance
lnd_service = LNDService()
