"""
Unit tests for utils/hashing.py

Hashing is how the pipeline decides "have I seen this exact file / record
before?". Two guarantees matter and are tested here:

  1. The same content always produces the same hash (so duplicates are caught).
  2. Different content produces a different hash (so real changes are noticed).
"""

import hashlib

import pytest

from utils.hashing import calculate_file_hash, calculate_record_hash

pytestmark = pytest.mark.unit


class TestRecordHash:
    def test_same_content_same_hash(self):
        record = {"name": "ACME", "country": "US"}
        assert calculate_record_hash(record) == calculate_record_hash(record)

    def test_key_order_does_not_change_hash(self):
        # sort_keys=True means the fields can arrive in any order
        a = {"name": "ACME", "country": "US"}
        b = {"country": "US", "name": "ACME"}
        assert calculate_record_hash(a) == calculate_record_hash(b)

    def test_different_content_different_hash(self):
        a = {"name": "ACME", "country": "US"}
        b = {"name": "ACME", "country": "GB"}
        assert calculate_record_hash(a) != calculate_record_hash(b)

    def test_is_sha256_hex(self):
        digest = calculate_record_hash({"x": 1})
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


class TestFileHash:
    def test_matches_hashlib_reference(self, tmp_path):
        # tmp_path is a pytest-provided throwaway directory, unique per test
        content = b"hello watchlist\n"
        file_path = tmp_path / "sample.txt"
        file_path.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        assert calculate_file_hash(file_path) == expected

    def test_different_files_differ(self, tmp_path):
        first = tmp_path / "a.txt"
        second = tmp_path / "b.txt"
        first.write_bytes(b"one")
        second.write_bytes(b"two")

        assert calculate_file_hash(first) != calculate_file_hash(second)
