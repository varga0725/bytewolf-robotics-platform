"""Private, encrypted P1 biometric template store.

This is an internal enrollment/verification persistence boundary, not an API.
It never exposes a template through dashboard artifacts, metadata journals or
public contracts. Callers must present active, matching opt-in consent both at
enrollment and before a template may be loaded for private verification.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .face_verification import BiometricConsent, ConsentState


TEMPLATE_STORE_V1 = "biometric_template.v1"


class TemplateStoreError(RuntimeError):
    """An opt-in biometric template cannot be safely persisted or retrieved."""


@dataclass(frozen=True)
class TemplateReference:
    """Metadata-only enrollment reference; never contains template bytes."""

    subject_id: str
    consent_record_id: str
    model_id: str
    model_version: str
    enrolled_at: datetime


@dataclass(frozen=True)
class _PrivateTemplate:
    reference: TemplateReference
    template: bytes


class BiometricTemplateStore:
    """One local encrypted template directory with explicit deletion on revocation."""

    def __init__(self, directory: Path, key: bytes) -> None:
        if not isinstance(directory, Path) or not directory.name:
            raise TemplateStoreError("Biometric template directory must be a concrete path.")
        if not isinstance(key, bytes) or not key:
            raise TemplateStoreError("Biometric template encryption requires a non-empty Fernet key.")
        try:
            from cryptography.fernet import Fernet
        except ImportError as error:  # pragma: no cover - deployment guard
            raise TemplateStoreError("Biometric template encryption requires the approved cryptography runtime.") from error
        try:
            self._fernet = Fernet(key)
        except (TypeError, ValueError) as error:
            raise TemplateStoreError("Biometric template encryption key is invalid.") from error
        self._directory = directory

    def enroll(
        self,
        consent: BiometricConsent,
        *,
        model_id: str,
        model_version: str,
        template: bytes,
        enrolled_at: datetime,
    ) -> TemplateReference:
        """Encrypt one opt-in template; replacement requires deletion/re-enrollment."""
        self._require_active_consent(consent, enrolled_at)
        if not isinstance(model_id, str) or not model_id.strip() or not isinstance(model_version, str) or not model_version.strip():
            raise TemplateStoreError("Biometric enrollment requires model ID and version.")
        if not isinstance(template, bytes) or not template:
            raise TemplateStoreError("Biometric enrollment template must be non-empty bytes.")
        reference = TemplateReference(consent.subject_id, consent.consent_record_id, model_id, model_version, enrolled_at)
        target = self._target(consent.subject_id)
        if target.exists():
            raise TemplateStoreError("Biometric template already exists; delete it before re-enrollment.")
        document = {
            "contract_version": TEMPLATE_STORE_V1,
            "subject_id": reference.subject_id,
            "consent_record_id": reference.consent_record_id,
            "model_id": reference.model_id,
            "model_version": reference.model_version,
            "enrolled_at": enrolled_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "template_base64": base64.b64encode(template).decode("ascii"),
        }
        try:
            plaintext = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise TemplateStoreError("Biometric enrollment metadata is invalid.") from error
        self._atomic_encrypt(target, plaintext)
        return reference

    def load_for_verification(self, consent: BiometricConsent, *, now: datetime) -> _PrivateTemplate:
        """Decrypt a template only for an active, matching consent record."""
        self._require_active_consent(consent, now)
        target = self._target(consent.subject_id)
        if not target.is_file():
            raise TemplateStoreError("No biometric template is enrolled for this consent.")
        try:
            plaintext = self._fernet.decrypt(target.read_bytes())
            document = json.loads(plaintext)
            reference = TemplateReference(
                document["subject_id"], document["consent_record_id"], document["model_id"], document["model_version"],
                datetime.fromisoformat(document["enrolled_at"].replace("Z", "+00:00")),
            )
            template = base64.b64decode(document["template_base64"], validate=True)
        except Exception as error:
            raise TemplateStoreError("Encrypted biometric template cannot be verified or decoded.") from error
        if reference.subject_id != consent.subject_id or reference.consent_record_id != consent.consent_record_id:
            raise TemplateStoreError("Encrypted biometric template consent does not match the active consent record.")
        if not template:
            raise TemplateStoreError("Encrypted biometric template is empty.")
        return _PrivateTemplate(reference, template)

    def revoke_and_delete(self, consent: BiometricConsent, *, now: datetime) -> bool:
        """Delete local encrypted template only after explicit effective revocation."""
        if not isinstance(consent, BiometricConsent) or consent.state(now) is not ConsentState.REVOKED:
            raise TemplateStoreError("Biometric template deletion by revocation requires revoked consent.")
        target = self._target(consent.subject_id)
        if not target.exists():
            return False
        if not target.is_file():
            raise TemplateStoreError("Biometric template target is not a regular file.")
        try:
            document = json.loads(self._fernet.decrypt(target.read_bytes()))
            if document.get("subject_id") != consent.subject_id or document.get("consent_record_id") != consent.consent_record_id:
                raise TemplateStoreError("Encrypted biometric template consent does not match the revoked consent record.")
            target.unlink()
        except TemplateStoreError:
            raise
        except Exception as error:
            raise TemplateStoreError("Encrypted biometric template could not be deleted.") from error
        return True

    def _require_active_consent(self, consent: BiometricConsent, now: datetime) -> None:
        if not isinstance(consent, BiometricConsent) or not consent.allows_verification(now):
            raise TemplateStoreError("Biometric template operation requires active consent.")

    def _target(self, subject_id: str) -> Path:
        digest = hashlib.sha256(subject_id.encode("ascii")).hexdigest()
        target = self._directory / f"{digest}.template"
        root = self._directory.resolve()
        try:
            target.resolve().relative_to(root)
        except ValueError as error:  # defensive even though digest is fixed-length hex
            raise TemplateStoreError("Biometric template target escapes its directory.") from error
        return target

    def _atomic_encrypt(self, target: Path, plaintext: bytes) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        token = self._fernet.encrypt(plaintext)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self._directory, prefix=f".{target.name}.", suffix=".tmp", delete=False) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary_name, 0o600)
                temporary.write(token)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
            os.chmod(target, 0o600)
        except OSError as error:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise TemplateStoreError("Encrypted biometric template could not be persisted.") from error
