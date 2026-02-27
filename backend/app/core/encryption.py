"""
Application-level Fernet encryption utilities.

Provides symmetric encryption for secrets stored in the database
(Nostr private keys, Boltz swap keys, etc.).

The Fernet key is derived from the application's SECRET_KEY using
PBKDF2-HMAC-SHA256 with a per-installation random salt and 600 000
iterations.

Salt management (GAP: MEDIUM-3):
  On first use, a 32-byte random salt is generated and written to
  ``$DATA_DIR/.encryption_salt`` (default: ``backend/.encryption_salt``).
  Subsequent starts read the same file.  If the file is missing but
  encrypted data already exists, the legacy fixed salt is tried as a
  transparent migration path.
"""
import base64
import hashlib
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

# Legacy fixed salt kept for transparent migration of existing ciphertext.
_LEGACY_KDF_SALT = b"money-agents:field-encryption:v1"
_KDF_ITERATIONS = 600_000

# Default location for the per-installation salt file.
# Overridable via ENCRYPTION_SALT_FILE env var (mainly for tests).
_SALT_FILE = Path(
    os.environ.get(
        "ENCRYPTION_SALT_FILE",
        str(Path(__file__).resolve().parent.parent.parent / ".encryption_salt"),
    )
)

# Lazy-initialised Fernet instances (one per process)
_fernet: Fernet | None = None
_fernet_legacy: Fernet | None = None


def _load_or_create_salt() -> bytes:
    """Load the per-installation salt, creating it on first run."""
    if _SALT_FILE.exists():
        salt = _SALT_FILE.read_bytes()
        if len(salt) >= 16:
            return salt
        logger.warning("Encryption salt file too short (%d bytes), regenerating", len(salt))

    salt = os.urandom(32)
    try:
        _SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SALT_FILE.write_bytes(salt)
        # Restrict permissions — owner read/write only
        _SALT_FILE.chmod(0o600)
        logger.info("Generated new per-installation encryption salt at %s", _SALT_FILE)
    except OSError as exc:
        logger.warning(
            "Could not persist encryption salt to %s: %s — "
            "encryption will work but a new salt will be generated on restart, "
            "breaking decryption of data encrypted in this session.",
            _SALT_FILE, exc,
        )
    return salt


def _derive_fernet(salt: bytes) -> Fernet:
    """Derive a Fernet instance from SECRET_KEY and the given salt."""
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        settings.secret_key.get_secret_value().encode("utf-8"),
        salt,
        iterations=_KDF_ITERATIONS,
        dklen=32,
    )
    key = base64.urlsafe_b64encode(derived)
    return Fernet(key)


def _get_fernet() -> Fernet:
    """Return the primary Fernet instance (random per-installation salt)."""
    global _fernet
    if _fernet is None:
        salt = _load_or_create_salt()
        _fernet = _derive_fernet(salt)
    return _fernet


def _get_legacy_fernet() -> Fernet:
    """Return a Fernet instance using the legacy fixed salt (migration)."""
    global _fernet_legacy
    if _fernet_legacy is None:
        _fernet_legacy = _derive_fernet(_LEGACY_KDF_SALT)
    return _fernet_legacy


def encrypt_field(plaintext: str) -> str:
    """Encrypt a plaintext string for database storage.

    Returns a Fernet token (URL-safe base64 string) suitable for a
    TEXT / VARCHAR column.
    """
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_field(ciphertext: str) -> str:
    """Decrypt a Fernet token back to plaintext.

    Tries the current per-installation salt first.  If that fails,
    transparently retries with the legacy fixed salt so data encrypted
    before the MEDIUM-3 migration still decrypts.  On successful legacy
    decryption a warning is logged encouraging re-encryption.

    Raises ValueError if decryption fails with both salts.
    """
    ct_bytes = ciphertext.encode("utf-8")

    # Try current salt first (fast path)
    try:
        return _get_fernet().decrypt(ct_bytes).decode("utf-8")
    except InvalidToken:
        pass

    # Transparent migration: try legacy fixed salt
    try:
        plaintext = _get_legacy_fernet().decrypt(ct_bytes).decode("utf-8")
        logger.info(
            "Decrypted field using legacy salt — consider re-encrypting "
            "stored data with the per-installation salt."
        )
        return plaintext
    except InvalidToken:
        raise ValueError(
            "Cannot decrypt field. The application SECRET_KEY may have changed "
            "or the encryption salt file is missing/corrupted."
        )


def validate_encryption_health() -> None:
    """SGA3-L12: Startup health check for encryption roundtrip.

    Verifies that encrypt/decrypt works with the current SECRET_KEY and
    salt file.  Should be called at application startup to fail fast
    rather than discovering lost data at use time.

    Raises AssertionError if the roundtrip fails.
    """
    sentinel = "__encryption_health_check__"
    try:
        ct = encrypt_field(sentinel)
        pt = decrypt_field(ct)
        assert pt == sentinel, (
            f"Encryption roundtrip mismatch: expected {sentinel!r}, got {pt!r}"
        )
    except Exception as exc:
        raise AssertionError(
            f"Encryption health check failed — the SECRET_KEY or "
            f".encryption_salt may be missing/corrupted: {exc}"
        ) from exc
