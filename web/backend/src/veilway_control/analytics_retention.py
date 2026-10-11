"""Hourly private-data expiry; cumulative profile usage stays available."""
import asyncio
from datetime import timedelta
from sqlalchemy import delete, select
from .models import AnalyticsAgent, AnalyticsCoverage, AnalyticsReceipt, DailyUsage, VpnSession, utcnow
from .workers import StepObservation, run_periodic_step


def prune(db, settings, now, nodes):
    if not nodes:
        return
    db.execute(delete(VpnSession).where(VpnSession.node.in_(nodes), VpnSession.present.is_(False),
        VpnSession.last_seen_at < now-timedelta(days=settings.analytics_session_days)))
    db.execute(delete(AnalyticsReceipt).where(AnalyticsReceipt.node.in_(nodes),
        AnalyticsReceipt.observed_at < now-timedelta(days=settings.analytics_session_days)))
    modes = ['aws-direct'] if nodes == ['aws-direct'] else ['yc-direct','yc-aws-multihop'] if nodes == ['yc-direct'] else ['aws-direct','yc-direct','yc-aws-multihop']
    cutoff = (now-timedelta(days=settings.analytics_aggregate_days-1)).date()
    db.execute(delete(DailyUsage).where(DailyUsage.mode.in_(modes), DailyUsage.day < cutoff))
    db.execute(delete(AnalyticsCoverage).where(AnalyticsCoverage.mode.in_(modes), AnalyticsCoverage.day < cutoff))


class AnalyticsRetentionWorker:
    def __init__(self, settings, session_factory):
        self.settings, self.session_factory = settings, session_factory
        self.stopping = asyncio.Event()
        self.observation = StepObservation()

    def stop(self):
        self.stopping.set()

    async def run(self):
        await run_periodic_step(self.step, self.stopping, 3600, continue_on_error=True, observation=self.observation)

    def step(self):
        now = utcnow()
        with self.session_factory() as db:
            # Lock in a stable order so cleanup cannot race an accounting upload.
            nodes = db.scalars(select(AnalyticsAgent).order_by(AnalyticsAgent.slug).with_for_update()).all()
            # Stale presence must not keep abandoned details forever.
            db.query(VpnSession).filter(VpnSession.last_seen_at < now-timedelta(days=self.settings.analytics_session_days)).update({'present':False})
            prune(db, self.settings, now, [node.slug for node in nodes])
            for node in nodes:
                node.pruned_at = now
            db.commit()
