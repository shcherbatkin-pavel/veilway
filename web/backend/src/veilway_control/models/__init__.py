"""Compatible model imports using the single database.Base registry.

Import domains eagerly in the original declaration order. Mobile tables remain
registered by mobile_models, as before; do not import that module here.
"""
from ..database import Base
from .shared import as_utc, utcnow
from .auth import (
    User,
    UserSession,
    OAuthLoginAttempt,
    GoogleAdminBinding,
)
from .profiles import VpnProfile, ProfileJob, ProfileAuditEvent
from .nodes import (
    VpnVm,
    VmHeartbeat,
    ALLOWED_VM_SLUGS,
    EXPECTED_VM_CONFIGURATION,
)
from .restart import RestartJob, RestartTarget
from .crl import CrlPublication, CrlAgent, CrlSyncState
from .analytics import (
    AnalyticsAgent,
    AnalyticsSource,
    ProfileOwnership,
    VpnSession,
    AnalyticsReceipt,
    ProfileUsage,
    AnalyticsOwner,
    DailyUsage,
    AnalyticsCoverage,
)

__all__ = [
    "Base",
    "as_utc",
    "utcnow",
    "User",
    "UserSession",
    "OAuthLoginAttempt",
    "GoogleAdminBinding",
    "VpnProfile",
    "ProfileJob",
    "ProfileAuditEvent",
    "VpnVm",
    "VmHeartbeat",
    "ALLOWED_VM_SLUGS",
    "EXPECTED_VM_CONFIGURATION",
    "RestartJob",
    "RestartTarget",
    "CrlPublication",
    "CrlAgent",
    "CrlSyncState",
    "AnalyticsAgent",
    "AnalyticsSource",
    "ProfileOwnership",
    "VpnSession",
    "AnalyticsReceipt",
    "ProfileUsage",
    "AnalyticsOwner",
    "DailyUsage",
    "AnalyticsCoverage",
]
