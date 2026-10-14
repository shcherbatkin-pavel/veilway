"""Golden baseline from main 409a2ff: a model move must not change the schema."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable


def snapshot():
    from veilway_control import models
    from veilway_control.database import Base

    registration = list(Base.metadata.tables)
    importlib.import_module("veilway_control.mobile_models")
    configure_mappers()

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def default(value):
        if value is None:
            return None
        arg = value.arg
        return [type(value).__name__, getattr(arg, "__name__", str(arg))]

    ddl = {}
    for dialect in (sqlite.dialect(), postgresql.dialect()):
        ddl[dialect.name] = [
            [str(CreateTable(table).compile(dialect=dialect)),
             sorted(str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes)]
            for table in Base.metadata.sorted_tables
        ]
    defaults = {
        table.name: [[column.name, default(column.default), default(column.onupdate)] for column in table.columns]
        for table in Base.metadata.sorted_tables
    }
    relationships = {
        mapper.class_.__name__: [
            [rel.key, rel.mapper.class_.__name__, rel.back_populates, str(rel.cascade),
             rel.uselist, rel.passive_deletes, str(rel.primaryjoin),
             [str(item) for item in rel.order_by] if rel.order_by is not False else []]
            for rel in mapper.relationships
        ]
        for mapper in Base.registry.mappers
    }
    return {"registration": registration, "ddl": digest(ddl), "defaults": digest(defaults),
            "relationships": digest(relationships), "vm_slugs": list(models.ALLOWED_VM_SLUGS),
            "vm_configuration": models.EXPECTED_VM_CONFIGURATION}


def test_model_baseline():
    # A fresh process excludes application imports from pytest collection.
    actual = json.loads(subprocess.check_output([sys.executable, __file__, "snapshot"], text=True))
    expected = json.loads((Path(__file__).parent / "fixtures/model-baseline.json").read_text())
    assert actual == expected


def test_import_orders_and_compatibility():
    for first in ("models", "mobile_models", "models.analytics", "models.profiles", "models.restart"):
        subprocess.run([sys.executable, "-c", f'''
import importlib
importlib.import_module("veilway_control.{first}")
from veilway_control import models, mobile_models
from veilway_control.database import Base
from sqlalchemy.orm import configure_mappers
configure_mappers()
assert models.Base is Base
assert len(Base.metadata.tables) == 26
for name in {list(['User','UserSession','OAuthLoginAttempt','GoogleAdminBinding','VpnProfile','ProfileJob','ProfileAuditEvent','VpnVm','VmHeartbeat','RestartJob','RestartTarget','CrlPublication','CrlAgent','CrlSyncState','AnalyticsAgent','AnalyticsSource','ProfileOwnership','VpnSession','AnalyticsReceipt','ProfileUsage','AnalyticsOwner','DailyUsage','AnalyticsCoverage'])!r}:
    assert getattr(models, name).__table__.metadata is Base.metadata
assert models.as_utc(models.utcnow()).tzinfo is not None
'''], check=True)


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2, sort_keys=True))
