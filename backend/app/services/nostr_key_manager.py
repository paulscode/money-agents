"""
Nostr Key Manager — Fernet-encrypted storage for Nostr private keys (nsec).

Security model: derives a Fernet key from the application's SECRET_KEY
using PBKDF2-HMAC-SHA256 (600 000 iterations), then encrypts/decrypts
nsec strings at rest.  Keys only exist in plaintext in-memory during
signing operations.
"""
import logging

from app.core.encryption import encrypt_field, decrypt_field

logger = logging.getLogger(__name__)


def encrypt_nsec(nsec: str) -> str:
    """Encrypt a Nostr nsec (private key) for database storage.

    Args:
        nsec: The bech32-encoded nsec string (starts with "nsec1").

    Returns:
        Fernet-encrypted ciphertext (safe for DB TEXT column).
    """
    return encrypt_field(nsec)


def decrypt_nsec(encrypted: str) -> str:
    """Decrypt a stored nsec back to plaintext for signing.

    Args:
        encrypted: The Fernet-encrypted ciphertext from the database.

    Returns:
        The original bech32 nsec string.

    Raises:
        ValueError: If decryption fails (wrong key or tampered data).
    """
    try:
        return decrypt_field(encrypted)
    except ValueError:
        logger.error("Failed to decrypt Nostr private key — SECRET_KEY may have changed")
        raise ValueError("Cannot decrypt Nostr private key. The application SECRET_KEY may have changed.")
