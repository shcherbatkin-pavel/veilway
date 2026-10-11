from datetime import timedelta
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from test_api import build_client, login
from test_migrations import postgres_connection
from veilway_control.analytics_ingest import AccountingReport, ingest
from veilway_control.analytics_api import build_business_overview
from veilway_control.config import Settings
from veilway_control.models import (AnalyticsAgent, AnalyticsReceipt, AnalyticsSource, DailyUsage,
    ProfileAuditEvent, ProfileOwnership, ProfileUsage, User, VpnProfile, VpnSession, utcnow)
from veilway_control.security import hash_token


@pytest.fixture
def scene(db_factory,seed_control_data):
    seed=seed_control_data()
    now=utcnow().replace(microsecond=0)
    with db_factory() as db:
        profile=VpnProfile(device_name='Synthetic device',mode='aws-direct',owner_id=seed['user_id'],
            created_by_id=seed['user_id'],status='active',certificate_serial='7A',
            created_at=now-timedelta(days=10),expires_at=now+timedelta(days=5))
        db.add(profile);db.flush()
        db.add(ProfileOwnership(profile_id=profile.id,owner_id=seed['user_id'],assigned_at=profile.created_at))
        for node,token,modes in [('aws-direct','A'*40,['aws-direct']),('yc-direct','Y'*40,['yc-direct','yc-aws-multihop'])]:
            db.add(AnalyticsAgent(slug=node,token_hash=hash_token(token)));db.flush()
            for mode in modes:db.add(AnalyticsSource(mode=mode,node=node,configured_at=now-timedelta(days=20),available=False,capture_errors=0))
        db.commit()
        return dict(now=now,profile=profile.id,owner=seed['user_id'],epoch=uuid.uuid4(),session=uuid.uuid4())


def data(scene, *, started=None, observed=None, rx=0, tx=0):
    return dict(session_id=str(scene['session']),epoch=str(scene['epoch']),mode='aws-direct',
        certificate_serial='7A',profile_id=None,started_at=(started or scene['now']-timedelta(seconds=10)).isoformat(),
        observed_at=(observed or scene['now']).isoformat(),bytes_received=rx,bytes_sent=tx)


def event(scene,kind,**kwargs):return dict(data(scene,**kwargs),id=str(uuid.uuid4()),kind=kind)


def frame(scene,rows=None,*,status='available',observed=None):
    return dict(mode='aws-direct',status=status,epoch=str(scene['epoch']),
        observed_at=(observed or scene['now']).isoformat(),connection_count=len(rows or []) if status=='available' else None,sessions=rows or [])


def apply(db_factory,scene,events=None,reports=None,now=None):
    with db_factory() as db:
        ingest(db,db.get(AnalyticsAgent,'aws-direct'),AccountingReport(events=events or [],sources=reports or [frame(scene)]),Settings(),now or scene['now'])


def test_session_bytes_replay_and_out_of_order_do_not_reopen(db_factory,scene):
    start=event(scene,'start',observed=scene['now']-timedelta(seconds=10))
    apply(db_factory,scene,[start],[frame(scene,[data(scene,rx=10,tx=20)])])
    apply(db_factory,scene,[start],[frame(scene,[data(scene,rx=10,tx=20)])])
    end=event(scene,'end',rx=30,tx=50)
    apply(db_factory,scene,[end])
    apply(db_factory,scene,[end])
    apply(db_factory,scene,[start],[frame(scene,[data(scene,rx=5,tx=5,observed=scene['now']-timedelta(seconds=1))],observed=scene['now']-timedelta(seconds=1))])
    with db_factory() as db:
        session=db.get(VpnSession,scene['session'])
        assert session.state=='closed' and session.duration_seconds==10 and not session.present
        usage=db.get(ProfileUsage,scene['profile'])
        assert (usage.sessions,usage.bytes_received,usage.bytes_sent)==(1,30,50)
        assert len(db.scalars(select(AnalyticsReceipt)).all())==2
        summary=build_business_overview(db,Settings(),scene['now'],30,'aws-direct')
        assert summary.online.connections==0
        assert summary.activity.bytes_received==30 and summary.activity.bytes_sent==50
        assert summary.activity.average_duration_seconds==10
    changed=dict(end,bytes_sent=51)
    with pytest.raises(HTTPException) as conflict:apply(db_factory,scene,[changed])
    assert conflict.value.status_code==409


def test_freshness_unavailable_and_empty_are_different(db_factory,scene):
    apply(db_factory,scene,reports=[frame(scene,[data(scene)])])
    with db_factory() as db:
        assert build_business_overview(db,Settings(),scene['now'],7,'aws-direct').online.users==1
        assert build_business_overview(db,Settings(),scene['now']+timedelta(seconds=46),7,'aws-direct').online.connections is None
    apply(db_factory,scene,reports=[frame(scene,status='unavailable')])
    with db_factory() as db:
        assert build_business_overview(db,Settings(),scene['now'],7,'aws-direct').online.connections is None
    apply(db_factory,scene,reports=[frame(scene,observed=scene['now']+timedelta(seconds=1))],now=scene['now']+timedelta(seconds=1))
    with db_factory() as db:
        overview=build_business_overview(db,Settings(),scene['now']+timedelta(seconds=1),7,'aws-direct')
        assert overview.online.connections==0
        assert overview.online.complete
        assert build_business_overview(db,Settings(),scene['now'],7).online.connections is None


def test_short_sessions_funnel_and_reconnect(db_factory,scene):
    first=scene['now']-timedelta(days=3)
    # Includes a session entirely between snapshots and one rapid reconnect.
    apply(db_factory,scene,[event(scene,'start',started=first,observed=first),
        event(scene,'end',started=first,observed=first+timedelta(seconds=2),rx=100,tx=200)])
    other=dict(scene,session=uuid.uuid4())
    apply(db_factory,scene,[event(other,'start',started=first+timedelta(seconds=3),observed=first+timedelta(seconds=3))])
    with db_factory() as db:
        source=db.get(AnalyticsSource,'aws-direct');source.first_observed_at=scene['now']-timedelta(days=20)
        db.add(ProfileAuditEvent(actor_id=scene['owner'],object_id=scene['profile'],action='download',result='succeeded'))
        db.commit()
        result=build_business_overview(db,Settings(),scene['now'],30,'aws-direct')
        assert result.activity.sessions_started==2 and result.activity.reconnects==1
        assert result.funnel.issued==result.funnel.download_recorded==result.funnel.connected==1
        assert result.funnel.activation_7d_percent==100
        assert result.funnel.average_first_connection_seconds==7*86400
        assert result.lifecycle.used_expiring_7d==1
        assert result.activity.wau==1 and result.activity.dau==0


def test_auth_privacy_and_scope(db_factory,scene):
    with build_client(db_factory) as client:
        assert client.get('/api/v1/analytics/overview').status_code==401
        assert client.post('/api/v1/analytics-agents/aws-direct/report',json=dict(events=[],sources=[frame(scene)])).status_code==401
        login(client)
        payload=dict(events=[],sources=[frame(scene,[data(scene,rx=3,tx=4)])])
        assert client.post('/api/v1/analytics-agents/aws-direct/report',json=payload,headers={'Authorization':'Bearer '+'A'*40}).status_code==204
        payload['sources'][0]['mode']='yc-direct';payload['sources'][0]['sessions']=[];payload['sources'][0]['connection_count']=0
        assert client.post('/api/v1/analytics-agents/aws-direct/report',json=payload,headers={'Authorization':'Bearer '+'A'*40}).status_code==403
        for url in ('overview?mode=aws-direct','profiles','sessions'):
            response=client.get('/api/v1/analytics/'+url)
            assert response.status_code==200 and response.headers['Cache-Control']=='no-store'
            for forbidden in ('certificate_serial','token_hash','epoch','trusted_ip','Real Address','PRIVATE KEY'):
                assert forbidden not in response.text
        assert client.get('/api/v1/analytics/profiles?online_only=true').json()['total']==1
        assert client.get('/api/v1/analytics/sessions?limit=101').status_code==422
        with db_factory() as db:db.get(User,scene['owner']).role='USER';db.commit()
        for url in ('overview','profiles','sessions'):
            assert client.get('/api/v1/analytics/'+url).status_code==403


def test_unknown_serial_and_historical_ownership(db_factory,scene):
    unknown=dict(data(scene),certificate_serial='999',session_id=str(uuid.uuid4()))
    apply(db_factory,scene,reports=[frame(scene,[unknown])])
    with db_factory() as db:
        result=build_business_overview(db,Settings(),scene['now'],7,'aws-direct')
        assert result.online.connections==1 and result.online.unmatched_connections==1
        assert result.activity.users==0
        db.execute(__import__('sqlalchemy').delete(ProfileOwnership));db.commit()
    apply(db_factory,scene,[event(scene,'start')],[frame(scene,[data(scene,observed=scene['now']+timedelta(seconds=1))],observed=scene['now']+timedelta(seconds=1))],now=scene['now']+timedelta(seconds=1))
    with db_factory() as db:
        assert db.get(VpnSession,scene['session']).owner_id is None


def test_retention_does_not_erase_lifetime_or_reingest_obsolete_events(db_factory,scene):
    first=scene['now']-timedelta(days=89)
    apply(db_factory,scene,[event(scene,'start',started=first,observed=first),event(scene,'end',started=first,observed=first+timedelta(seconds=2),rx=12,tx=34)])
    obsolete=event(dict(scene,session=uuid.uuid4()),'end',started=first-timedelta(days=5),observed=first-timedelta(days=4))
    apply(db_factory,scene,[obsolete],now=scene['now']+timedelta(days=2))
    with db_factory() as db:
        assert db.get(VpnSession,scene['session']) is None
        assert db.get(ProfileUsage,scene['profile']).bytes_sent==34
        assert db.scalar(select(__import__('sqlalchemy').func.sum(DailyUsage.bytes_sent)))==34


def test_chunked_upload_limit_before_json_parsing():
    import asyncio
    from veilway_control.analytics_body import AnalyticsBodyLimit
    async def scenario():
        called=[];messages=[]
        async def app(*args):called.append(True)
        incoming=iter([{'type':'http.request','body':b'123','more_body':True},{'type':'http.request','body':b'456','more_body':False}])
        async def receive():return next(incoming)
        async def send(message):messages.append(message)
        await AnalyticsBodyLimit(app,limit=5)({'type':'http','path':'/api/v1/analytics-agents/aws-direct/report'},receive,send)
        assert not called and messages[0]['status']==413
        assert (b'cache-control',b'no-store') in messages[0]['headers']
    asyncio.run(scenario())


def test_hourly_retention_without_agent_upload(db_factory,scene):
    from veilway_control.analytics_retention import AnalyticsRetentionWorker
    first=scene['now']-timedelta(days=89)
    apply(db_factory,scene,[event(scene,'end',started=first,observed=first+timedelta(seconds=2),rx=12,tx=34)])
    with db_factory() as db:
        session=db.get(VpnSession,scene['session']);session.last_seen_at=scene['now']-timedelta(days=100);session.present=True
        db.commit()
    worker=AnalyticsRetentionWorker(Settings(),db_factory)
    worker.step()
    with db_factory() as db:
        assert db.get(VpnSession,scene['session']) is None
        assert db.get(ProfileUsage,scene['profile']).bytes_sent==34


def test_sync_agent_token_separation_and_mode_validation(db_factory,scene,monkeypatch):
    from veilway_control import cli
    monkeypatch.setattr(cli,'get_session_factory',lambda:db_factory)
    monkeypatch.setattr(cli,'read_json_stdin',lambda:{'aws-direct':{'token':'a'*40,'modes':['aws-direct']}})
    # This synthetic credential belongs to the heartbeat fixture.
    with pytest.raises(SystemExit,match='separate'):cli.sync_analytics_agents()
    monkeypatch.setattr(cli,'read_json_stdin',lambda:{'aws-direct':{'token':'N'*40,'modes':['aws-direct']}})
    cli.sync_analytics_agents()
    with db_factory() as db:
        assert db.get(AnalyticsAgent,'aws-direct').token_hash==hash_token('N'*40)
    monkeypatch.setattr(cli,'read_json_stdin',lambda:{'aws-direct':{'token':'N'*40,'modes':['yc-direct']}})
    with pytest.raises(SystemExit,match='ingress'):cli.sync_analytics_agents()


def test_real_postgres_accounting_and_migration_guard(postgres_connection):
    from alembic import command
    from sqlalchemy.orm import sessionmaker
    from test_migrations import config
    connection=postgres_connection
    cfg=config(connection);command.upgrade(cfg,'head')
    factory=sessionmaker(bind=connection,expire_on_commit=False)
    now=utcnow().replace(microsecond=0)
    with factory() as db:
        owner=User(google_sub='synthetic-analytics-owner',email='analytics@example.test',role='USER')
        db.add(owner);db.flush()
        p=VpnProfile(device_name='Synthetic PostgreSQL profile',mode='aws-direct',owner_id=owner.id,
            created_by_id=owner.id,status='active',certificate_serial='7A',created_at=now-timedelta(days=2),expires_at=now+timedelta(days=7))
        db.add(p);db.flush()
        db.add(ProfileOwnership(profile_id=p.id,owner_id=owner.id,assigned_at=p.created_at))
        db.add(AnalyticsAgent(slug='aws-direct',token_hash=hash_token('synthetic-postgres-agent-fixture')));db.flush()
        source=db.get(AnalyticsSource,'aws-direct');source.node='aws-direct';source.configured_at=now-timedelta(days=3)
        db.commit()
        context=dict(now=now,profile=p.id,owner=owner.id,epoch=uuid.uuid4(),session=uuid.uuid4())
        started=now-timedelta(seconds=123)
        payload=AccountingReport(events=[event(context,'start',started=started,observed=started),event(context,'end',started=started,rx=123,tx=456)],sources=[frame(context)])
        ingest(db,db.get(AnalyticsAgent,'aws-direct'),payload,Settings(),now)
        ingest(db,db.get(AnalyticsAgent,'aws-direct'),payload,Settings(),now)
        result=build_business_overview(db,Settings(),now,30,'aws-direct')
        assert result.activity.sessions_started==1 and result.activity.sessions_closed==1
        assert result.activity.average_duration_seconds==123 and result.activity.bytes_sent==456
        assert result.online.connections==0
    connection.commit()
    with pytest.raises(RuntimeError,match='operator-preserving'):command.downgrade(cfg,'0007_heartbeat_details')
    connection.rollback()
