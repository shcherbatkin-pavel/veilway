"""Publish a complete signed CRL and finalize only acknowledged revocations."""
from __future__ import annotations

import asyncio
import hashlib

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select

from .models import CrlAgent, CrlPublication, CrlSyncState, ProfileJob, VpnProfile, as_utc, utcnow
from .pki import PkiClient
from .profiles import audit
from .workers import StepObservation, run_periodic_step


def verify(pem, ca_pem, now):
    ca = x509.load_pem_x509_certificate(ca_pem)
    crl = x509.load_pem_x509_crl(pem)
    if (not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or not ca.extensions.get_extension_for_class(x509.KeyUsage).value.crl_sign
            or not ca.not_valid_before_utc <= now < ca.not_valid_after_utc
            or crl.issuer != ca.subject or not crl.is_signature_valid(ca.public_key())
            or crl.next_update_utc is None or not crl.last_update_utc <= now < crl.next_update_utc):
        raise ValueError("invalid CRL")
    # Only full, direct CRLs from our common CA are supported.
    for extension in crl.extensions:
        if isinstance(extension.value, (x509.DeltaCRLIndicator, x509.IssuingDistributionPoint)) or (
                extension.critical and not isinstance(extension.value, x509.CRLNumber)):
            raise ValueError("unsupported CRL")
    version = crl.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number
    if not 1 <= version < 2**63:
        raise ValueError("invalid version")
    return crl, version, hashlib.sha256(pem).hexdigest(), ca.fingerprint(hashes.SHA256()).hex()


def finalize(db, now):
    jobs = db.scalars(select(ProfileJob).join(VpnProfile).where(
        ProfileJob.kind == "revoke", ProfileJob.status == "succeeded",
        ProfileJob.crl_number.is_not(None), VpnProfile.status == "revoking"
    ).with_for_update(skip_locked=True, of=ProfileJob)).all()
    for job in jobs:
        profile = db.scalar(select(VpnProfile).where(VpnProfile.id == job.profile_id).with_for_update())
        if profile.status != "revoking":
            continue
        agent = db.get(CrlAgent, "aws-direct" if profile.mode == "aws-direct" else "yc-direct")
        if (agent is None or agent.acknowledged_version is None
                or agent.acknowledged_version < job.crl_number or agent.acknowledged_until is None
                or as_utc(agent.acknowledged_until) <= now):
            continue
        publication = db.get(CrlPublication, agent.acknowledged_version)
        if publication is None or publication.sha256 != agent.acknowledged_sha256:
            continue
        crl = x509.load_pem_x509_crl(publication.pem)
        if profile.certificate_serial is None or crl.get_revoked_certificate_by_serial_number(
                int(profile.certificate_serial, 16)) is None:
            continue
        profile.status = "revoked"
        audit(db, job.requested_by_id, "revoke_result", profile.id, "succeeded")


class CrlWorker:
    def __init__(self, settings, session_factory, client=None):
        self.settings, self.session_factory = settings, session_factory
        self.client = client or PkiClient(settings.pki_socket_path)
        self.stopping = asyncio.Event()
        self.observation = StepObservation()

    def stop(self):
        self.stopping.set()

    async def run(self):
        await run_periodic_step(
            self.step, self.stopping, self.settings.worker_interval_seconds, observation=self.observation,
            continue_on_error=True,
        )

    def step(self, *, now=None):
        now = now or utcnow()
        with self.session_factory() as db:
            state = db.scalar(select(CrlSyncState).where(CrlSyncState.id == 1).with_for_update(skip_locked=True))
            if state is None:
                return False
            state.attempted_at = now
            try:
                pem, ca = self.client.crl()
                crl, version, digest, ca_digest = verify(pem, ca, now)
                previous = db.scalar(select(CrlPublication).order_by(CrlPublication.version.desc()).limit(1))
                if previous is not None:
                    if ca_digest != previous.ca_sha256 or version < previous.version:
                        raise ValueError("rollback")
                    if version == previous.version and digest != previous.sha256:
                        raise ValueError("equivocation")
                    old_serials = {entry.serial_number for entry in x509.load_pem_x509_crl(previous.pem)}
                    if not old_serials.issubset({entry.serial_number for entry in crl}):
                        raise ValueError("incomplete CRL")
                if previous is None or version > previous.version:
                    db.add(CrlPublication(version=version, sha256=digest, ca_sha256=ca_digest,
                        pem=pem, this_update=crl.last_update_utc, next_update=crl.next_update_utc))
                    db.flush()
                state.error_code = None
            except Exception:
                state.error_code = "publication_unavailable"
            finalize(db, now)
            db.commit()
        return True
