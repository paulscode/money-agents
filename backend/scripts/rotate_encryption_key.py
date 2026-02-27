#!/usr/bin/env python3
"""
Encryption Key Rotation Script (SGA3-L3)

Re-encrypts all encrypted database fields after a SECRET_KEY change.

Usage:
    # 1. Set both old and new keys
    # 2. Run from the backend directory:
    python -m scripts.rotate_encryption_key --old-key <OLD_SECRET_KEY>

    # Or with environment variables:
    OLD_SECRET_KEY=<old> SECRET_KEY=<new> python -m scripts.rotate_encryption_key

This script will:
    1. Derive Fernet instances from both old and new SECRET_KEYs
    2. Decrypt all encrypted fields with the old key
    3. Re-encrypt with the new key
    4. Verify decryption round-trip
    5. Update the database in a transaction (rollback on any error)

Tables with encrypted fields:
    - nostr_identities.encrypted_nsec  (Nostr private keys)
"""
import argparse
import asyncio
import base64
import hashlib
import logging
import os
import sys
from pathlib import Path

# Add the backend directory to the Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("rotate_encryption_key")


def _derive_fernet_from_key(secret_key: str, salt: bytes):
    """Derive a Fernet instance from a SECRET_KEY and salt."""
    from cryptography.fernet import Fernet

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        secret_key.encode("utf-8"),
        salt,
        iterations=600_000,
        dklen=32,
    )
    key = base64.urlsafe_b64encode(derived)
    return Fernet(key)


async def rotate_keys(old_key: str, new_key: str, dry_run: bool = False):
    """Rotate encryption keys for all encrypted database fields."""
    from cryptography.fernet import InvalidToken

    # Load the encryption salt
    salt_file = Path(
        os.environ.get(
            "ENCRYPTION_SALT_FILE",
            str(Path(__file__).resolve().parent.parent / ".encryption_salt"),
        )
    )

    if not salt_file.exists():
        logger.error("Encryption salt file not found at %s", salt_file)
        logger.error("Cannot proceed without the salt used to encrypt existing data.")
        return False

    salt = salt_file.read_bytes()
    if len(salt) < 16:
        logger.error("Salt file is too short (%d bytes)", len(salt))
        return False

    old_fernet = _derive_fernet_from_key(old_key, salt)
    new_fernet = _derive_fernet_from_key(new_key, salt)

    # Also try the legacy fixed salt
    legacy_salt = b"money-agents:field-encryption:v1"
    old_fernet_legacy = _derive_fernet_from_key(old_key, legacy_salt)

    # Connect to database
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        from app.core.config import settings
        database_url = settings.database_url

    # Convert sync URL to async if needed
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    success_count = 0
    error_count = 0

    async with async_session() as session:
        async with session.begin():
            # === Rotate nostr_identities.encrypted_nsec ===
            result = await session.execute(
                text("SELECT id, encrypted_nsec FROM nostr_identities WHERE encrypted_nsec IS NOT NULL")
            )
            rows = result.fetchall()
            logger.info("Found %d Nostr identities with encrypted keys", len(rows))

            for row in rows:
                row_id, ciphertext = row
                plaintext = None

                # Try decrypting with old key + current salt
                try:
                    plaintext = old_fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
                except InvalidToken:
                    pass

                # Try decrypting with old key + legacy salt
                if plaintext is None:
                    try:
                        plaintext = old_fernet_legacy.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
                    except InvalidToken:
                        pass

                if plaintext is None:
                    logger.error(
                        "FAILED to decrypt nostr_identities.id=%s — "
                        "old key may be incorrect or data is corrupt",
                        row_id,
                    )
                    error_count += 1
                    continue

                # Re-encrypt with new key
                new_ciphertext = new_fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

                # Verify round-trip
                verify = new_fernet.decrypt(new_ciphertext.encode("utf-8")).decode("utf-8")
                if verify != plaintext:
                    logger.error(
                        "Round-trip verification FAILED for nostr_identities.id=%s", row_id
                    )
                    error_count += 1
                    continue

                if not dry_run:
                    await session.execute(
                        text("UPDATE nostr_identities SET encrypted_nsec = :new_ct WHERE id = :id"),
                        {"new_ct": new_ciphertext, "id": row_id},
                    )

                success_count += 1
                logger.info(
                    "%s nostr_identities.id=%s",
                    "Would rotate" if dry_run else "Rotated",
                    row_id,
                )

            if error_count > 0:
                logger.error(
                    "Encountered %d errors — rolling back ALL changes", error_count
                )
                raise RuntimeError(f"Key rotation failed with {error_count} errors")

    await engine.dispose()

    logger.info(
        "Key rotation %s: %d fields %s, %d errors",
        "dry-run complete" if dry_run else "complete",
        success_count,
        "would be rotated" if dry_run else "rotated",
        error_count,
    )
    return error_count == 0


def main():
    parser = argparse.ArgumentParser(
        description="Rotate encryption keys for all encrypted database fields"
    )
    parser.add_argument(
        "--old-key",
        default=os.environ.get("OLD_SECRET_KEY"),
        help="The old SECRET_KEY to decrypt with (or set OLD_SECRET_KEY env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying the database",
    )
    args = parser.parse_args()

    old_key = args.old_key
    new_key = os.environ.get("SECRET_KEY", "")

    if not old_key:
        logger.error("Old key must be provided via --old-key or OLD_SECRET_KEY env var")
        sys.exit(1)

    if not new_key:
        logger.error("New key must be set via SECRET_KEY env var (or in .env)")
        sys.exit(1)

    if old_key == new_key:
        logger.error("Old and new keys are identical — nothing to rotate")
        sys.exit(1)

    logger.info("Starting key rotation%s...", " (DRY RUN)" if args.dry_run else "")
    success = asyncio.run(rotate_keys(old_key, new_key, dry_run=args.dry_run))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
