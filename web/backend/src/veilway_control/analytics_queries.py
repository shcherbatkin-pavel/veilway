"""Read-only analytics queries and metric definitions, independent of HTTP routing."""
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import and_, func, select

from .analytics_schemas import (SourceStatus, OnlineStats, ActivityStats, Funnel, Lifecycle,
    DayStats, ModeStats, BusinessOverview, ProfileAnalytics, SessionAnalytics, ProfilePage, SessionPage)
from .models import (AnalyticsCoverage, AnalyticsOwner, AnalyticsSource, DailyUsage, ProfileAuditEvent,
    ProfileUsage, User, VpnProfile, VpnSession, as_utc)


def period(now, days):
    return datetime.combine(now.date() - timedelta(days=days-1), time.min, timezone.utc)


def sources(db, settings, now, mode):
    rows = {s.mode: s for s in db.scalars(select(AnalyticsSource))}
    result = []
    for key in ('aws-direct', 'yc-direct', 'yc-aws-multihop'):
        if mode and mode != key:
            continue
        row = rows.get(key)
        stamp = as_utc(row.observed_at) if row and row.observed_at else None
        age = max(0, int((now-stamp).total_seconds())) if stamp else None
        state = ('unconfigured' if row is None or row.node is None else
                 'unavailable' if not row.available or stamp is None else
                 'stale' if stamp < now - timedelta(seconds=settings.analytics_stale_seconds) else 'fresh')
        closed_in_snapshot = count(db, VpnSession, VpnSession.mode == key,
            VpnSession.snapshot_at == stamp, VpnSession.epoch == row.epoch,
            VpnSession.state != 'open') if state == 'fresh' else 0
        connections = max(0, row.connection_count - closed_in_snapshot) if state == 'fresh' and row.connection_count is not None else None
        result.append(SourceStatus(mode=key, state=state, configured_at=as_utc(row.configured_at) if row and row.configured_at else None,
            collection_started_at=as_utc(row.first_observed_at) if row and row.first_observed_at else None,
            observed_at=stamp, age_seconds=age, connections=connections,
            capture_errors=row.capture_errors if row else 0))
    return result


def online_condition(statuses, settings, now):
    fresh = [s.mode for s in statuses if s.state == 'fresh']
    return and_(VpnSession.present.is_(True), VpnSession.state == 'open', VpnSession.mode.in_(fresh),
                VpnSession.last_seen_at >= now - timedelta(seconds=settings.analytics_stale_seconds))


def usage_conditions(since, mode):
    return [DailyUsage.day >= since.date(), *([DailyUsage.mode == mode] if mode else [])]


def usage_summary(db, conditions):
    row = db.execute(select(func.count(func.distinct(DailyUsage.owner_id)), func.count(func.distinct(DailyUsage.profile_id)),
        func.coalesce(func.sum(DailyUsage.sessions_started), 0), func.coalesce(func.sum(DailyUsage.sessions_closed), 0),
        func.coalesce(func.sum(DailyUsage.interrupted), 0), func.coalesce(func.sum(DailyUsage.reconnects), 0),
        func.coalesce(func.sum(DailyUsage.duration_seconds), 0), func.coalesce(func.sum(DailyUsage.bytes_received), 0),
        func.coalesce(func.sum(DailyUsage.bytes_sent), 0)).where(*conditions)).one()
    return row


def count(db, model, *conditions):
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def profile_base(mode):
    return [VpnProfile.mode == mode] if mode else []


def build_online(db, settings, now, states):
    online_where = online_condition(states, settings, now)
    confirmed = sum(s.connections or 0 for s in states)
    online_profiles = db.scalar(select(func.count(func.distinct(VpnSession.profile_id))).where(online_where)) or 0
    online_users = db.scalar(select(func.count(func.distinct(VpnSession.owner_id))).where(online_where)) or 0
    matched = count(db, VpnSession, online_where, VpnSession.profile_id.is_not(None))
    unassigned = db.scalar(select(func.count(func.distinct(VpnSession.profile_id))).where(online_where, VpnSession.owner_id.is_(None))) or 0
    complete = bool(states) and all(s.state == 'fresh' for s in states)
    # A selected unconfigured mode cannot be described as zero online.
    online = OnlineStats(complete=complete, connections=confirmed if complete else None,
        profiles=online_profiles if complete else None, users=online_users if complete else None,
        confirmed_connections=confirmed, confirmed_profiles=online_profiles, confirmed_users=online_users,
        unmatched_connections=max(0, confirmed-matched), unassigned_profiles=unassigned)
    return online


def build_activity(db, now, since, mode):
    conditions = usage_conditions(since, mode)
    users, profiles, started, closed, interrupted, reconnects, duration, rx, tx = usage_summary(db, conditions)
    active_owners = select(DailyUsage.owner_id).where(*conditions, DailyUsage.owner_id.is_not(None)).distinct()
    new_users = count(db, AnalyticsOwner, AnalyticsOwner.owner_id.in_(active_owners), AnalyticsOwner.first_connected_at >= since)
    windows = [usage_summary(db, usage_conditions(period(now, d), mode))[0] for d in (1, 7, 30)]
    activity = ActivityStats(users=users, profiles=profiles, dau=windows[0], wau=windows[1], mau=windows[2],
        new_users=new_users, returning_users=users-new_users, sessions_started=started, sessions_closed=closed,
        interruptions=interrupted, reconnects=reconnects, average_duration_seconds=duration/closed if closed else None,
        bytes_received=rx, bytes_sent=tx)
    return activity


def build_funnel(db, now, since, mode):
    cohort = [*profile_base(mode), VpnProfile.created_at >= since, VpnProfile.created_at <= now,
              VpnProfile.certificate_serial.is_not(None), VpnProfile.legacy_import_sha256.is_(None),
              VpnProfile.created_at >= select(AnalyticsSource.first_observed_at).where(AnalyticsSource.mode == VpnProfile.mode).scalar_subquery()]
    issued = count(db, VpnProfile, *cohort)
    downloads = select(ProfileAuditEvent.object_id).where(ProfileAuditEvent.action == 'download', ProfileAuditEvent.result == 'succeeded').distinct()
    download_recorded = count(db, VpnProfile, *cohort, VpnProfile.id.in_(downloads))
    first = select(ProfileUsage.first_connected_at).where(ProfileUsage.profile_id == VpnProfile.id).scalar_subquery()
    connected = count(db, VpnProfile, *cohort, first.is_not(None))
    mature = [*cohort, VpnProfile.created_at <= now-timedelta(days=7)]
    mature_count = count(db, VpnProfile, *mature)
    delay = func.extract('epoch', first) - func.extract('epoch', VpnProfile.created_at)
    activated = count(db, VpnProfile, *mature, delay >= 0, delay <= 7*86400)
    average = db.scalar(select(func.avg(delay)).where(*cohort, first.is_not(None), delay >= 0))
    funnel = Funnel(issued=issued, download_recorded=download_recorded, connected=connected, mature_7d=mature_count,
        connected_within_7d=activated, pending_7d=issued-mature_count,
        activation_7d_percent=100*activated/mature_count if mature_count else None, average_first_connection_seconds=average)
    return funnel


def build_lifecycle(db, now, mode):
    valid = [*profile_base(mode), VpnProfile.status == 'active', VpnProfile.expires_at > now]
    last = select(ProfileUsage.last_seen_at).where(ProfileUsage.profile_id == VpnProfile.id).scalar_subquery()
    lifecycle = Lifecycle(valid_profiles=count(db, VpnProfile, *valid),
        never_observed=count(db, VpnProfile, *valid, last.is_(None)),
        inactive_30d=count(db, VpnProfile, *valid, last < now-timedelta(days=30)),
        used_expiring_7d=count(db, VpnProfile, *valid, VpnProfile.expires_at <= now+timedelta(days=7), last >= now-timedelta(days=30)))
    return lifecycle


def build_daily_list(db, since, days, mode):
    conditions = usage_conditions(since, mode)
    daily_rows = db.execute(select(DailyUsage.day, func.count(func.distinct(DailyUsage.owner_id)),
        func.count(func.distinct(DailyUsage.profile_id)), func.sum(DailyUsage.sessions_started), func.sum(DailyUsage.bytes_received),
        func.sum(DailyUsage.bytes_sent)).where(*conditions).group_by(DailyUsage.day).order_by(DailyUsage.day)).all()
    observed_days = set(db.scalars(select(AnalyticsCoverage.day).where(AnalyticsCoverage.day >= since.date(),
        *([AnalyticsCoverage.mode == mode] if mode else []))))
    daily_values = {r[0]: DayStats(day=r[0], observed=True, users=r[1], profiles=r[2], sessions_started=r[3], bytes_received=r[4], bytes_sent=r[5]) for r in daily_rows}
    daily_list = [daily_values.get((since+timedelta(days=i)).date(), DayStats(day=(since+timedelta(days=i)).date(),
        observed=(since+timedelta(days=i)).date() in observed_days, users=0, profiles=0, sessions_started=0, bytes_received=0, bytes_sent=0)) for i in range(days)]
    return daily_list


def build_modes(db, since, states):
    modes = []
    for state in states:
        u, p, sessions, _, _, _, _, received, sent = usage_summary(db, usage_conditions(since, state.mode))
        modes.append(ModeStats(mode=state.mode, users=u, profiles=p, sessions_started=sessions,
                              bytes_received=received, bytes_sent=sent, connections=state.connections))
    return modes


def build_business_overview(db, settings, now, days, mode=None):
    since = period(now, days)
    states = sources(db, settings, now, mode)
    online = build_online(db, settings, now, states)
    activity = build_activity(db, now, since, mode)
    funnel = build_funnel(db, now, since, mode)
    lifecycle = build_lifecycle(db, now, mode)
    daily_list = build_daily_list(db, since, days, mode)
    modes = build_modes(db, since, states)
    collected = [s.collection_started_at for s in states if s.collection_started_at]
    return BusinessOverview(generated_at=now, period_days=days, detail_retention_days=settings.analytics_session_days,
        aggregate_retention_days=settings.analytics_aggregate_days, collection_started_at=min(collected) if collected else None,
        sources=states, online=online, activity=activity, funnel=funnel, lifecycle=lifecycle, daily=daily_list, modes=modes)


def build_profile_page(db, settings, now, days, mode=None, online_only=False, limit=50, offset=0):
    states = sources(db, settings, now, mode)
    status_by_mode = {s.mode: s.state for s in states}
    online_where = online_condition(states, settings, now)
    connected_ids = select(VpnSession.profile_id).where(online_where).distinct()
    conditions = profile_base(mode)
    if online_only:
        conditions.append(VpnProfile.id.in_(connected_ids))
    totals = (select(DailyUsage.profile_id, func.sum(DailyUsage.sessions_started).label('sessions'),
        func.sum(DailyUsage.bytes_received).label('rx'), func.sum(DailyUsage.bytes_sent).label('tx'))
        .where(*usage_conditions(period(now, days), mode)).group_by(DailyUsage.profile_id).subquery())
    rows = db.execute(select(VpnProfile, User.email, ProfileUsage, totals.c.sessions, totals.c.rx, totals.c.tx,
        VpnProfile.id.in_(connected_ids)).outerjoin(User, User.id == VpnProfile.owner_id)
        .outerjoin(ProfileUsage, ProfileUsage.profile_id == VpnProfile.id)
        .outerjoin(totals, totals.c.profile_id == VpnProfile.id).where(*conditions)
        .order_by(ProfileUsage.last_seen_at.desc().nullslast(), VpnProfile.created_at.desc(), VpnProfile.id)
        .offset(offset).limit(limit)).all()
    items = [ProfileAnalytics(profile_id=p.id, device_name=p.device_name, mode=p.mode, owner_email=email, status=p.status,
        expires_at=as_utc(p.expires_at), online=bool(online) if status_by_mode[p.mode] == 'fresh' else None,
        first_connected_at=as_utc(usage.first_connected_at) if usage else None,
        last_connected_at=as_utc(usage.last_connected_at) if usage else None,
        last_seen_at=as_utc(usage.last_seen_at) if usage else None,
        sessions_started=sessions or 0, bytes_received=rx or 0, bytes_sent=tx or 0)
        for p, email, usage, sessions, rx, tx, online in rows]
    return ProfilePage(items=items, total=count(db, VpnProfile, *conditions))


def build_session_page(db, settings, now, days, mode=None, profile_id=None, limit=50, offset=0):
    conditions = [VpnSession.last_seen_at >= max(period(now, days), now-timedelta(days=settings.analytics_session_days))]
    if mode:
        conditions.append(VpnSession.mode == mode)
    if profile_id:
        conditions.append(VpnSession.profile_id == profile_id)
    states = {s.mode: s.state for s in sources(db, settings, now, mode)}
    rows = db.execute(select(VpnSession, VpnProfile.device_name, User.email)
        .outerjoin(VpnProfile, VpnProfile.id == VpnSession.profile_id).outerjoin(User, User.id == VpnSession.owner_id)
        .where(*conditions).order_by(VpnSession.started_at.desc(), VpnSession.id).offset(offset).limit(limit)).all()
    items = [SessionAnalytics(session_id=s.id, profile_id=s.profile_id, device_name=name, owner_email=email, mode=s.mode,
        state=s.state, online=(s.present and s.state == 'open' and as_utc(s.last_seen_at) >= now-timedelta(seconds=settings.analytics_stale_seconds))
            if states.get(s.mode) == 'fresh' else None,
        started_at=as_utc(s.started_at), last_seen_at=as_utc(s.last_seen_at), ended_at=as_utc(s.ended_at) if s.ended_at else None,
        duration_seconds=s.duration_seconds, bytes_received=s.bytes_received, bytes_sent=s.bytes_sent,
        recovered=s.recovered, reconnect=s.reconnect) for s, name, email in rows]
    return SessionPage(items=items, total=count(db, VpnSession, *conditions), retained_since=now-timedelta(days=settings.analytics_session_days))
