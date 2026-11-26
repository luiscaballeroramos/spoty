import tempfile
import unittest
from pathlib import Path

from services.encrypted_listening_events import (
    decrypt_bytes,
    decrypt_file,
    encrypt_file,
)


class EncryptedListeningEventsTest(unittest.TestCase):
    def test_encrypt_and_decrypt_round_trip(self):
        key = bytes(range(32))
        plaintext = b"private listening history"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "listening_events.csv"
            encrypted = root / "listening_events.csv.enc"
            restored = root / "restored.csv"
            source.write_bytes(plaintext)

            self.assertTrue(encrypt_file(source, encrypted, key))
            self.assertNotIn(plaintext, encrypted.read_bytes())
            self.assertTrue(decrypt_file(encrypted, restored, key))
            self.assertEqual(restored.read_bytes(), plaintext)

    def test_reencrypting_unchanged_data_does_not_change_ciphertext(self):
        key = bytes(range(32))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.csv"
            encrypted = root / "source.csv.enc"
            source.write_bytes(b"same data")

            self.assertTrue(encrypt_file(source, encrypted, key))
            original = encrypted.read_bytes()
            self.assertFalse(encrypt_file(source, encrypted, key))
            self.assertEqual(encrypted.read_bytes(), original)

    def test_tampered_ciphertext_is_rejected(self):
        key = bytes(range(32))
        encrypted = bytearray(b"SPOTY-LEC\x01" + bytes(12) + bytes(16))
        encrypted[-1] ^= 1

        with self.assertRaisesRegex(ValueError, "authenticate"):
            decrypt_bytes(bytes(encrypted), key)
