"""P1 encrypted opt-in biometric-template storage tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cryptography.fernet import Fernet

from brain.vision.biometric_store import (
    BiometricTemplateStore,
    TemplateStoreError,
)
from brain.vision.face_verification import BiometricConsent


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
SUBJECT = "sub_0123456789abcdef0123456789abcdef"


def consent(**overrides: object) -> BiometricConsent:
    document: dict[str, object] = {
        "subject_id": SUBJECT,
        "consent_record_id": "consent-0123456789abcdef",
        "granted_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=1),
    }
    return BiometricConsent(**{**document, **overrides})  # type: ignore[arg-type]


class BiometricTemplateStoreTests(unittest.TestCase):
    def test_enrolls_and_loads_only_for_active_matching_consent(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = BiometricTemplateStore(root, Fernet.generate_key())
            reference = store.enroll(
                consent(), model_id="research-arcface", model_version="r100-v1",
                template=b"private-template", enrolled_at=NOW,
            )

            loaded = store.load_for_verification(consent(), now=NOW)

            self.assertEqual(reference.subject_id, SUBJECT)
            self.assertEqual(loaded.template, b"private-template")
            self.assertNotIn(SUBJECT, "\n".join(path.name for path in root.iterdir()))
            encrypted = next(root.iterdir()).read_bytes()
            self.assertNotIn(b"private-template", encrypted)
            self.assertEqual(next(root.iterdir()).stat().st_mode & 0o777, 0o600)

    def test_refuses_enrollment_or_load_when_consent_is_not_active(self) -> None:
        revoked = consent(revoked_at=NOW - timedelta(seconds=1))
        with TemporaryDirectory() as temporary:
            store = BiometricTemplateStore(Path(temporary), Fernet.generate_key())

            with self.assertRaisesRegex(TemplateStoreError, "active consent"):
                store.enroll(revoked, model_id="research-arcface", model_version="r100-v1", template=b"private", enrolled_at=NOW)
            with self.assertRaisesRegex(TemplateStoreError, "active consent"):
                store.load_for_verification(revoked, now=NOW)

    def test_revocation_deletes_the_encrypted_template_and_prevents_reuse(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = BiometricTemplateStore(root, Fernet.generate_key())
            store.enroll(consent(), model_id="research-arcface", model_version="r100-v1", template=b"private", enrolled_at=NOW)
            revoked = consent(revoked_at=NOW + timedelta(seconds=1))

            self.assertTrue(store.revoke_and_delete(revoked, now=NOW + timedelta(seconds=1)))
            self.assertEqual(tuple(root.iterdir()), ())
            with self.assertRaisesRegex(TemplateStoreError, "active consent"):
                store.load_for_verification(revoked, now=NOW + timedelta(seconds=1))

    def test_rejects_mismatched_consent_and_implicit_template_replacement(self) -> None:
        with TemporaryDirectory() as temporary:
            store = BiometricTemplateStore(Path(temporary), Fernet.generate_key())
            store.enroll(consent(), model_id="research-arcface", model_version="r100-v1", template=b"private", enrolled_at=NOW)
            other = consent(consent_record_id="consent-fedcba9876543210")

            with self.assertRaisesRegex(TemplateStoreError, "already exists"):
                store.enroll(consent(), model_id="research-arcface", model_version="r100-v1", template=b"new", enrolled_at=NOW)
            with self.assertRaisesRegex(TemplateStoreError, "consent"):
                store.load_for_verification(other, now=NOW)

            revoked_other = consent(
                consent_record_id="consent-fedcba9876543210", revoked_at=NOW + timedelta(seconds=1),
            )
            with self.assertRaisesRegex(TemplateStoreError, "consent"):
                store.revoke_and_delete(revoked_other, now=NOW + timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()
