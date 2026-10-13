#!/usr/bin/env python3
"""Local browser acceptance with synthetic API fixtures; no cloud/Google calls."""
import os
from datetime import datetime
from pathlib import Path
import uuid
from playwright.sync_api import sync_playwright, expect, Error as PlaywrightError

BASE = os.environ.get('VEILWAY_BROWSER_BASE_URL', 'http://127.0.0.1:4178')
# The reproducible image serves its compiled bundle on its own loopback only.
server = None
if os.environ.get('VEILWAY_BROWSER_DIST'):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 4178), partial(QuietHandler, directory=os.environ['VEILWAY_BROWSER_DIST']))
    threading.Thread(target=server.serve_forever, daemon=True).start()
ADMIN = '10000000-0000-4000-8000-000000000001'
USER = '10000000-0000-4000-8000-000000000002'
OTHER = '10000000-0000-4000-8000-000000000003'
P1 = '20000000-0000-4000-8000-000000000001'
P2 = '20000000-0000-4000-8000-000000000002'
CSRF = 'synthetic-browser-csrf'
NOW = '2026-10-05T10:00:00Z'


def profile(pid=P1, name='Рабочий ноутбук', owner=USER, status='active', mode='yc-direct'):
    return dict(id=pid, device_name=name, mode=mode, owner_id=owner, status=status,
                created_at=NOW, expires_at='2027-10-05T10:00:00Z')



def health_fixture():
    return dict(generated_at=NOW, assessment='attention',
        nodes=[dict(slug='aws-direct', state='healthy', assessment='ok', reasons=[],
                    last_heartbeat_at=NOW, heartbeat_age_seconds=5, uptime_seconds=100,
                    containers={'veilway-openvpn':'healthy'}),
               dict(slug='yc-direct', state='degraded', assessment='attention',
                    reasons=['heartbeat_stale','component_unhealthy'], last_heartbeat_at=NOW,
                    heartbeat_age_seconds=90, uptime_seconds=100,
                    containers={'veilway-openvpn':'unhealthy'})],
        workers=[dict(name=name, assessment='ok', state='running', started_at=NOW,
                      completed_at=NOW, succeeded_at=NOW, in_progress=False, error_code=None, slow=False)
                 for name in ('restart-worker','profile-worker','crl-worker','analytics-retention')],
        crl=dict(assessment='attention', version=4100, next_update=NOW,
                 publisher_error='publication_unavailable', attempted_at=NOW,
                 observation_stale=False, publication_expired=True,
                 nodes=[dict(slug='yc-direct',status='error',acknowledged_version=4099,
                             last_contact_at=NOW,acknowledged_at=NOW,error_code='installation_failed')]),
        operations=[dict(kind=kind, assessment='attention' if kind == 'issue' else 'ok',
                         queued=1, running=0, oldest_pending_age_seconds=301,
                         delayed=1 if kind == 'issue' else 0, needs_review=0,
                         failed_last_day=0, pki_errors=0, awaiting_delivery=0)
                    for kind in ('issue','revoke','restart')])


def analytics_fixture(days=30, mode=None):
    modes = [mode] if mode else ['aws-direct','yc-direct','yc-aws-multihop']
    source=[dict(mode=m,state='fresh' if m=='aws-direct' else 'unconfigured',configured_at=NOW,
                 collection_started_at="2026-10-02T00:00:00Z",observed_at=NOW,age_seconds=5,connections=1 if m=='aws-direct' else None,capture_errors=0) for m in modes]
    return dict(generated_at=NOW,period_days=days,timezone='UTC',collection_started_at='2026-10-02T00:00:00Z',
        detail_retention_days=90,aggregate_retention_days=365,sources=source,
        online=dict(complete=False,connections=None,profiles=None,users=None,confirmed_connections=1,
                    confirmed_profiles=1,confirmed_users=1,unmatched_connections=0,unassigned_profiles=0),
        activity=dict(users=2,profiles=3,dau=1,wau=2,mau=2,new_users=1,returning_users=1,
                      sessions_started=3,sessions_closed=2,interruptions=1,reconnects=1,
                      average_duration_seconds=123,bytes_received=1024,bytes_sent=2048),
        funnel=dict(issued=3,download_recorded=2,connected=1,mature_7d=2,connected_within_7d=1,
                    pending_7d=1,activation_7d_percent=50,average_first_connection_seconds=600),
        lifecycle=dict(valid_profiles=3,never_observed=1,inactive_30d=1,used_expiring_7d=1),
        daily=[dict(day=f'2026-10-0{i}',observed=i not in (1,3),users=i,profiles=i,sessions_started=i,bytes_received=i*100,bytes_sent=i*200) for i in range(1,6)],
        modes=[dict(mode=m,users=1,profiles=1,sessions_started=1,bytes_received=100,bytes_sent=200,connections=1 if m=='aws-direct' else None) for m in modes])

class Scene:
    def __init__(self, browser, viewport, role='ADMIN', profiles=None, jobs=None, hold_profiles=False):
        self.role, self.expired = role, False
        self.profiles = profiles if profiles is not None else [profile(), profile(P2, 'Телефон', None, mode='aws-direct')]
        self.jobs = jobs or []
        self.requests, self.created, self.revokes, self.restarts = [], [], [], []
        self.audit = []
        self.analytics_queries = []
        self.analytics_override = None
        self.analytics_empty = False
        self.analytics_session_total = 51
        self.hold_analytics = set()
        self.failures = {}
        self.hold_profiles = hold_profiles
        self.held = []
        self.fail_create_after_commit = False
        self.restart_jobs = []
        self.context = browser.new_context(viewport=viewport, accept_downloads=True, is_mobile=viewport["width"] < 761, has_touch=viewport["width"] < 761)
        self.page = self.context.new_page()
        self.page.add_init_script('''
            window.testApiCacheModes = [];
            window.testAbortedUrls = [];
            window.testBlobUrls = new Set();
            const fetchOriginal = window.fetch;
            window.fetch = (url, options) => {
                if (String(url).startsWith('/api/')) window.testApiCacheModes.push(options?.cache);
                options?.signal?.addEventListener('abort', () => window.testAbortedUrls.push(String(url)));
                return fetchOriginal(url, options);
            };
            const createOriginal = URL.createObjectURL;
            const revokeOriginal = URL.revokeObjectURL;
            URL.createObjectURL = blob => { const url = createOriginal(blob); window.testBlobUrls.add(url); return url; };
            URL.revokeObjectURL = url => { window.testBlobUrls.delete(url); return revokeOriginal(url); };
        ''')
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/api/**', self.route)
        self.page.goto(BASE)
        expect(self.page.get_by_role('heading', level=1)).to_contain_text('Профили' if role == 'ADMIN' else 'Мои профили')

    def route(self, route):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(route.request.url)
        path = parsed.path.removeprefix('/api/v1/')
        method = route.request.method
        self.requests.append((method, path))
        if path == 'observability/overview' and getattr(self, 'hold_health', False):
            self.held.append(route); return
        if path == 'profiles' and method == 'GET' and self.hold_profiles:
            self.held.append(route); return
        if path in self.failures:
            route.fulfill(status=self.failures[path], json={'detail':'synthetic-secret-error-text'})
            return
        if path == 'auth/session':
            if self.expired:
                route.fulfill(status=401, json={}); return
            result = dict(user_id=ADMIN if self.role == 'ADMIN' else USER, email='operator@example.test' if self.role == 'ADMIN' else 'owner@example.test', role=self.role, csrf_token=CSRF)
        elif path == 'auth/logout':
            assert route.request.headers.get('x-csrf-token') == CSRF
            self.expired = True
            route.fulfill(status=204); return
        elif self.expired:
            route.fulfill(status=401, json={}); return
        elif method == 'GET' and path.startswith('analytics/'):
            assert self.role=='ADMIN', 'USER requested business analytics'
            query=parse_qs(parsed.query)
            self.analytics_queries.append((path,query))
            if path in self.hold_analytics:
                self.held.append(route);return
            offset=int(query.get('offset',[0])[0]);mode=query.get('mode',[None])[0]
            if path=='analytics/overview':
                result=self.analytics_override or analytics_fixture(int(query.get('days',[30])[0]),mode)
            elif path=='analytics/profiles':
                items=[dict(profile_id=P1 if i==0 else str(uuid.UUID(int=i+100)),device_name='Analytics device' if i==0 else f'Synthetic device {i}',
                    mode='aws-direct',owner_email='owner@example.test',status='active',expires_at=NOW,online=True,
                    first_connected_at=NOW,last_connected_at=NOW,last_seen_at=NOW,sessions_started=1,bytes_received=1024,bytes_sent=2048) for i in range(51)]
                limit=int(query.get('limit',[50])[0]);result=dict(items=items[offset:offset+limit],total=51)
                if self.analytics_empty:result=dict(items=[],total=0)
            else:
                limit=int(query.get('limit',[50])[0])
                items=[dict(session_id=str(uuid.UUID(int=i+999)),profile_id=P1,device_name='Analytics device',owner_email='owner@example.test',
                    mode='aws-direct',state='closed',online=False,started_at=NOW,last_seen_at=NOW,ended_at=NOW,
                    duration_seconds=123,bytes_received=1024,bytes_sent=2048,recovered=False,reconnect=True)
                    for i in range(self.analytics_session_total)]
                result=dict(items=items[offset:offset+limit],total=self.analytics_session_total,retained_since=NOW)
        elif method == 'GET':
            if self.role == 'USER':
                assert path in {'profiles','profile-jobs'}, 'USER attempted an administrative API'
            result = {
                'observability/overview': getattr(self, 'health_override', None) or health_fixture(),
                'profiles': [p for p in self.profiles if self.role == 'ADMIN' or p['owner_id'] == USER],
                'profile-jobs': [j for j in self.jobs if self.role == 'ADMIN' or any(p['id'] == j['profile_id'] and p['owner_id'] == USER for p in self.profiles)],
                'users': [dict(id=USER,email='owner@example.test'),dict(id=OTHER,email='other@example.test')],
                'profile-audit-events': self.audit,
                'vpn-vms': [dict(slug=slug,provider='aws' if slug == 'aws-direct' else 'yandex',state='healthy',last_heartbeat_at=NOW) for slug in ('aws-direct','yc-direct')],
                'restart-jobs': self.restart_jobs,
                'crl-delivery': dict(version=4100,next_update='2026-10-12T10:00:00Z',publisher_error='publication_unavailable',nodes=[dict(slug=slug,status='error' if slug == 'yc-direct' else 'current',acknowledged_version=4099,error_code='installation_failed' if slug == 'yc-direct' else None,last_contact_at=NOW) for slug in ('aws-direct','yc-direct')]),
            }.get(path)
            assert result is not None, f'Unexpected API path: {path}'
            if isinstance(result,list):
                query = parse_qs(parsed.query)
                offset, limit = int(query.get('offset',[0])[0]), int(query.get('limit',[100])[0])
                result = result[offset:offset+limit]
        else:
            assert route.request.headers.get('x-csrf-token') == CSRF
            if path.endswith('/download'):
                pid = path.split('/')[1]
                p = next(p for p in self.profiles if p['id'] == pid)
                assert p['status'] == 'active' and (self.role == 'ADMIN' or p['owner_id'] == USER)
                route.fulfill(status=200, content_type='application/x-openvpn-profile', body='synthetic profile fixture — no VPN keys'); return
            assert self.role == 'ADMIN', 'USER attempted an administrative mutation'
            payload = route.request.post_data_json
            if path == 'profiles':
                self.created.append(payload)
                previous = next((p for p in self.profiles if p.get('creation_key') == payload['idempotency_key']),None)
                p = previous or profile(str(uuid.uuid4()),payload['device_name'],payload.get('owner_id'),'issuing',payload['mode'])
                if not previous:
                    p['creation_key'] = payload['idempotency_key']; self.profiles.insert(0,p)
                result = dict(profile=p,job={})
                if self.fail_create_after_commit:
                    self.fail_create_after_commit = False
                    route.abort('failed'); return
            elif path == 'restart-jobs':
                self.restarts.append(payload)
                result = dict(id=str(uuid.uuid4()),status='queued',created_at=NOW,error_code=None,targets=[dict(slug=slug,position=i,status='queued') for i,slug in enumerate(payload['targets'])])
                self.restart_jobs.insert(0,result)
            else:
                pid = path.split('/')[1]
                p = next(p for p in self.profiles if p['id'] == pid)
                if path.endswith('/owner'):
                    assert p['owner_id'] is None; p['owner_id'] = payload['owner_id']; result = p
                elif path.endswith('/revoke'):
                    self.revokes.append(payload); p['status']='revoking'; result={}
                    self.audit.insert(0,dict(id=str(uuid.uuid4()),actor_id=ADMIN,object_id=pid,action='revoke',result='accepted',created_at=NOW))
                else:
                    assert method == 'PATCH'; p['device_name']=payload['device_name']; result=p
        route.fulfill(status=200 if method != 'POST' or path != 'profiles' else 202, json=result)

    def tab(self,label):
        self.page.get_by_role('navigation').get_by_role('button',name=label,exact=True).click()

    def row(self,name):
        return self.page.get_by_role('article',name=name,exact=True)

    def no_overflow(self):
        assert self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Horizontal overflow: ' + str(self.page.evaluate('Array.from(document.querySelectorAll("body *")).filter(e => e.getBoundingClientRect().right > innerWidth + 1).slice(0,8).map(e => [e.tagName, e.className, e.getBoundingClientRect().width])'))
        assert not self.errors, self.errors

    def close(self):
        self.no_overflow(); self.context.close()


def admin_flow(browser, viewport):
    s=Scene(browser,viewport); page=s.page
    expect(s.row('Рабочий ноутбук')).to_be_visible()
    page.get_by_role('searchbox',name='Поиск профилей').fill('Телефон')
    expect(s.row('Рабочий ноутбук')).to_have_count(0)
    page.get_by_role('searchbox',name='Поиск профилей').fill('')
    page.get_by_label('Режим VPN',exact=True).select_option('aws-direct')
    expect(s.row('Рабочий ноутбук')).to_have_count(0)
    page.get_by_label('Режим VPN',exact=True).select_option('')
    page.get_by_role('button',name='＋ Создать профиль',exact=True).click()
    dialog=page.get_by_role('dialog',name='Создать профиль')
    dialog.get_by_label('Название устройства').fill('Планшет')
    dialog.get_by_label('Режим VPN',exact=True).select_option('yc-aws-multihop')
    dialog.get_by_label('Владелец',exact=True).select_option(USER)
    dialog.get_by_label('Срок действия, дней').fill('30')
    dialog.get_by_role('button',name='Создать',exact=True).click()
    expect(dialog).to_have_count(0)
    expect(s.row('Планшет').get_by_text('Выпускается',exact=True)).to_be_visible()
    page.get_by_label('Статус',exact=True).select_option('active')
    expect(s.row('Планшет')).to_have_count(0)
    page.get_by_label('Статус',exact=True).select_option('')
    page.get_by_label('Владелец',exact=True).select_option('unassigned')
    expect(s.row('Рабочий ноутбук')).to_have_count(0)
    expect(s.row('Телефон')).to_be_visible()
    page.get_by_label('Владелец',exact=True).select_option('')
    assert s.created[-1]['duration_days']==30 and s.created[-1]['owner_id']==USER
    uuid.UUID(s.created[-1]['idempotency_key'])
    s.row('Телефон').get_by_role('button',name='Назначить владельца Телефон').click()
    dialog=page.get_by_role('dialog',name='Назначить владельца')
    dialog.get_by_label('Пользователь',exact=True).select_option(OTHER)
    dialog.get_by_role('button',name='Сохранить',exact=True).click()
    expect(s.row('Телефон').get_by_text('other@example.test')).to_be_visible()
    s.row('Телефон').get_by_role('button',name='Переименовать Телефон').click()
    dialog=page.get_by_role('dialog',name='Переименовать профиль')
    dialog.get_by_label('Название устройства').fill('Личный телефон')
    dialog.get_by_role('button',name='Сохранить',exact=True).click()
    expect(s.row('Личный телефон')).to_be_visible()
    s.row('Рабочий ноутбук').get_by_role('button',name='Отозвать Рабочий ноутбук').click()
    page.get_by_role('dialog').get_by_role('button',name='Отмена',exact=True).click()
    assert not s.revokes
    s.row('Рабочий ноутбук').get_by_role('button',name='Отозвать Рабочий ноутбук').click()
    page.get_by_role('dialog').get_by_role('button',name='Отозвать профиль',exact=True).click()
    expect(s.row('Рабочий ноутбук').get_by_text('Отзыв применяется',exact=True)).to_be_visible()
    expect(s.row('Рабочий ноутбук').get_by_role('button',name='↓ Скачать')).to_be_disabled()
    s.tab('Пользователи')
    page.get_by_role('searchbox',name='Поиск пользователей').fill('other')
    expect(page.get_by_text('owner@example.test',exact=True)).to_have_count(0)
    page.get_by_role('button',name='Профили пользователя →').click()
    expect(s.row('Личный телефон')).to_be_visible()
    expect(s.row('Рабочий ноутбук')).to_have_count(0)
    s.tab('Узлы')
    expect(page.get_by_role('heading',name='VPN-узлы',exact=True).first).to_be_visible()
    expect(page.get_by_text('Не удалось установить список отзывов',exact=True)).to_be_visible()
    page.get_by_role('button',name='Перезагрузить все',exact=True).click()
    dialog=page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    dialog.get_by_role('button',name='Отмена',exact=True).click()
    assert not s.restarts
    page.get_by_label('Выбрать aws-direct',exact=True).check()
    page.get_by_role('button',name='Перезагрузить выбранные',exact=True).click()
    page.get_by_role('dialog').get_by_role('button',name='Перезагрузить',exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert s.restarts==[{'targets':['aws-direct']}]
    s.tab('История')
    expect(page.locator('.audit-row').get_by_text('Запрос отзыва',exact=True)).to_be_visible()
    expect(page.get_by_role('heading',name='История перезагрузок',exact=True)).to_be_visible()
    expect(page.get_by_text('В очереди',exact=True)).to_be_visible()
    s.close()


def user_flow(browser,viewport):
    s=Scene(browser,viewport,'USER',[profile(),profile(P2,'Чужой профиль',OTHER)])
    page=s.page
    expect(s.row('Рабочий ноутбук')).to_be_visible()
    expect(s.row('Рабочий ноутбук').get_by_text('Yandex Direct',exact=True)).to_be_visible()
    expect(s.row('Рабочий ноутбук').locator('time')).to_have_attribute('datetime','2027-10-05T10:00:00Z')
    output=os.environ.get('VEILWAY_BROWSER_SCREENSHOTS')
    if output:
        root=Path(output); root.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(root/f'user-{viewport["width"]}.png'),full_page=True)
    assert not any(path in {'users','vpn-vms','restart-jobs','profile-audit-events','crl-delivery'} for _,path in s.requests)
    assert 'Чужой профиль' not in page.locator('body').inner_text()
    assert 'operator@example.test' not in page.locator('body').inner_text()
    assert 'other@example.test' not in page.locator('body').inner_text()
    for name in ('Пользователи','Узлы','История','＋ Создать профиль'):
        expect(page.get_by_role('button',name=name,exact=True)).to_have_count(0)
    s.failures[f'profiles/{P1}/download']=503
    s.row('Рабочий ноутбук').get_by_role('button',name='↓ Скачать').click()
    expect(page.get_by_role('alert')).to_contain_text('Скачивание временно недоступно')
    assert 'synthetic-secret-error-text' not in page.locator('body').inner_text()
    s.failures.clear()
    with page.expect_download() as download:
        s.row('Рабочий ноутбук').get_by_role('button',name='↓ Скачать').click()
    assert download.value.suggested_filename==f'veilway-{P1}.ovpn'
    s.profiles[0]['status']='revoking'
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(s.row('Рабочий ноутбук').get_by_text('Отзыв применяется',exact=True)).to_be_visible()
    expect(s.row('Рабочий ноутбук').get_by_role('button',name='↓ Скачать')).to_be_disabled()
    s.profiles[0]['status']='revoked'
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(s.row('Рабочий ноутбук').get_by_text('Отозван',exact=True)).to_be_visible()
    s.expired=True
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_role('link',name='Войти через Google')).to_be_visible()
    expect(page.get_by_text('Сессия завершена. Войдите через Google снова.')).to_be_visible()
    expect(page.get_by_role('article')).to_have_count(0)
    s.close()


def empty_and_states(browser,viewport):
    s=Scene(browser,viewport,'USER',[])
    expect(s.page.get_by_role('heading',name='Вам ещё не назначены профили')).to_be_visible(); s.close()
    p1=profile(status='expired'); p1['expires_at']='2026-09-01T10:00:00Z'
    p2=profile(P2,'Ошибка',USER,'failed')
    p3=profile(str(uuid.uuid4()),'Ожидает выпуск',USER,'issuing')
    jobs=[dict(id=str(uuid.uuid4()),profile_id=P2,kind='issue',status='failed',created_at=NOW,error_code='pki_expiry_rejected'),dict(id=str(uuid.uuid4()),profile_id=p3['id'],kind='issue',status='queued',created_at=NOW,error_code='pki_unavailable')]
    s=Scene(browser,viewport,'USER',[p1,p2,p3],jobs)
    expect(s.row('Рабочий ноутбук').get_by_text('Истёк',exact=True)).to_be_visible()
    expect(s.row('Ошибка').get_by_text('Ошибка выпуска',exact=True)).to_be_visible()
    expect(s.row('Ошибка').get_by_text('Не удалось выпустить профиль. Обратитесь к администратору.')).to_be_visible()
    expect(s.row('Ожидает выпуск').get_by_text('Сервис выпуска профилей временно недоступен. Операция будет повторена автоматически.')).to_be_visible()
    expect(s.page.get_by_role('button',name='↓ Скачать')).to_have_count(3)
    for button in s.page.get_by_role('button',name='↓ Скачать').all(): expect(button).to_be_disabled()
    s.close()


def expired_revoke(browser, viewport):
    active = profile()
    active['expires_at'] = '2026-10-05T10:01:00Z'
    expired = profile(P2, 'Истёкший', status='expired')
    elapsed = profile(str(uuid.uuid4()), 'Срок наступил')
    elapsed['expires_at'] = NOW
    s = Scene(browser, viewport, profiles=[active, expired, elapsed])
    s.page.clock.set_fixed_time(datetime.fromisoformat(NOW))
    s.page.reload()
    expect(s.row('Рабочий ноутбук').get_by_role('button', name='Отозвать Рабочий ноутбук', exact=True)).to_be_visible()
    for name in ('Истёкший', 'Срок наступил'):
        expect(s.row(name).get_by_text('Истёк', exact=True)).to_be_visible()
        expect(s.row(name).get_by_role('button', name=f'Отозвать {name}', exact=True)).to_have_count(0)
    s.row('Рабочий ноутбук').get_by_role('button', name='Отозвать Рабочий ноутбук', exact=True).click()
    dialog = s.page.get_by_role('dialog', name='Отозвать профиль?')
    s.page.clock.set_fixed_time(datetime.fromisoformat(active['expires_at']))
    dialog.get_by_role('button', name='Отозвать профиль', exact=True).click()
    expect(dialog.get_by_role('alert')).to_have_text('Срок действия профиля истёк. Отзыв недоступен.')
    assert not s.revokes
    assert not any(method == 'POST' and path.endswith('/revoke') for method, path in s.requests)
    expect(dialog.get_by_role('button', name='Отмена', exact=True)).to_be_enabled()
    dialog.get_by_role('button', name='Отмена', exact=True).click()
    expect(s.row('Рабочий ноутбук').get_by_role('button', name='Отозвать Рабочий ноутбук', exact=True)).to_have_count(0)
    s.close()


def idempotent_creation(browser,viewport):
    s=Scene(browser,viewport)
    s.fail_create_after_commit=True
    s.page.get_by_role('button',name='＋ Создать профиль',exact=True).click()
    dialog=s.page.get_by_role('dialog')
    dialog.get_by_label('Название устройства').fill('Профиль без дубликата')
    dialog.get_by_role('button',name='Создать',exact=True).click()
    expect(dialog.get_by_role('alert')).to_be_visible()
    expect(dialog.get_by_label('Название устройства')).to_be_disabled()
    dialog.get_by_role('button',name='Отмена',exact=True).click()
    s.page.get_by_role('button',name='＋ Создать профиль',exact=True).click()
    dialog=s.page.get_by_role('dialog')
    expect(dialog.get_by_label('Название устройства')).to_have_value('Профиль без дубликата')
    dialog.get_by_role('button',name='Повторить',exact=True).click()
    expect(dialog).to_have_count(0)
    assert len(s.created)==2 and s.created[0]==s.created[1]
    assert len([p for p in s.profiles if p['device_name']=='Профиль без дубликата'])==1
    s.close()


def pagination_and_screenshot(browser,viewport):
    ps=[profile(str(uuid.uuid4()),f'Устройство {n}',USER,mode='aws-direct' if n%2 else 'yc-direct') for n in range(101)]
    s=Scene(browser,viewport,'USER',ps)
    s.page.get_by_role('searchbox',name='Поиск профилей').fill('Устройство 100')
    expect(s.row('Устройство 100')).to_be_visible()
    s.close()
    ps=[profile(),profile(P2,'Личный телефон',OTHER,mode='aws-direct'),profile(str(uuid.uuid4()),'Планшет',USER,'issuing','yc-aws-multihop'),profile(str(uuid.uuid4()),'Архивный ноутбук',None,'revoked')]
    s=Scene(browser,viewport,'ADMIN',ps)
    expect(s.row('Рабочий ноутбук')).to_be_visible()
    output=os.environ.get('VEILWAY_BROWSER_SCREENSHOTS')
    if output:
        root=Path(output); root.mkdir(parents=True,exist_ok=True)
        s.page.screenshot(path=str(root/f'admin-{viewport["width"]}.png'),full_page=True)
    s.close()


def loading_and_failures(browser, viewport):
    s=Scene(browser,viewport,hold_profiles=True)
    expect(s.page.get_by_text('Загружаем профили…',exact=True)).to_be_visible()
    s.hold_profiles=False
    for route in s.held: s.route(route)
    s.held=[]
    expect(s.row('Рабочий ноутбук')).to_be_visible()
    s.failures['profiles']=503
    s.page.get_by_role('button',name='Обновить',exact=True).click()
    expect(s.page.get_by_role('alert')).to_contain_text('Сервис временно недоступен')
    expect(s.row('Рабочий ноутбук')).to_be_visible()
    s.failures.clear()
    s.page.get_by_role('button',name='Обновить',exact=True).click()
    expect(s.page.get_by_role('alert')).to_have_count(0)
    # Native dialog keeps keyboard focus inside and supports Escape without a mutation.
    s.page.get_by_role('button',name='＋ Создать профиль',exact=True).click()
    expect(s.page.get_by_role('dialog')).to_be_visible()
    assert s.page.evaluate('document.activeElement.closest("dialog") !== null')
    s.page.keyboard.press('Escape')
    expect(s.page.get_by_role('dialog')).to_have_count(0)
    assert not s.created
    s.expired=True
    s.row('Телефон').get_by_role('button',name='Переименовать Телефон').click()
    dialog=s.page.get_by_role('dialog')
    dialog.get_by_label('Название устройства').fill('Сессия истекла')
    dialog.get_by_role('button',name='Сохранить',exact=True).click()
    expect(s.page.get_by_role('link',name='Войти через Google')).to_be_visible()
    s.close()


def storage_and_logout(browser, viewport):
    s=Scene(browser,viewport,'USER',[profile()]); page=s.page
    with page.expect_download() as download:
        s.row('Рабочий ноутбук').get_by_role('button',name='↓ Скачать').click()
    assert download.value.path().read_text() == 'synthetic profile fixture — no VPN keys'
    assert page.evaluate('window.testApiCacheModes.every(mode => mode === "no-store")')
    assert page.evaluate('localStorage.length === 0 && sessionStorage.length === 0')
    assert page.evaluate('caches.keys().then(keys => keys.length === 0)')
    assert page.evaluate('indexedDB.databases().then(dbs => dbs.length === 0)')
    assert page.evaluate('navigator.serviceWorker.getRegistrations().then(items => items.length === 0)')
    page.get_by_role('button',name='Выйти',exact=True).click()
    expect(page.get_by_role('link',name='Войти через Google')).to_be_visible()
    expect(page.get_by_role('article')).to_have_count(0)
    assert 'Рабочий ноутбук' not in page.locator('body').inner_text()
    page.wait_for_function('window.testBlobUrls.size === 0',timeout=15000)
    page.reload()
    expect(page.get_by_role('link',name='Войти через Google')).to_be_visible()
    expect(page.get_by_role('article')).to_have_count(0)
    s.close()


def connection_guide(browser, viewport):
    links = {
        'Android': ('Скачать Veilway для Android', '/downloads/android/veilway-0.1.2-pilot.apk'),
        'iPhone': ('App Store', 'https://apps.apple.com/app/openvpn-connect/id590379981'),
        'Windows': ('официальной страницы', 'https://openvpn.net/client/'),
        'Linux': ('официальных репозиториях OpenVPN', 'https://community.openvpn.net/Pages/OpenVPN%20software%20repos'),
    }
    for role in ('USER', 'ADMIN'):
        s = Scene(browser, viewport, role, [profile()]); page = s.page
        expect(s.row('Рабочий ноутбук')).to_be_visible()
        navigation = page.get_by_role('navigation', name='Основная навигация')
        navigation.get_by_role('button', name='Как подключиться', exact=True).click()
        expect(page.get_by_role('heading', name='Как подключиться', exact=True)).to_be_visible()
        expect(navigation.get_by_role('button', name='Как подключиться', exact=True)).to_have_attribute('aria-current', 'page')
        platforms = page.get_by_role('group', name='Выберите платформу')
        expect(platforms.get_by_role('button', name='Android', exact=True)).to_have_attribute('aria-pressed', 'true')
        requests = list(s.requests)
        for label, (text, href) in links.items():
            button = platforms.get_by_role('button', name=label, exact=True)
            button.focus(); page.keyboard.press('Enter')
            expect(button).to_have_attribute('aria-pressed', 'true')
            region = page.get_by_role('region', name=f'Подключение: {label}', exact=True)
            expect(region.locator('ol > li')).to_have_count(4)
            link = region.get_by_role('link', name=text, exact=False)
            expect(link).to_have_attribute('href', href)
            if label == 'Android':
                expect(link).to_have_attribute('download', '')
                expect(region).to_contain_text('Android 10')
                expect(region).to_contain_text('Google')
                expect(region).to_contain_text('OZON и ВКонтакте без отметки')
                expect(region).not_to_contain_text('.ovpn')
                expect(region.get_by_role('link', name='Исходники приложения и библиотек')).to_have_attribute('href', '/downloads/android/veilway-0.1.2-pilot-sources.tar.gz')
                expect(region.get_by_role('link', name='SHA-256')).to_have_attribute('href', '/downloads/android/SHA256SUMS')
            else:
                expect(link).to_have_attribute('target', '_blank')
                expect(link).to_have_attribute('rel', 'noopener noreferrer')
                expect(region).to_contain_text('Не передавайте файл')
            expect(region).to_contain_text('обратитесь к администратору')
            s.no_overflow()
        expect(region).to_contain_text('sudo apt install openvpn')
        expect(region).to_contain_text('sudo openvpn --config "/путь/к/скачанному/файлу.ovpn"')
        expect(region).to_contain_text('Initialization Sequence Completed')
        expect(region).to_contain_text('Ctrl+C')
        page.wait_for_timeout(5500)
        assert s.requests == requests, 'The guide must not fetch APIs or download profiles'
        if role == 'USER':
            for label in ('Пользователи', 'Узлы', 'История'):
                expect(navigation.get_by_role('button', name=label, exact=True)).to_have_count(0)
            assert not any(path in {'users', 'vpn-vms', 'restart-jobs', 'profile-audit-events', 'crl-delivery'} for _, path in s.requests)
        page.get_by_role('button', name='К профилям →', exact=True).click()
        expect(s.row('Рабочий ноутбук')).to_be_visible()
        navigation.get_by_role('button', name='Как подключиться', exact=True).click()
        navigation.get_by_role('button', name='Мои профили' if role == 'USER' else 'Профили', exact=True).click()
        expect(s.row('Рабочий ноутбук')).to_be_visible()
        s.close()


def session_unavailable(browser, viewport):
    context=browser.new_context(viewport=viewport, is_mobile=viewport["width"] < 761, has_touch=viewport["width"] < 761)
    page=context.new_page()
    unavailable=True
    def handler(route):
        if route.request.url.endswith('/auth/session') and unavailable:
            route.fulfill(status=503,json={'detail':'synthetic-secret-provider-body'})
        else:
            route.fulfill(status=401,json={})
    page.route('**/api/**',handler)
    page.goto(BASE)
    expect(page.get_by_role('alert')).to_contain_text('Сервис временно недоступен')
    expect(page.get_by_role('link',name='Войти через Google')).to_have_count(0)
    assert 'synthetic-secret-provider-body' not in page.locator('body').inner_text()
    unavailable=False
    page.get_by_role('button',name='Повторить',exact=True).click()
    expect(page.get_by_role('link',name='Войти через Google')).to_be_visible()
    context.close()


def health_overview(browser, viewport):
    s = Scene(browser, viewport, 'ADMIN', [profile()]); page = s.page
    navigation = page.get_by_role('navigation', name='Основная навигация')
    navigation.get_by_role('button', name='Состояние', exact=True).click()
    expect(page.get_by_role('heading', name='Состояние', exact=True)).to_be_visible()
    expect(page.get_by_role('heading', name='Фоновые задачи', exact=True)).to_be_visible()
    expect(page.get_by_text('Сигнал от узла устарел', exact=False).first).to_be_visible()
    cards = page.locator('.health-row')
    expect(cards.first).to_contain_text('yc-direct')
    assert not page.locator('#health-node-yc-direct').evaluate('(el)=>el.open')
    page.get_by_role('region', name='Проблемы и действия').get_by_role('button', name='Подробности', exact=True).first.click()
    expect(page.locator('#health-node-yc-direct > summary')).to_be_focused()
    assert page.locator('#health-node-yc-direct').evaluate('(el)=>el.open')
    page.locator('#health-node-yc-direct > summary').press('Enter')
    assert not page.locator('#health-node-yc-direct').evaluate('(el)=>el.open')
    page.locator('#health-node-yc-direct > summary').press('Space')
    expect(page.get_by_text('Проход завершился ошибкой', exact=False)).to_have_count(0)
    expect(page.get_by_text('Публикация отзывов недоступна', exact=False).first).to_be_visible()
    s.no_overflow()
    before = len([r for r in s.requests if r == ('GET','observability/overview')])
    page.wait_for_timeout(5500)
    assert len([r for r in s.requests if r == ('GET','observability/overview')]) > before
    assert page.locator('#health-node-yc-direct').evaluate('(el)=>el.open')
    output=os.environ.get('VEILWAY_BROWSER_SCREENSHOTS')
    if output:page.screenshot(path=str(Path(output)/f'health-problems-{viewport["width"]}.png'),full_page=True)
    s.failures['observability/overview'] = 503
    page.get_by_role('button', name='Обновить', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('Показан устаревший снимок')
    expect(page.get_by_role('heading', name='Фоновые задачи', exact=True)).to_be_visible()
    assert 'synthetic-secret-error-text' not in page.locator('body').inner_text()
    del s.failures['observability/overview']
    page.get_by_role('button', name='Обновить', exact=True).click()
    expect(page.get_by_role('alert')).to_have_count(0)
    for button, heading in (('Профили','Профили'), ('Открыть узлы','VPN-узлы'), ('История','История')):
        page.get_by_role('button', name=button, exact=True).last.click()
        expect(page.get_by_role('heading', level=1)).to_contain_text(heading)
        navigation.get_by_role('button', name='Состояние', exact=True).click()
        expect(page.get_by_role('heading', name='Фоновые задачи', exact=True)).to_be_visible()
    # All healthy: compact rows, timestamps/components remain in closed details.
    healthy=health_fixture();healthy['assessment']='ok'
    for node in healthy['nodes']:
        node.update(assessment='ok',reasons=[],heartbeat_age_seconds=5,containers={'veilway-openvpn':'healthy'})
    healthy['crl'].update(assessment='ok',publisher_error=None,publication_expired=False)
    for node in healthy['crl']['nodes']:node.update(status='current',error_code=None)
    for op in healthy['operations']:op.update(assessment='ok',delayed=0)
    s.health_override=healthy
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_text('Проблем не обнаружено',exact=True)).to_be_visible()
    expect(page.get_by_text('veilway-openvpn: Работает',exact=True).first).not_to_be_visible()
    assert page.locator('.health-row[open]').count()==0
    s.no_overflow()
    if output:page.screenshot(path=str(Path(output)/f'health-healthy-{viewport["width"]}.png'),full_page=True)
    # Leaving the page cancels an outstanding poll and ignores a late result.
    s.hold_health=True
    page.get_by_role('button',name='Обновить',exact=True).click()
    page.wait_for_timeout(150);assert s.held
    navigation.get_by_role('button',name='Профили',exact=True).click()
    expect(page.get_by_role('heading',level=1)).to_contain_text('Профили')
    assert '/api/v1/observability/overview' in page.evaluate('window.testAbortedUrls')
    for route in s.held:
        try:route.fulfill(json=health_fixture())
        except PlaywrightError:pass
    s.held.clear();s.hold_health=False
    expect(page.get_by_role('heading',name='Фоновые задачи',exact=True)).to_have_count(0)
    navigation.get_by_role('button',name='Состояние',exact=True).click()
    expect(page.get_by_text('Проблем не обнаружено',exact=True)).to_be_visible()
    # Incomplete heartbeat detail and planned restart are distinct.
    healthy['assessment']='unknown'
    healthy['nodes'][0].update(assessment='unknown',reasons=['heartbeat_missing','details_missing'],last_heartbeat_at=None,heartbeat_age_seconds=None,containers=None)
    healthy['nodes'][1].update(state='restarting',reasons=['planned_restart'])
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_text('Сигнал от узла ещё не получен',exact=False).first).to_be_visible()
    expect(page.get_by_text('Текущая операция: плановый перезапуск',exact=True)).to_be_visible()
    assert 'yc-direct' not in page.get_by_role('region',name='Проблемы и действия').inner_text()
    # Detail missing after a legacy heartbeat remains different from no signal.
    healthy['nodes'][0].update(reasons=['details_missing'],last_heartbeat_at=NOW,heartbeat_age_seconds=5)
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.locator('#health-node-aws-direct > summary')).to_contain_text('Детализация отсутствует')
    expect(page.get_by_text('Сигнал от узла ещё не получен',exact=False)).to_have_count(0)
    # Worker error, slow pass, stale publication and delivery each stay visible.
    healthy['assessment']='attention'
    healthy['workers'][0].update(assessment='attention',error_code='step_failed',slow=True,in_progress=True)
    healthy['workers'][1].update(assessment='unknown',state='stopped',succeeded_at=None,completed_at=None)
    healthy['crl'].update(assessment='attention',observation_stale=True)
    healthy['crl']['nodes'][0].update(status='offline',error_code='synthetic-secret-error-text')
    healthy['operations'][1].update(assessment='attention',delayed=1,needs_review=2,failed_last_day=3,pki_errors=1,awaiting_delivery=1)
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_text('Проход длится больше трёх минут',exact=False).first).to_be_visible()
    expect(page.get_by_text('Нет свежего контакта',exact=False).first).to_be_visible()
    expect(page.get_by_text('Доступность PKI не подтверждена',exact=False).first).to_be_visible()
    assert 'synthetic-secret-error-text' not in page.locator('body').inner_text()
    page.locator('#health-delivery-yc-direct > summary').click()
    expect(page.get_by_text('Неизвестный код',exact=True)).to_be_visible()
    for state,label in (('pending','Ожидает установки'),('expired','Срок действия истёк')):
        healthy['crl']['nodes'][0].update(status=state,error_code=None)
        page.get_by_role('button',name='Обновить',exact=True).click()
        expect(page.locator('#health-delivery-yc-direct > summary')).to_contain_text(label)
        expect(page.get_by_role('region',name='Проблемы и действия')).to_contain_text(label)
    # A successful pass does not conceal a business operation error.
    healthy['workers'][0].update(assessment='ok',error_code=None,slow=False,in_progress=False)
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.locator('#health-worker-restart-worker > summary')).to_contain_text('В норме')
    expect(page.locator('#health-operation-revoke > summary')).to_contain_text('Требует внимания')
    s.failures['observability/overview']=403
    page.get_by_role('button', name='Обновить', exact=True).click()
    expect(page.get_by_role('link', name='Войти через Google')).to_be_visible()
    expect(page.get_by_role('heading', name='Фоновые задачи', exact=True)).to_have_count(0)
    s.close()
    s = Scene(browser, viewport, 'ADMIN', [profile()])
    s.page.get_by_role('button', name='Состояние', exact=True).click()
    expect(s.page.get_by_role('heading', name='Фоновые задачи', exact=True)).to_be_visible()
    s.expired=True
    s.page.get_by_role('button', name='Обновить', exact=True).click()
    expect(s.page.get_by_role('link',name='Войти через Google')).to_be_visible()
    s.close()
    s = Scene(browser, viewport, 'USER', [profile()])
    expect(s.page.get_by_role('button', name='Состояние', exact=True)).to_have_count(0)
    assert not any(path == 'observability/overview' for _, path in s.requests)
    s.close()
    s = Scene(browser, viewport, 'ADMIN', [profile()])
    s.failures['observability/overview'] = 503
    s.page.get_by_role('button', name='Состояние', exact=True).click()
    expect(s.page.get_by_role('alert')).to_contain_text('Не удалось получить состояние')
    expect(s.page.get_by_role('heading', name='Фоновые задачи', exact=True)).to_have_count(0)
    s.close()


def business_analytics(browser, viewport):
    s=Scene(browser,viewport,'ADMIN',[profile()]);page=s.page
    tab=lambda name:page.get_by_role('tab',name=name,exact=True)
    def last_query(path):return [q for p,q in s.analytics_queries if p==path][-1]
    page.get_by_role('button',name='Аналитика',exact=True).click()
    expect(page.get_by_role('heading',name='Подключённые профили',exact=True)).to_be_visible()
    expect(tab('Обзор')).to_have_attribute('aria-selected','true')
    expect(page.get_by_label('Период аналитики',exact=True)).to_have_count(0)
    assert last_query('analytics/profiles')['limit']==['8']
    assert page.locator('.analytics-online-table tbody tr').count()==8
    assert not any(path=='analytics/sessions' for _,path in s.requests)
    expect(page.get_by_text('Онлайн-данные неполные.',exact=False)).to_be_visible()
    assert not page.locator('.analytics-sources').evaluate('(el)=>el.open')
    page.locator('.analytics-sources > summary').click()
    expect(page.get_by_text('Сбор не настроен',exact=True).first).to_be_visible()
    page.locator('.analytics-sources > summary').click()
    s.no_overflow()
    output=os.environ.get('VEILWAY_BROWSER_SCREENSHOTS')
    if output:page.screenshot(path=str(Path(output)/f'analytics-overview-{viewport["width"]}.png'),full_page=True)
    tab('Обзор').focus();page.keyboard.press('ArrowRight')
    expect(tab('Активность')).to_be_focused()
    expect(page.get_by_role('heading',name='Использование по дням',exact=True)).to_be_visible()
    before=len(s.analytics_queries)
    page.get_by_role('button',name='Обновить',exact=True).click();page.wait_for_timeout(150)
    assert all(path=='analytics/overview' for path,_ in s.analytics_queries[before:])
    chart=page.get_by_role('img',name='Пользователи по дням, UTC')
    expect(chart).to_be_visible()
    assert chart.bounding_box()['height'] <= (160 if viewport['width']<761 else 220)
    assert '2026-10-01' not in chart.text_content()
    page.get_by_label('Показатель графика',exact=True).select_option('traffic')
    page.get_by_label('День графика',exact=True).fill('1')
    expect(page.get_by_text('Нет наблюдений',exact=True)).to_be_visible()
    assert all(not d.evaluate('(el)=>el.open') for d in page.locator('.analytics-details').all())
    page.locator('.analytics-details > summary').filter(has_text='Первое подключение').click()
    expect(page.get_by_text('50.0%',exact=True)).to_be_visible()
    page.get_by_label('Период аналитики',exact=True).select_option('7')
    page.get_by_label('Режим аналитики',exact=True).select_option('aws-direct')
    expect(page.get_by_role('img',name='Трафик по дням, UTC')).to_be_visible()
    s.no_overflow()
    if output:page.screenshot(path=str(Path(output)/f'analytics-activity-{viewport["width"]}.png'),full_page=True)
    tab('Обзор').click();page.get_by_role('button',name='Все подключённые',exact=True).click()
    expect(tab('Профили')).to_have_attribute('aria-selected','true')
    expect(page.get_by_label('Только подключённые профили',exact=True)).to_be_checked()
    expect(page.get_by_label('Период аналитики',exact=True)).to_have_value('7')
    page.get_by_role('button',name='Следующие профили',exact=True).click()
    item=page.get_by_role('button',name='Сессии профиля Synthetic device 50',exact=True)
    expect(item).to_be_visible();item.click()
    expect(page.get_by_role('heading',name='История сессий',exact=True)).to_be_visible()
    expect(page.locator('.analytics-selection')).to_contain_text('Synthetic device 50')
    assert last_query('analytics/sessions')['profile_id']==[str(uuid.UUID(int=150))]
    page.get_by_role('button',name='Следующие сессии',exact=True).click()
    expect(page.get_by_text('51–51 из 51',exact=True)).to_be_visible()
    page.get_by_role('button',name='К профилям',exact=True).click()
    expect(item).to_be_visible()
    expect(page.get_by_label('Только подключённые профили',exact=True)).to_be_checked()
    tab('Сессии').click()
    expect(page.locator('.analytics-selection')).to_contain_text('Synthetic device 50')
    expect(page.get_by_text('51–51 из 51',exact=True)).to_be_visible()
    page.get_by_label('Период аналитики',exact=True).select_option('30')
    expect(page.locator('.analytics-selection')).to_have_count(0)
    expect(page.get_by_role('heading',name='История сессий',exact=True)).to_be_visible()
    tab('Профили').click()
    page.get_by_role('button',name='Сессии профиля Analytics device',exact=True).click()
    page.get_by_role('button',name='Все сессии',exact=True).click()
    expect(page.locator('.analytics-selection')).to_have_count(0)
    s.no_overflow()
    before=len([path for _,path in s.requests if path=='analytics/overview'])
    page.wait_for_timeout(5500)
    assert len([path for _,path in s.requests if path=='analytics/overview'])>before
    s.failures['analytics/overview']=503
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('Показан устаревший снимок')
    expect(page.locator('.analytics-freshness')).to_contain_text('Снимок устарел')
    assert 'synthetic-secret-error-text' not in page.locator('body').inner_text()
    del s.failures['analytics/overview']
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_role('alert')).to_have_count(0)
    s.hold_analytics.add('analytics/sessions')
    page.get_by_role('button',name='Обновить',exact=True).click();page.wait_for_timeout(150)
    assert s.held
    tab('Активность').click()
    expect(page.get_by_role('heading',name='Использование по дням',exact=True)).to_be_visible()
    for route in s.held:
        try:route.fulfill(json=dict(items=[],total=0,retained_since=NOW))
        except PlaywrightError:pass
    s.held.clear();s.hold_analytics.clear()
    expect(page.get_by_role('heading',name='История сессий',exact=True)).to_have_count(0)
    s.failures['analytics/overview']=503
    tab('Профили').click()
    expect(page.get_by_role('alert')).to_contain_text('Свежие данные недоступны')
    expect(page.get_by_role('heading',name='Использование профилей',exact=True)).to_have_count(0)
    del s.failures['analytics/overview']
    tab('Активность').click()
    expect(page.get_by_role('heading',name='Использование по дням',exact=True)).to_be_visible()
    fixture=analytics_fixture();fixture['daily']=[fixture['daily'][1]]
    fixture['funnel']={k:None if k in ('activation_7d_percent','average_first_connection_seconds') else 0 for k in fixture['funnel']}
    fixture['sources'][0].update(state='stale',capture_errors=1)
    s.analytics_override=fixture
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_text('Пока наблюдался только один день.',exact=False)).to_be_visible()
    expect(page.get_by_role('img',name='Трафик по дням, UTC')).to_have_count(0)
    expect(page.get_by_role('alert')).to_contain_text('Зарегистрированы пропуски сбора')
    page.locator('.analytics-details > summary').filter(has_text='Первое подключение').click()
    expect(page.get_by_text('Новых профилей для воронки пока нет.',exact=True)).to_be_visible()
    fixture['daily']=[];fixture['sources'][0].update(capture_errors=0)
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_text('За выбранный период наблюдений нет.',exact=True)).to_be_visible()
    s.analytics_empty=True
    tab('Обзор').click()
    expect(page.get_by_text('Подтверждённых подключённых профилей нет.',exact=False)).to_be_visible()
    s.no_overflow()
    fixture['online'].update(complete=True,connections=0,profiles=0,users=0)
    for source in fixture['sources']:source.update(state='fresh',connections=0,capture_errors=0)
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_text('Подключённых профилей сейчас нет.',exact=True)).to_be_visible()
    expect(page.locator('.analytics-freshness')).to_contain_text('Свежие данные')
    expect(page.get_by_text('Онлайн-данные неполные.',exact=False)).to_have_count(0)
    tab('Обзор').focus();page.keyboard.press('End')
    expect(tab('Сессии')).to_be_focused()
    page.keyboard.press('Home')
    expect(tab('Обзор')).to_be_focused()
    page.keyboard.press('ArrowLeft')
    expect(tab('Сессии')).to_be_focused()
    expect(page.get_by_role('heading',name='История сессий',exact=True)).to_be_visible()
    s.expired=True
    page.get_by_role('button',name='Обновить',exact=True).click()
    expect(page.get_by_role('link',name='Войти через Google')).to_be_visible()
    expect(page.get_by_role('heading',name='Аналитика',exact=True)).to_have_count(0)
    s.close()
    s=Scene(browser,viewport,'ADMIN',[profile()]);s.failures['analytics/overview']=503;s.failures['analytics/profiles']=403
    s.page.get_by_role('button',name='Аналитика',exact=True).click()
    expect(s.page.get_by_role('link',name='Войти через Google')).to_be_visible();s.close()
    s=Scene(browser,viewport,'USER',[profile()])
    expect(s.page.get_by_role('button',name='Аналитика',exact=True)).to_have_count(0)
    assert not any(path.startswith('analytics/') for _,path in s.requests)
    s.close()


with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    try:
        count=0
        for viewport in ({'width':1440,'height':1000},{'width':390,'height':844},{'width':320,'height':740}):
            for run in (admin_flow,user_flow,empty_and_states,expired_revoke,idempotent_creation,pagination_and_screenshot,loading_and_failures,session_unavailable,storage_and_logout,connection_guide,health_overview,business_analytics):
                run(browser,viewport)
                count+=1; print(f'PASS {run.__name__} {viewport["width"]}',flush=True)
        print(f'{count} browser scenarios passed',flush=True)
    finally:
        browser.close()
        if server:
            server.shutdown(); server.server_close()
