"""
Unit tests for Nostr Key Manager.

Tests Fernet encryption/decryption of nsec private keys.

NOTE: The nsec key used throughout these tests is a throwaway test key,
not associated with any real identity or funds.
"""
import pytest
from unittest.mock import patch, MagicMock
from pydantic import SecretStr


class TestNostrKeyManager:
    """Tests for encrypt_nsec / decrypt_nsec functions."""

    @pytest.fixture(autouse=True)
    def _mock_salt(self):
        """Mock the salt file to avoid PermissionError on /app/.encryption_salt."""
        with patch("app.core.encryption._load_or_create_salt", return_value=b"test_salt_value!" * 2):
            yield

    @patch("app.core.encryption.settings")
    def test_encrypt_decrypt_roundtrip(self, mock_settings):
        """Encrypted nsec can be decrypted back to original."""
        mock_settings.secret_key = SecretStr("test_secret_key_for_nostr_testing")

        # Reset the cached Fernet instance so it picks up our mock
        import app.core.encryption as enc_mod
        enc_mod._fernet = None

        from app.services.nostr_key_manager import encrypt_nsec, decrypt_nsec

        nsec = "nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5"
        encrypted = encrypt_nsec(nsec)

        # Encrypted form is different from plaintext
        assert encrypted != nsec
        assert len(encrypted) > len(nsec)

        # Decrypts back to original
        decrypted = decrypt_nsec(encrypted)
        assert decrypted == nsec

    @patch("app.core.encryption.settings")
    def test_different_keys_produce_different_ciphertext(self, mock_settings):
        """Different SECRET_KEYs produce different ciphertexts."""
        import app.core.encryption as enc_mod
        nsec = "nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5"

        mock_settings.secret_key = SecretStr("key_one")
        enc_mod._fernet = None
        from app.services.nostr_key_manager import encrypt_nsec
        enc1 = encrypt_nsec(nsec)

        mock_settings.secret_key = SecretStr("key_two")
        enc_mod._fernet = None
        enc2 = encrypt_nsec(nsec)

        assert enc1 != enc2

    @patch("app.core.encryption.settings")
    def test_wrong_key_fails_decrypt(self, mock_settings):
        """Decrypting with wrong SECRET_KEY raises ValueError."""
        import app.core.encryption as enc_mod

        mock_settings.secret_key = SecretStr("correct_key")
        enc_mod._fernet = None
        from app.services.nostr_key_manager import encrypt_nsec, decrypt_nsec

        nsec = "nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5"
        encrypted = encrypt_nsec(nsec)

        # Change key and reset cache
        mock_settings.secret_key = SecretStr("wrong_key")
        enc_mod._fernet = None
        with pytest.raises(ValueError, match="Cannot decrypt"):
            decrypt_nsec(encrypted)

    @patch("app.core.encryption.settings")
    def test_tampered_ciphertext_fails(self, mock_settings):
        """Tampered ciphertext raises ValueError."""
        import app.core.encryption as enc_mod

        mock_settings.secret_key = SecretStr("test_key")
        enc_mod._fernet = None
        from app.services.nostr_key_manager import encrypt_nsec, decrypt_nsec

        nsec = "nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5"
        encrypted = encrypt_nsec(nsec)

        # Tamper with ciphertext
        tampered = encrypted[:-5] + "XXXXX"
        with pytest.raises(ValueError, match="Cannot decrypt"):
            decrypt_nsec(tampered)

    @patch("app.core.encryption.settings")
    def test_encrypt_produces_string(self, mock_settings):
        """encrypt_nsec returns a string suitable for DB TEXT column."""
        import app.core.encryption as enc_mod

        mock_settings.secret_key = SecretStr("test_key")
        enc_mod._fernet = None
        from app.services.nostr_key_manager import encrypt_nsec

        nsec = "nsec1test"
        result = encrypt_nsec(nsec)
        assert isinstance(result, str)
        assert len(result) > 0
