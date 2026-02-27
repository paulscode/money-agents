"""
Unit tests for Nostr event building and signing.

Tests _build_and_sign, _build_text_note, _build_article, _build_metadata_event,
_build_zap_request with real pynostr keypairs — no mocks needed for crypto.
"""
import json
import time
import pytest

pynostr = pytest.importorskip("pynostr", reason="pynostr not installed")

from pynostr.key import PrivateKey
from pynostr.event import Event

from app.services.nostr_service import NostrService, KIND_TEXT_NOTE, KIND_METADATA, KIND_LONG_FORM, KIND_REACTION, KIND_REPOST, KIND_DELETE, KIND_CONTACTS, KIND_ZAP_REQUEST


@pytest.fixture
def service():
    """Create a NostrService with mocked settings."""
    from unittest.mock import patch, MagicMock
    with patch("app.services.nostr_service.settings") as mock_settings:
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"
        mock_settings.nostr_lightning_address = ""
        svc = NostrService()
        yield svc


@pytest.fixture
def keypair():
    """Generate a fresh keypair for testing."""
    pk = PrivateKey()
    return pk


class TestBuildAndSign:
    """Tests for the core _build_and_sign method."""

    def test_returns_valid_event_dict(self, service, keypair):
        """Signed event dict has all required NIP-01 fields."""
        event = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="hello")
        assert "id" in event
        assert "pubkey" in event
        assert "created_at" in event
        assert "kind" in event
        assert "tags" in event
        assert "content" in event
        assert "sig" in event

    def test_event_id_is_64_hex(self, service, keypair):
        """Event ID is a 64-character hex string (SHA256)."""
        event = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="test")
        assert len(event["id"]) == 64
        assert all(c in "0123456789abcdef" for c in event["id"])

    def test_event_sig_is_128_hex(self, service, keypair):
        """Event signature is a 128-character hex string."""
        event = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="test")
        assert len(event["sig"]) == 128
        assert all(c in "0123456789abcdef" for c in event["sig"])

    def test_pubkey_matches_keypair(self, service, keypair):
        """Event pubkey matches the signing keypair's public key."""
        event = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="test")
        assert event["pubkey"] == keypair.public_key.hex()

    def test_kind_set_correctly(self, service, keypair):
        """Event kind matches the requested kind."""
        event = service._build_and_sign(keypair, kind=KIND_REACTION, content="+")
        assert event["kind"] == KIND_REACTION

    def test_content_set_correctly(self, service, keypair):
        """Event content matches the input."""
        event = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="Hello Nostr!")
        assert event["content"] == "Hello Nostr!"

    def test_tags_included(self, service, keypair):
        """Tags are included in the signed event."""
        tags = [["e", "abc123"], ["p", "def456"]]
        event = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="test", tags=tags)
        assert ["e", "abc123"] in event["tags"]
        assert ["p", "def456"] in event["tags"]

    def test_no_tags_defaults_to_empty(self, service, keypair):
        """Missing tags defaults to empty list."""
        event = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="test")
        assert event["tags"] == []

    def test_created_at_is_recent(self, service, keypair):
        """Event timestamp is within a few seconds of now."""
        now = int(time.time())
        event = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="test")
        assert abs(event["created_at"] - now) < 5

    def test_different_content_different_ids(self, service, keypair):
        """Different content produces different event IDs."""
        e1 = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="test1")
        e2 = service._build_and_sign(keypair, kind=KIND_TEXT_NOTE, content="test2")
        assert e1["id"] != e2["id"]

    def test_empty_content_allowed(self, service, keypair):
        """Events can have empty content (e.g., reposts)."""
        event = service._build_and_sign(keypair, kind=KIND_REPOST, content="", tags=[["e", "abc"]])
        assert event["content"] == ""
        assert event["id"]  # Still has valid ID


class TestBuildTextNote:
    """Tests for _build_text_note."""

    def test_basic_note(self, service, keypair):
        """Basic text note has kind 1 and correct content."""
        event = service._build_text_note(keypair, content="Hello world")
        assert event["kind"] == KIND_TEXT_NOTE
        assert event["content"] == "Hello world"

    def test_hashtags_as_t_tags(self, service, keypair):
        """Hashtags are added as 't' tags, lowercase, stripped of #."""
        event = service._build_text_note(keypair, content="test", hashtags=["Bitcoin", "#Lightning"])
        t_tags = [t for t in event["tags"] if t[0] == "t"]
        assert ["t", "bitcoin"] in t_tags
        assert ["t", "lightning"] in t_tags

    def test_reply_adds_e_tag(self, service, keypair):
        """Replying adds an 'e' tag with the reply marker."""
        reply_id = "a" * 64
        event = service._build_text_note(keypair, content="Great post!", reply_to=reply_id)
        e_tags = [t for t in event["tags"] if t[0] == "e"]
        assert len(e_tags) == 1
        assert e_tags[0][1] == reply_id
        assert e_tags[0][3] == "reply"

    def test_no_hashtags_no_t_tags(self, service, keypair):
        """Without hashtags, no 't' tags are present."""
        event = service._build_text_note(keypair, content="test")
        t_tags = [t for t in event["tags"] if t[0] == "t"]
        assert len(t_tags) == 0

    def test_reply_with_hashtags(self, service, keypair):
        """Reply with hashtags includes both e and t tags."""
        event = service._build_text_note(
            keypair, content="reply", reply_to="b" * 64, hashtags=["nostr"]
        )
        e_tags = [t for t in event["tags"] if t[0] == "e"]
        t_tags = [t for t in event["tags"] if t[0] == "t"]
        assert len(e_tags) == 1
        assert len(t_tags) == 1


class TestBuildArticle:
    """Tests for _build_article."""

    def test_article_kind(self, service, keypair):
        """Articles are kind 30023."""
        event = service._build_article(keypair, title="Test", content="Body")
        assert event["kind"] == KIND_LONG_FORM

    def test_article_has_d_tag(self, service, keypair):
        """Article has a 'd' tag for deduplication."""
        event = service._build_article(keypair, title="My Article", content="Body")
        d_tags = [t for t in event["tags"] if t[0] == "d"]
        assert len(d_tags) == 1
        assert d_tags[0][1] == "my-article"  # Lowercase, hyphenated

    def test_article_title_tag(self, service, keypair):
        """Article has a 'title' tag."""
        event = service._build_article(keypair, title="Bitcoin Guide", content="Body")
        title_tags = [t for t in event["tags"] if t[0] == "title"]
        assert len(title_tags) == 1
        assert title_tags[0][1] == "Bitcoin Guide"

    def test_article_summary_tag(self, service, keypair):
        """Article includes summary tag when provided."""
        event = service._build_article(
            keypair, title="Test", content="Body", summary="A summary"
        )
        summary_tags = [t for t in event["tags"] if t[0] == "summary"]
        assert len(summary_tags) == 1
        assert summary_tags[0][1] == "A summary"

    def test_article_image_tag(self, service, keypair):
        """Article includes image tag when provided."""
        event = service._build_article(
            keypair, title="Test", content="Body", image="https://example.com/img.png"
        )
        image_tags = [t for t in event["tags"] if t[0] == "image"]
        assert len(image_tags) == 1
        assert image_tags[0][1] == "https://example.com/img.png"

    def test_article_no_summary_when_empty(self, service, keypair):
        """No summary tag when summary is empty."""
        event = service._build_article(keypair, title="Test", content="Body")
        summary_tags = [t for t in event["tags"] if t[0] == "summary"]
        assert len(summary_tags) == 0

    def test_article_hashtags(self, service, keypair):
        """Article hashtags as 't' tags."""
        event = service._build_article(
            keypair, title="Test", content="Body", hashtags=["bitcoin", "privacy"]
        )
        t_tags = [t for t in event["tags"] if t[0] == "t"]
        assert len(t_tags) == 2

    def test_d_tag_truncated_at_50_chars(self, service, keypair):
        """Long titles produce d-tags truncated at 50 characters."""
        long_title = "A Very Long Article Title That Exceeds Fifty Characters In Total Length"
        event = service._build_article(keypair, title=long_title, content="Body")
        d_tags = [t for t in event["tags"] if t[0] == "d"]
        assert len(d_tags[0][1]) <= 50


class TestBuildMetadataEvent:
    """Tests for _build_metadata_event."""

    def test_metadata_kind_0(self, service, keypair):
        """Profile metadata is kind 0."""
        event = service._build_metadata_event(keypair, name="Alice", about="Bio")
        assert event["kind"] == KIND_METADATA

    def test_metadata_json_content(self, service, keypair):
        """Metadata content is valid JSON with name and about."""
        event = service._build_metadata_event(keypair, name="Alice", about="A test user")
        data = json.loads(event["content"])
        assert data["name"] == "Alice"
        assert data["about"] == "A test user"

    def test_metadata_optional_fields(self, service, keypair):
        """Optional fields included when non-empty."""
        event = service._build_metadata_event(
            keypair, name="Bob", about="Bio",
            picture="https://example.com/avatar.png",
            nip05="bob@example.com",
            lud16="bob@walletofsatoshi.com",
        )
        data = json.loads(event["content"])
        assert data["picture"] == "https://example.com/avatar.png"
        assert data["nip05"] == "bob@example.com"
        assert data["lud16"] == "bob@walletofsatoshi.com"

    def test_metadata_empty_optional_fields_omitted(self, service, keypair):
        """Empty optional fields are not included in JSON."""
        event = service._build_metadata_event(keypair, name="Alice", about="Bio")
        data = json.loads(event["content"])
        assert "picture" not in data
        assert "nip05" not in data
        assert "lud16" not in data


class TestBuildZapRequest:
    """Tests for _build_zap_request."""

    def test_zap_request_kind(self, service, keypair):
        """Zap request is kind 9734."""
        event = service._build_zap_request(
            keypair,
            sender_pubkey="a" * 64,
            recipient_pubkey="b" * 64,
            amount_sats=1000,
            comment="Great post!",
            relays=["wss://relay.test.io"],
        )
        assert event["kind"] == KIND_ZAP_REQUEST

    def test_zap_request_p_tag(self, service, keypair):
        """Zap request has 'p' tag with recipient pubkey."""
        recipient = "b" * 64
        event = service._build_zap_request(
            keypair, "a" * 64, recipient, 1000, "", ["wss://relay.test.io"]
        )
        p_tags = [t for t in event["tags"] if t[0] == "p"]
        assert len(p_tags) == 1
        assert p_tags[0][1] == recipient

    def test_zap_request_amount_in_millisats(self, service, keypair):
        """Zap request 'amount' tag is in millisats (sats × 1000)."""
        event = service._build_zap_request(
            keypair, "a" * 64, "b" * 64, 500, "", ["wss://relay.test.io"]
        )
        amount_tags = [t for t in event["tags"] if t[0] == "amount"]
        assert len(amount_tags) == 1
        assert amount_tags[0][1] == "500000"  # 500 sats = 500000 msats

    def test_zap_request_relays_tag(self, service, keypair):
        """Zap request includes relays tag (max 5)."""
        relays = ["wss://r1.io", "wss://r2.io", "wss://r3.io"]
        event = service._build_zap_request(
            keypair, "a" * 64, "b" * 64, 100, "", relays
        )
        relay_tags = [t for t in event["tags"] if t[0] == "relays"]
        assert len(relay_tags) == 1
        assert "wss://r1.io" in relay_tags[0]

    def test_zap_request_comment_as_content(self, service, keypair):
        """Zap comment is set as event content."""
        event = service._build_zap_request(
            keypair, "a" * 64, "b" * 64, 100, "Nice work!", ["wss://relay.test.io"]
        )
        assert event["content"] == "Nice work!"

    def test_zap_request_empty_comment(self, service, keypair):
        """Empty comment produces empty content."""
        event = service._build_zap_request(
            keypair, "a" * 64, "b" * 64, 100, "", ["wss://relay.test.io"]
        )
        assert event["content"] == ""


class TestParseZapReceipt:
    """Tests for _parse_zap_receipt."""

    def test_valid_receipt(self, service):
        """Parses a valid kind-9735 zap receipt."""
        zap_request_json = json.dumps({
            "pubkey": "sender" + "a" * 52,
            "tags": [["amount", "100000"]],
        })
        receipt = {
            "id": "r" * 64,
            "pubkey": "z" * 64,
            "created_at": int(time.time()) - 300,
            "kind": 9735,
            "tags": [
                ["bolt11", "lnbc1000..."],
                ["description", zap_request_json],
            ],
        }
        result = service._parse_zap_receipt(receipt)
        assert result is not None
        assert result["amount_sats"] == 100  # 100000 msats / 1000

    def test_receipt_with_zero_amount_returns_none(self, service):
        """Receipt with zero amount returns None."""
        receipt = {
            "id": "r" * 64,
            "pubkey": "z" * 64,
            "created_at": int(time.time()),
            "kind": 9735,
            "tags": [["bolt11", "lnbc..."]],
        }
        result = service._parse_zap_receipt(receipt)
        assert result is None

    def test_receipt_invalid_description_json(self, service):
        """Malformed description JSON doesn't crash."""
        receipt = {
            "id": "r" * 64,
            "pubkey": "z" * 64,
            "created_at": int(time.time()),
            "kind": 9735,
            "tags": [
                ["bolt11", "lnbc..."],
                ["description", "not valid json"],
            ],
        }
        result = service._parse_zap_receipt(receipt)
        assert result is None
