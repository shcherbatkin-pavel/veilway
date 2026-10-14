"""Shared vocabulary verified against validation, persistence and HTTP."""
from datetime import timedelta
from pathlib import Path
import re
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from test_mobile import PREFIX, advance, scene  # scene is a reusable HTTP fixture
from veilway_control import models, schemas


CONTRACT = dict(
    line.split("=", 1) for line in
    (Path(__file__).parent / "fixtures/client-contract.properties").read_text().splitlines()
    if line and not line.startswith("#")
)


@pytest.mark.parametrize("key,annotation,table,constraint", [
    ("profile.modes", schemas.ProfileMode, models.VpnProfile, "ck_vpn_profile_mode"),
    ("profile.statuses", schemas.ProfileStatus, models.VpnProfile, "ck_vpn_profile_status"),
    ("session.roles", schemas.SessionResponse.model_fields["role"].annotation, models.User, "ck_user_role"),
    ("job.kinds", schemas.ProfileJobResponse.model_fields["kind"].annotation, models.ProfileJob, "ck_profile_job_kind"),
    ("job.statuses", schemas.ProfileJobResponse.model_fields["status"].annotation, models.ProfileJob, "ck_profile_job_status"),
])
def test_validation_and_constraints(key, annotation, table, constraint):
    values = CONTRACT[key].split(",")
    assert set(get_args(annotation)) == set(values)
    check = next(item for item in table.__table__.constraints if item.name == constraint)
    assert set(re.findall(r"'([^']+)'", str(check.sqltext))) == set(values)
    adapter = TypeAdapter(annotation)
    for value in values:
        assert adapter.validate_python(value) == value
    with pytest.raises(ValidationError):
        adapter.validate_python("unknown-synthetic-value")


@pytest.mark.parametrize("state,key,index", [
    ("early", "mobile.slow_down", 0),
    ("pending", "mobile.pending", 0),
    ("expired", "mobile.terminal", 0),
    ("denied", "mobile.terminal", 1),
])
def test_mobile_http_vocabulary(scene, db_factory, state, key, index):
    client, headers, user_id, _ = scene
    grant = client.post(PREFIX + "/auth/device").json()
    code = grant["device_code"]
    if state != "early":
        advance(db_factory, code)
    if state == "expired":
        advance(db_factory, code, expires_at=models.utcnow() - timedelta(seconds=1))
    elif state == "denied":
        assert client.post(PREFIX + "/auth/approve", headers=headers,
                           json={"user_code": grant["user_code"]}).status_code == 204
        with db_factory() as db:
            db.get(models.User, user_id).is_active = False
            db.commit()
    response = client.post(PREFIX + "/auth/token", json={"device_code": code})
    assert response.status_code == 400
    assert response.json() == {"error": CONTRACT[key].split(",")[index]}
    assert response.headers["cache-control"] == "no-store"
