#!/usr/bin/env python3
"""Local browser acceptance with synthetic API fixtures; no cloud/Google calls."""
import os
from pathlib import Path
import uuid
from playwright.sync_api import sync_playwright, expect

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


class Scene:
    def __init__(self, browser, viewport, role='ADMIN', profiles=None, jobs=None, hold_profiles=False):
        self.role, self.expired = role, False
        self.profiles = profiles if profiles is not None else [profile(), profile(P2, 'Телефон', None, mode='aws-direct')]
        self.jobs = jobs or []
        self.requests, self.created, self.revokes, self.restarts = [], [], [], []
        self.audit = []
        self.failures = {}
        self.hold_profiles = hold_profiles
        self.held = []
        self.fail_create_after_commit = False
        self.restart_jobs = []
        self.context = browser.new_context(viewport=viewport, accept_downloads=True, is_mobile=viewport["width"] < 761, has_touch=viewport["width"] < 761)
        self.page = self.context.new_page()
        self.page.add_init_script('''
            window.testApiCacheModes = [];
            window.testBlobUrls = new Set();
            const fetchOriginal = window.fetch;
            window.fetch = (url, options) => {
                if (String(url).startsWith('/api/')) window.testApiCacheModes.push(options?.cache);
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
        elif method == 'GET':
            if self.role == 'USER':
                assert path in {'profiles','profile-jobs'}, 'USER attempted an administrative API'
            result = {
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
        'Android': ('Google Play', 'https://play.google.com/store/apps/details?id=net.openvpn.openvpn'),
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
            expect(link).to_have_attribute('target', '_blank')
            expect(link).to_have_attribute('rel', 'noopener noreferrer')
            expect(region).to_contain_text('обратитесь к администратору')
            expect(region).to_contain_text('Не передавайте файл')
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


with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    try:
        count=0
        for viewport in ({'width':1440,'height':1000},{'width':390,'height':844},{'width':320,'height':740}):
            for run in (admin_flow,user_flow,empty_and_states,idempotent_creation,pagination_and_screenshot,loading_and_failures,session_unavailable,storage_and_logout,connection_guide):
                run(browser,viewport)
                count+=1; print(f'PASS {run.__name__} {viewport["width"]}',flush=True)
        print(f'{count} browser scenarios passed',flush=True)
    finally:
        browser.close()
        if server:
            server.shutdown(); server.server_close()
