"""
Boltz Swap Service — Reverse Submarine Swap orchestration.

Manages the lifecycle of Boltz reverse swaps (Lightning → On-chain) for
cold storage withdrawals. All Boltz API traffic is routed via Tor by default.

Responsibilities:
- Fetch swap pair info (fees/limits)
- Create reverse swaps (generate preimage/keypair, call Boltz API)
- Persist swap state to PostgreSQL for crash recovery
- Monitor swap status and advance lifecycle
- Coordinate claim transaction construction (via Node.js boltz-core)
- Handle failure modes and recovery

Crypto-heavy claim construction is delegated to a Node.js subprocess
using the boltz-core library (reference implementation).
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import select, or_

# GAP-12: Pattern to redact hex strings ≥32 chars (potential keys/preimages)
_HEX_KEY_PATTERN = re.compile(r'\b[0-9a-fA-F]{32,}\b')


def _sanitize_stderr(raw: bytes, max_len: int = 500) -> str:
    """Sanitize subprocess stderr for safe logging.

    Strips hex strings ≥32 characters that could be cryptographic key
    material (private keys, preimages) leaked by the claim script.
    """
    text = raw.decode(errors="replace")[:max_len] if raw else ""
    return _HEX_KEY_PATTERN.sub("[REDACTED_HEX]", text)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import encrypt_field, decrypt_field
from app.models.boltz_swap import BoltzSwap, SwapStatus

logger = logging.getLogger(__name__)

# Boltz reverse swap limits (BTC→BTC)
BOLTZ_MIN_AMOUNT_SATS = 25_000
BOLTZ_MAX_AMOUNT_SATS = 25_000_000

# Claim script location
CLAIM_SCRIPT_DIR = Path(__file__).parent.parent.parent / "scripts"
CLAIM_SCRIPT_PATH = CLAIM_SCRIPT_DIR / "boltz_claim.js"

from app.core.datetime_utils import utc_now as _utc_now


def _generate_preimage() -> tuple[str, str]:
    """Generate a 32-byte random preimage and its SHA-256 hash.

    Returns:
        (preimage_hex, preimage_hash_hex)
    """
    preimage = secrets.token_bytes(32)
    preimage_hash = hashlib.sha256(preimage).digest()
    return preimage.hex(), preimage_hash.hex()


async def _generate_keypair() -> tuple[str, str]:
    """Generate an ephemeral secp256k1 keypair for claim signing.

    Uses the Node.js boltz-core helper for correct EC math.
    Private key is passed via stdin to avoid exposure in process listing.
    Returns (private_key_hex, public_key_hex) — 32-byte privkey, 33-byte compressed pubkey.

    Uses asyncio subprocess to avoid blocking the event loop (GAP: MEDIUM-5).
    """
    # Generate a random 32-byte private key
    private_key = secrets.token_bytes(32)

    node_script = """
                const { ECPairFactory } = require('ecpair');
                const ecc = require('tiny-secp256k1');
                const ECPair = ECPairFactory(ecc);
                let data = '';
                process.stdin.on('data', c => data += c);
                process.stdin.on('end', () => {
                    const kp = ECPair.fromPrivateKey(Buffer.from(data.trim(), 'hex'));
                    console.log(JSON.stringify({
                        privateKey: kp.privateKey.toString('hex'),
                        publicKey: kp.publicKey.toString('hex')
                    }));
                });
                """

    try:
        proc = await asyncio.create_subprocess_exec(
            "node", "-e", node_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(CLAIM_SCRIPT_DIR),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=private_key.hex().encode()),
                timeout=10,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                "EC keypair generation timed out (10s). "
                "Node.js may be hanging or overloaded."
            )

        if proc.returncode == 0:
            data = json.loads(stdout.decode().strip())
            return data["privateKey"], data["publicKey"]
        else:
            logger.error("Keypair generation failed (non-zero exit): %s", _sanitize_stderr(stderr))
            raise RuntimeError("EC keypair generation failed")
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(
            f"EC keypair generation returned invalid data: {e}"
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Node.js not found. Required for Boltz claim signing. "
            "Install Node.js or add it to the backend Docker image."
        )


class BoltzSwapService:
    """Manages Boltz Reverse Submarine Swaps for cold storage withdrawals."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._pair_info_cache: Optional[dict] = None
        self._pair_info_cached_at: Optional[datetime] = None

    @property
    def _boltz_url(self) -> str:
        """Active Boltz API URL (Tor onion or clearnet)."""
        if settings.boltz_use_tor and settings.lnd_tor_proxy:
            return settings.boltz_onion_url
        return settings.boltz_api_url

    @property
    def _proxy(self) -> Optional[str]:
        """SOCKS5 proxy URL if Tor routing is enabled."""
        if settings.boltz_use_tor and settings.lnd_tor_proxy:
            return settings.lnd_tor_proxy
        return None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client for Boltz API."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                proxy=self._proxy,
                timeout=httpx.Timeout(30.0, connect=15.0),
                verify=True,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[dict] = None,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Make a request to the Boltz API.

        Returns (data, error_message). On success error is None.
        Falls back to clearnet if Tor unavailable and fallback is enabled.
        """
        url = f"{self._boltz_url}{path}"
        try:
            client = await self._get_client()
            response = await client.request(method, url, json=json_data)
            response.raise_for_status()
            return response.json(), None
        except (httpx.ConnectError, httpx.ProxyError, httpx.ReadTimeout) as e:
            if settings.boltz_fallback_clearnet and settings.boltz_use_tor:
                logger.warning(f"Tor connection to Boltz failed, trying clearnet: {e}")
                return await self._request_clearnet(method, path, json_data)
            error_type = type(e).__name__
            return None, f"Connection failed ({error_type}): {e}"
        except httpx.HTTPStatusError as e:
            body = e.response.text
            try:
                error_body = e.response.json()
                body = error_body.get("error", body)
            except Exception:
                pass
            return None, f"Boltz API error {e.response.status_code}: {body}"
        except Exception as e:
            return None, f"Boltz request failed: {e}"

    async def _request_clearnet(
        self,
        method: str,
        path: str,
        json_data: Optional[dict] = None,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Fallback clearnet request (no Tor proxy)."""
        url = f"{settings.boltz_api_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(method, url, json=json_data)
                response.raise_for_status()
                return response.json(), None
        except httpx.HTTPStatusError as e:
            body = e.response.text
            try:
                body = e.response.json().get("error", body)
            except Exception:
                pass
            return None, f"Boltz API error {e.response.status_code}: {body}"
        except Exception as e:
            return None, f"Boltz clearnet request failed: {e}"

    # ─── Public API ──────────────────────────────────────────────────

    async def get_reverse_pair_info(self) -> tuple[Optional[dict], Optional[str]]:
        """Fetch current BTC/BTC reverse swap fees and limits.

        Returns parsed pair info dict with keys:
            min, max, fees_percentage, fees_miner_lockup, fees_miner_claim
        Caches for 60 seconds.
        """
        now = _utc_now()
        if (
            self._pair_info_cache
            and self._pair_info_cached_at
            and (now - self._pair_info_cached_at).total_seconds() < 60
        ):
            return self._pair_info_cache, None

        data, error = await self._request("GET", "/swap/reverse")
        if error:
            return None, error

        try:
            btc_pair = data.get("BTC", {}).get("BTC", {})
            if not btc_pair:
                return None, "BTC/BTC reverse pair not found in Boltz response"

            info = {
                "min": btc_pair.get("limits", {}).get("minimal", BOLTZ_MIN_AMOUNT_SATS),
                "max": btc_pair.get("limits", {}).get("maximal", BOLTZ_MAX_AMOUNT_SATS),
                "fees_percentage": btc_pair.get("fees", {}).get("percentage", 0.5),
                "fees_miner_lockup": btc_pair.get("fees", {}).get("minerFees", {}).get("lockup", 462),
                "fees_miner_claim": btc_pair.get("fees", {}).get("minerFees", {}).get("claim", 333),
                "hash": btc_pair.get("hash", ""),
            }
            self._pair_info_cache = info
            self._pair_info_cached_at = now
            return info, None
        except Exception as e:
            return None, f"Failed to parse pair info: {e}"

    async def create_reverse_swap(
        self,
        db: AsyncSession,
        user_id: UUID,
        invoice_amount_sats: int,
        destination_address: str,
    ) -> tuple[Optional[BoltzSwap], Optional[str]]:
        """Create a Boltz reverse swap for cold storage withdrawal.

        1. Validates amount is within Boltz limits
        2. Generates preimage + claim keypair
        3. Calls Boltz /swap/reverse to create swap
        4. Persists complete swap state to DB
        5. Returns swap record (caller should then pay the invoice)
        """
        # Validate amount
        pair_info, err = await self.get_reverse_pair_info()
        if err:
            return None, f"Failed to fetch Boltz pair info: {err}"

        min_amt = pair_info["min"]
        max_amt = pair_info["max"]
        if invoice_amount_sats < min_amt or invoice_amount_sats > max_amt:
            return None, f"Amount must be between {min_amt:,} and {max_amt:,} sats"

        # Generate crypto material
        try:
            preimage_hex, preimage_hash_hex = _generate_preimage()
            claim_private_key_hex, claim_public_key_hex = await _generate_keypair()
        except RuntimeError as e:
            return None, str(e)

        # Create swap with Boltz
        swap_request = {
            "from": "BTC",
            "to": "BTC",
            "preimageHash": preimage_hash_hex,
            "claimPublicKey": claim_public_key_hex,
            "invoiceAmount": invoice_amount_sats,
            "claimAddress": destination_address,
        }

        # Include pair hash for locked pricing
        if pair_info.get("hash"):
            swap_request["pairHash"] = pair_info["hash"]

        data, error = await self._request("POST", "/swap/reverse", swap_request)
        if error:
            return None, f"Boltz swap creation failed: {error}"

        # Persist swap state (encrypt sensitive key material at rest)
        swap = BoltzSwap(
            boltz_swap_id=data["id"],
            user_id=user_id,
            invoice_amount_sats=invoice_amount_sats,
            onchain_amount_sats=data.get("onchainAmount"),
            destination_address=destination_address,
            fee_percentage=str(pair_info["fees_percentage"]),
            miner_fee_sats=pair_info["fees_miner_lockup"] + pair_info["fees_miner_claim"],
            preimage_hex=encrypt_field(preimage_hex),
            preimage_hash_hex=preimage_hash_hex,
            claim_private_key_hex=encrypt_field(claim_private_key_hex),
            claim_public_key_hex=claim_public_key_hex,
            boltz_invoice=data.get("invoice"),
            boltz_lockup_address=data.get("lockupAddress"),
            boltz_refund_public_key_hex=data.get("refundPublicKey"),
            boltz_swap_tree_json=data.get("swapTree"),
            timeout_block_height=data.get("timeoutBlockHeight"),
            boltz_blinding_key=data.get("blindingKey"),
            status=SwapStatus.CREATED,
            boltz_status="swap.created",
            status_history=[{
                "status": "created",
                "boltz_status": "swap.created",
                "timestamp": _utc_now().isoformat(),
            }],
        )
        db.add(swap)
        await db.commit()
        await db.refresh(swap)

        logger.info(
            f"Boltz reverse swap created: {swap.boltz_swap_id}, "
            f"amount={invoice_amount_sats} sats, dest={destination_address[:12]}..."
        )
        return swap, None

    async def get_swap_status_from_boltz(self, boltz_swap_id: str) -> tuple[Optional[str], Optional[dict], Optional[str]]:
        """Query Boltz for current swap status.

        Returns (status_string, full_response, error).
        """
        # SA2-22: Validate swap_id format to prevent path injection
        import re
        if not boltz_swap_id or not re.fullmatch(r'[a-zA-Z0-9\-]+', boltz_swap_id):
            return None, None, "Invalid swap ID format"
        
        data, error = await self._request("GET", f"/swap/{boltz_swap_id}")
        if error:
            return None, None, error
        return data.get("status"), data, None

    async def get_lockup_transaction(self, boltz_swap_id: str) -> tuple[Optional[str], Optional[str]]:
        """Fetch the lockup transaction hex from Boltz.

        Returns (tx_hex, error).
        """
        # SA3-M4: Validate swap ID format (parity with get_swap_status_from_boltz)
        if not boltz_swap_id or not re.fullmatch(r'[a-zA-Z0-9\-]+', boltz_swap_id):
            return None, "Invalid swap ID format"

        data, error = await self._request("GET", f"/swap/reverse/{boltz_swap_id}/transaction")
        if error:
            return None, error
        return data.get("hex") or data.get("transactionHex"), None

    async def cooperative_claim(
        self,
        swap: BoltzSwap,
        lockup_tx_hex: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """Construct and broadcast a cooperative Taproot claim transaction.

        Delegates to the Node.js boltz-core claim script for Musig2 signing.

        Returns (claim_txid, error).
        """
        if not CLAIM_SCRIPT_PATH.exists():
            return None, f"Claim script not found at {CLAIM_SCRIPT_PATH}"

        # Build input for claim script
        # Use Tor onion URL + SOCKS proxy when Tor routing is enabled,
        # so the user's IP is never revealed to Boltz Exchange.
        # Falls back to clearnet URL when Tor is not configured.
        claim_input = {
            "boltzUrl": self._boltz_url,
            "swapId": swap.boltz_swap_id,
            "preimage": decrypt_field(swap.preimage_hex),
            "claimPrivateKey": decrypt_field(swap.claim_private_key_hex),
            "refundPublicKey": swap.boltz_refund_public_key_hex,
            "swapTree": swap.boltz_swap_tree_json,
            "lockupTxHex": lockup_tx_hex,
            "destinationAddress": swap.destination_address,
        }

        # Pass SOCKS proxy so the Node.js script routes through Tor
        proxy = self._proxy
        if proxy:
            claim_input["socksProxy"] = proxy

        try:
            # Longer timeout when routing through Tor (onion circuit adds latency)
            script_timeout = 120 if proxy else 60
            proc = await asyncio.create_subprocess_exec(
                "node", str(CLAIM_SCRIPT_PATH),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(CLAIM_SCRIPT_DIR),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=json.dumps(claim_input).encode()),
                    timeout=script_timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return None, f"Claim script timed out ({script_timeout}s)"

            if proc.returncode != 0:
                # GAP-12: Sanitize stderr — redact hex key/preimage material
                stderr_safe = _sanitize_stderr(stderr)
                logger.error(f"Claim script failed (exit {proc.returncode}): {stderr_safe}")
                return None, "Claim script failed (non-zero exit code)"

            output = json.loads(stdout.decode().strip())
            txid = output.get("txid")
            if not txid:
                return None, f"Claim script returned no txid: {output}"

            return txid, None
        except json.JSONDecodeError:
            logger.error("Claim script returned invalid JSON: %s", stdout.decode()[:500])
            return None, "Claim script returned invalid output"
        except FileNotFoundError:
            return None, "Node.js not found for claim script execution"
        except Exception as e:
            return None, f"Claim script error: {e}"

    async def broadcast_transaction(self, tx_hex: str) -> tuple[Optional[str], Optional[str]]:
        """Broadcast a raw transaction via Boltz API.

        Returns (txid, error).
        """
        data, error = await self._request("POST", "/chain/BTC/transaction", {"hex": tx_hex})
        if error:
            return None, error
        return data.get("id"), None

    async def advance_swap(
        self,
        db: AsyncSession,
        swap: BoltzSwap,
    ) -> tuple[BoltzSwap, Optional[str]]:
        """Check swap status and advance the lifecycle.

        Called by the Celery monitoring task. Handles:
        - CREATED: check if invoice should still be paid
        - PAYING_INVOICE: check LND payment status
        - INVOICE_PAID: wait for Boltz lockup tx, then claim
        - CLAIMING: check if claim tx broadcast
        - CLAIMED: check if claim tx confirmed

        Returns (updated_swap, error).
        """
        boltz_status, boltz_data, err = await self.get_swap_status_from_boltz(
            swap.boltz_swap_id
        )
        if err:
            logger.warning(f"Failed to check Boltz status for {swap.boltz_swap_id}: {err}")
            return swap, err

        old_boltz_status = swap.boltz_status
        swap.boltz_status = boltz_status
        swap.updated_at = _utc_now()

        # Append to status history
        if boltz_status != old_boltz_status:
            history = swap.status_history or []
            history.append({
                "status": swap.status.value,
                "boltz_status": boltz_status,
                "timestamp": _utc_now().isoformat(),
            })
            swap.status_history = history

        # Terminal failure states
        if boltz_status in ("invoice.expired", "swap.expired", "transaction.failed"):
            swap.status = SwapStatus.FAILED
            swap.error_message = f"Boltz swap ended: {boltz_status}"
            swap.completed_at = _utc_now()
            await db.commit()
            logger.warning(f"Swap {swap.boltz_swap_id} failed: {boltz_status}")
            return swap, None

        if boltz_status == "transaction.refunded":
            swap.status = SwapStatus.REFUNDED
            swap.error_message = (
                "Boltz refunded the on-chain lockup (claim timed out). "
                "Lightning funds were paid but on-chain funds were not received."
            )
            swap.completed_at = _utc_now()
            await db.commit()
            logger.error(f"CRITICAL: Swap {swap.boltz_swap_id} was refunded by Boltz!")
            return swap, None

        if boltz_status == "invoice.settled":
            swap.status = SwapStatus.COMPLETED
            swap.completed_at = _utc_now()
            await db.commit()
            logger.info(f"Swap {swap.boltz_swap_id} completed successfully")
            return swap, None

        # Lockup transaction appeared — attempt claim
        if boltz_status in ("transaction.mempool", "transaction.confirmed"):
            if swap.status in (SwapStatus.INVOICE_PAID, SwapStatus.PAYING_INVOICE, SwapStatus.CREATED):
                swap.status = SwapStatus.CLAIMING
                await db.commit()

            if swap.status == SwapStatus.CLAIMING and not swap.claim_txid:
                lockup_hex, lockup_err = await self.get_lockup_transaction(swap.boltz_swap_id)
                if lockup_err:
                    logger.warning(f"Failed to fetch lockup tx: {lockup_err}")
                    return swap, lockup_err

                claim_txid, claim_err = await self.cooperative_claim(swap, lockup_hex)
                if claim_err:
                    logger.error(f"Claim failed for {swap.boltz_swap_id}: {claim_err}")
                    swap.recovery_count = (swap.recovery_count or 0) + 1
                    swap.recovery_attempted_at = _utc_now()
                    await db.commit()
                    return swap, claim_err

                swap.claim_txid = claim_txid
                swap.status = SwapStatus.CLAIMED
                await db.commit()
                logger.info(f"Swap {swap.boltz_swap_id} claimed: txid={claim_txid}")

        await db.commit()
        return swap, None

    async def recover_pending_swaps(self, db: AsyncSession) -> list[dict]:
        """Recover swaps interrupted by crash/restart.

        Called on startup. Checks all non-terminal swaps and resumes processing.
        Returns list of recovery results.
        """
        result = await db.execute(
            select(BoltzSwap).where(
                or_(
                    BoltzSwap.status == SwapStatus.CREATED,
                    BoltzSwap.status == SwapStatus.PAYING_INVOICE,
                    BoltzSwap.status == SwapStatus.INVOICE_PAID,
                    BoltzSwap.status == SwapStatus.CLAIMING,
                    BoltzSwap.status == SwapStatus.CLAIMED,
                )
            )
        )
        pending_swaps = result.scalars().all()

        if not pending_swaps:
            return []

        logger.info(f"Recovering {len(pending_swaps)} pending Boltz swap(s)")
        results = []

        for swap in pending_swaps:
            try:
                _, err = await self.advance_swap(db, swap)
                results.append({
                    "boltz_swap_id": swap.boltz_swap_id,
                    "status": swap.status.value,
                    "error": err,
                })
            except Exception as e:
                logger.error(f"Recovery failed for {swap.boltz_swap_id}: {e}")
                results.append({
                    "boltz_swap_id": swap.boltz_swap_id,
                    "status": swap.status.value,
                    "error": str(e),
                })

        return results

    async def get_swap_by_id(self, db: AsyncSession, swap_id: UUID) -> Optional[BoltzSwap]:
        """Fetch a swap by its internal UUID."""
        result = await db.execute(
            select(BoltzSwap).where(BoltzSwap.id == swap_id)
        )
        return result.scalar_one_or_none()

    async def get_swaps_for_user(
        self,
        db: AsyncSession,
        user_id: UUID,
        limit: int = 20,
    ) -> list[BoltzSwap]:
        """Fetch recent swaps for a user."""
        result = await db.execute(
            select(BoltzSwap)
            .where(BoltzSwap.user_id == user_id)
            .order_by(BoltzSwap.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def cancel_swap(
        self,
        db: AsyncSession,
        swap: BoltzSwap,
    ) -> tuple[bool, Optional[str]]:
        """Cancel a swap if still in early stages."""
        if swap.status not in (SwapStatus.CREATED,):
            return False, f"Cannot cancel swap in status '{swap.status.value}'. Only 'created' swaps can be cancelled."

        swap.status = SwapStatus.CANCELLED
        swap.completed_at = _utc_now()
        swap.error_message = "Cancelled by user"
        history = swap.status_history or []
        history.append({
            "status": "cancelled",
            "timestamp": _utc_now().isoformat(),
        })
        swap.status_history = history
        await db.commit()
        logger.info(f"Swap {swap.boltz_swap_id} cancelled by user")
        return True, None


# Singleton
boltz_service = BoltzSwapService()
