"""Same-origin WSGI application with transaction-protected multi-user APIs."""

import hmac
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .core.dates import month_bounds
from .core.models import LunchError
from .core.repository import LunchRepository
from .core.service import LunchService
from .core.weights import redistribute_weight
from .database import Database, new_scope
from .security import digest, hash_password, make_session, read_cookie, verify_password

STATIC = Path(__file__).resolve().parent.parent / 'static'
CHINA = timezone(timedelta(hours=8))


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def text(value, label, maximum=64):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise APIError(f'{label}不能为空，且最多 {maximum} 个字符。')
    value = value.strip()
    if any(ord(c) < 32 for c in value):
        raise APIError(f'{label}不能包含控制字符。')
    return value


def group_code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9]{8,16}', value):
        raise APIError('群组号必须是 8～16 位数字或大小写英文字母。')
    return value


def record_json(record):
    if record is None:
        return None
    result = asdict(record)
    result['day'] = record.day.isoformat()
    result['chosen_at'] = record.chosen_at.isoformat()
    result['remaining_rerolls'] = record.remaining_rerolls
    result['version'] = f'{record.chosen_at.isoformat()}:{record.rerolls}'
    return result


class Application:
    def __init__(self, path, *, secure_cookie=False, now=None):
        self.database = Database(path)
        self.secure = secure_cookie
        self.now = now or (lambda: datetime.now(CHINA))

    def __call__(self, env, start_response):
        status, headers, body = 200, [], b''
        try:
            path, method = env.get('PATH_INFO', '/'), env['REQUEST_METHOD']
            if path.startswith('/api/'):
                result, cookie = self.api(env, path, method)
                body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
                headers = [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store')]
                if cookie:
                    headers.append(('Set-Cookie', cookie))
            else:
                if method not in ('GET', 'HEAD'):
                    raise APIError('请求方法不支持。', 405)
                assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                          '/style.css': ('style.css', 'text/css')}
                if path not in assets:
                    raise APIError('页面不存在。', 404)
                filename, mime = assets[path]
                body = (STATIC / filename).read_bytes()
                headers = [('Content-Type', mime + '; charset=utf-8'), ('Cache-Control', 'no-cache')]
        except (APIError, LunchError) as error:
            status = getattr(error, 'status', 400)
            body = json.dumps({'error': getattr(error, 'message', str(error))}, ensure_ascii=False).encode('utf-8')
            headers = [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store')]
        except Exception:
            logging.exception('Request failed')
            status = 500
            body = b'{"error":"Service unavailable. Please retry."}'
            headers = [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store')]
        headers.extend([
            ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'same-origin'),
            ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
            ('Content-Length', str(len(body)))])
        if self.secure:
            headers.append(('Strict-Transport-Security', 'max-age=31536000'))
        reasons = {200: 'OK', 400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found',
                   405: 'Method Not Allowed', 409: 'Conflict', 413: 'Content Too Large', 429: 'Too Many Requests', 500: 'Internal Server Error'}
        start_response(f'{status} {reasons.get(status, "Error")}', headers)
        return [b'' if env['REQUEST_METHOD'] == 'HEAD' else body]

    def api(self, env, path, method):
        if method not in ('GET', 'POST'):
            raise APIError('请求方法不支持。', 405)
        if method == 'GET' and path not in ('/api/state', '/api/groups/search'):
            raise APIError('接口不存在。', 404)
        payload = {}
        if method == 'POST':
            origin = env.get('HTTP_ORIGIN')
            if origin:
                parsed = urlsplit(origin)
                if parsed.netloc != env.get('HTTP_HOST') or parsed.scheme not in ('http', 'https'):
                    raise APIError('请从本站页面操作。', 403)
            if env.get('HTTP_SEC_FETCH_SITE') == 'cross-site':
                raise APIError('不接受跨站请求。', 403)
            if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
                raise APIError('请提交 JSON 数据。')
            try:
                length = int(env.get('CONTENT_LENGTH') or 0)
                if not 0 < length <= 65536:
                    raise APIError('请求内容过大或为空。', 413)
                payload = json.loads(env['wsgi.input'].read(length))
                if not isinstance(payload, dict):
                    raise ValueError()
                # Reject unpaired Unicode surrogates and non-finite JSON numbers.
                json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            except (ValueError, UnicodeError):
                raise APIError('请求内容格式不正确。') from None
        now = self.now()
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                session = db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?',
                                     (digest(read_cookie(env)), now.timestamp())).fetchone()
                cookie = None
                if session is None:
                    if method != 'GET' or path != '/api/state':
                        raise APIError('登录状态已过期，请刷新页面后重试。', 401)
                    db.execute('DELETE FROM sessions WHERE expires<=?', (now.timestamp(),))
                    session, cookie = make_session(db, now.timestamp(), guest_scope=new_scope(db), secure=self.secure)
                if method == 'POST' and not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', '').encode('utf-8'), session['csrf'].encode('ascii')):
                    raise APIError('页面凭证已失效，请刷新后重试。', 403)
                user, group, scope = self.context(db, session)
                if method == 'POST' and path not in ('/api/auth/login', '/api/auth/register', '/api/auth/logout'):
                    if payload.get('scope') != scope:
                        raise APIError('当前账号或群组已变化，请刷新后重试。', 409)
                repo = LunchRepository(db, scope)
                service = LunchService(repo, now=lambda: now)
                if path in ('/api/auth/login', '/api/auth/register') and method == 'POST':
                    # Keep failed attempts even when authentication fails.
                    self.rate_limit(db, env.get('REMOTE_ADDR', ''), payload.get('username'), now.timestamp())
                    db.commit()
                    db.execute('BEGIN IMMEDIATE')
                    if not db.execute('SELECT 1 FROM sessions WHERE token_hash=?', (session['token_hash'],)).fetchone():
                        raise APIError('登录状态已变化，请刷新后重试。', 401)
                    session, cookie = self.authenticate(db, session, path, payload, now)
                elif path == '/api/auth/logout' and method == 'POST':
                    db.execute('DELETE FROM sessions WHERE token_hash=?', (session['token_hash'],))
                    session, cookie = make_session(db, now.timestamp(), guest_scope=new_scope(db), secure=self.secure)
                elif path == '/api/groups/search' and method == 'GET':
                    self.require_user(user)
                    code = group_code(parse_qs(env.get('QUERY_STRING', '')).get('code', [''])[0])
                    found = db.execute('SELECT id,name,code FROM groups WHERE code=? AND archived=0', (code,)).fetchone()
                    if not found:
                        raise APIError('没有找到此群组，请检查群组号。', 404)
                    result = dict(found)
                    result['members'] = db.execute('SELECT count(*) FROM memberships WHERE group_id=? AND left_at IS NULL', (found['id'],)).fetchone()[0]
                    db.commit()
                    return {'group': result}, cookie
                elif path == '/api/groups/create' and method == 'POST':
                    self.require_user(user)
                    if group:
                        raise APIError('每个账号最多加入或创建一个群组，请先退出当前群组。', 409)
                    code, name = group_code(payload.get('code')), text(payload.get('name'), '群组名', 40)
                    if db.execute('SELECT 1 FROM groups WHERE code=?', (code,)).fetchone():
                        raise APIError('此群组号已被使用。', 409)
                    group_scope = new_scope(db)
                    group_id = db.execute('INSERT INTO groups(code,name,owner_id,scope_id) VALUES(?,?,?,?)',
                                          (code, name, user['id'], group_scope)).lastrowid
                    target = LunchRepository(db, group_scope)
                    for item in repo.options():
                        target.insert_option(item.name, item.weight)
                    db.execute('INSERT INTO memberships(user_id,group_id,joined_at) VALUES(?,?,?)', (user['id'], group_id, now.isoformat()))
                elif path == '/api/groups/join' and method == 'POST':
                    self.require_user(user)
                    if group:
                        raise APIError('每个账号最多加入或创建一个群组，请先退出当前群组。', 409)
                    found = db.execute('SELECT * FROM groups WHERE code=? AND archived=0', (group_code(payload.get('code')),)).fetchone()
                    if not found:
                        raise APIError('群组不存在或已归档。', 404)
                    db.execute('INSERT INTO memberships(user_id,group_id,joined_at) VALUES(?,?,?)', (user['id'], found['id'], now.isoformat()))
                elif path == '/api/groups/leave' and method == 'POST':
                    self.require_user(user)
                    if not group:
                        raise APIError('当前未加入群组。')
                    self.leave_group(db, user, group, now)
                elif path == '/api/options/add' and method == 'POST':
                    names = payload.get('names')
                    if not isinstance(names, list) or len(names) > 100 or not all(isinstance(n, str) for n in names):
                        raise APIError('每次最多添加 100 个选项。')
                    if len(repo.options()) + len(names) > 200:
                        raise APIError('午餐清单最多保留 200 个选项。')
                    service.add_options(names)
                elif path == '/api/options/delete' and method == 'POST':
                    ids = payload.get('ids')
                    if not isinstance(ids, list) or not all(type(n) is int for n in ids):
                        raise APIError('请选择要删除的选项。')
                    service.delete_options(ids)
                elif path == '/api/options/weight' and method == 'POST':
                    items = repo.options()
                    ids = [o.id for o in items]
                    target = payload.get('percent')
                    if type(target) not in (int, float) or payload.get('id') not in ids:
                        raise APIError('权重或选项不正确。')
                    values = redistribute_weight([o.weight for o in items], ids.index(payload['id']), target / 100)
                    service.set_weights(dict(zip(ids, values)))
                elif path == '/api/options/reset' and method == 'POST':
                    service.reset_weights()
                elif path == '/api/draw' and method == 'POST':
                    if payload.get('day') != now.date().isoformat():
                        raise APIError('日期已变化，请刷新后重新抽签。', 409)
                    reroll = payload.get('reroll', False)
                    if type(reroll) is not bool:
                        raise APIError('抽签参数不正确。')
                    current = service.today_record()
                    if reroll and (not current or payload.get('version') != record_json(current)['version']):
                        raise APIError('其他成员已更新结果，请查看最新结果后再操作。', 409)
                    if reroll and current.rerolls >= 1 and payload.get('confirmed') is not True:
                        raise APIError('请确认使用本次反悔机会。')
                    service.choose(reroll=reroll)
                elif path != '/api/state' or method != 'GET':
                    raise APIError('接口不存在。', 404)
                result = self.state(db, session, env, now)
                db.commit()
                return result, cookie
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def require_user(user):
        if user is None:
            raise APIError('此功能需要登录账号。', 401)

    @staticmethod
    def context(db, session):
        user = db.execute('SELECT id,username,scope_id FROM users WHERE id=?', (session['user_id'],)).fetchone()
        if user is None:
            return None, None, session['guest_scope']
        group = db.execute('''SELECT g.*, m.id AS membership_id,m.joined_at FROM memberships m
            JOIN groups g ON g.id=m.group_id WHERE m.user_id=? AND m.left_at IS NULL''', (user['id'],)).fetchone()
        return user, group, group['scope_id'] if group else user['scope_id']

    @staticmethod
    def rate_limit(db, ip, username, timestamp):
        db.execute('DELETE FROM auth_attempts WHERE started<?', (timestamp - 600,))
        keys = [('ip:' + digest(ip), 60), ('account:' + digest(ip + ':' + str(username).strip().casefold()), 8)]
        for key, limit in keys:
            row = db.execute('SELECT count FROM auth_attempts WHERE key=?', (key,)).fetchone()
            if row and row['count'] >= limit:
                raise APIError('尝试次数过多，请在 10 分钟后重试。', 429)
        for key, _ in keys:
            db.execute('INSERT INTO auth_attempts VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, timestamp))

    def authenticate(self, db, session, path, payload, now):
        username = text(payload.get('username'), '用户名')
        password = payload.get('password')
        if not isinstance(password, str) or not 4 <= len(password) <= 16:
            raise APIError('密码必须为 4～16 位，字符种类不限。')
        user = db.execute('SELECT * FROM users WHERE username_key=?', (username.casefold(),)).fetchone()
        if path.endswith('register'):
            if user:
                raise APIError('此用户名已注册。', 409)
            user_id = db.execute('INSERT INTO users(username,username_key,password_hash,scope_id) VALUES(?,?,?,?)',
                                 (username, username.casefold(), hash_password(password), new_scope(db))).lastrowid
        else:
            # Perform the same expensive hash for unknown usernames.
            valid = verify_password(password, user['password_hash']) if user else bool(hash_password(password)) and False
            if not valid:
                raise APIError('用户名或密码错误。', 401)
            user_id = user['id']
        db.execute('DELETE FROM sessions WHERE token_hash=?', (session['token_hash'],))
        return make_session(db, now.timestamp(), user_id=user_id, remember=payload.get('remember') is True, secure=self.secure)

    @staticmethod
    def leave_group(db, user, group, now):
        db.execute('''INSERT INTO membership_lunches SELECT ?,day,option_name,color,rerolls,chosen_at FROM lunches
            WHERE scope_id=? AND day>=? AND day<=?''',
            (group['membership_id'], group['scope_id'], group['joined_at'][:10], now.date().isoformat()))
        db.execute('UPDATE memberships SET left_at=? WHERE id=?', (now.isoformat(), group['membership_id']))
        next_owner = db.execute('SELECT user_id FROM memberships WHERE group_id=? AND left_at IS NULL ORDER BY id LIMIT 1', (group['id'],)).fetchone()
        if not next_owner:
            db.execute('UPDATE groups SET archived=1 WHERE id=?', (group['id'],))
        elif group['owner_id'] == user['id']:
            db.execute('UPDATE groups SET owner_id=? WHERE id=?', (next_owner['user_id'], group['id']))

    def state(self, db, session, env, now):
        user, group, scope = self.context(db, session)
        query = parse_qs(env.get('QUERY_STRING', ''))
        try:
            year = int(query.get('year', [now.year])[0])
            month = int(query.get('month', [now.month])[0])
            if not 1900 <= year <= 9998:
                raise ValueError()
            start, end = (d.isoformat() for d in month_bounds(year, month))
        except (ValueError, OverflowError):
            raise APIError('请选择有效年月（1900～9998）。') from None
        repo = LunchRepository(db, scope)
        records = []
        personal = user['scope_id'] if user else scope
        for row in db.execute('SELECT * FROM lunches WHERE scope_id=? AND day>=? AND day<?', (personal, start, end)):
            records.append({**dict(row), 'group_name': None, 'group_id': None})
        if user:
            for row in db.execute('''SELECT l.*,g.name AS group_name,g.id AS group_id FROM memberships m
                JOIN groups g ON g.id=m.group_id JOIN lunches l ON l.scope_id=g.scope_id
                WHERE m.user_id=? AND m.left_at IS NULL AND l.day>=substr(m.joined_at,1,10) AND l.day>=? AND l.day<?''', (user['id'], start, end)):
                records.append(dict(row))
            for row in db.execute('''SELECT l.*,g.name AS group_name,g.id AS group_id FROM memberships m
                JOIN groups g ON g.id=m.group_id JOIN membership_lunches l ON l.membership_id=m.id
                WHERE m.user_id=? AND l.day>=? AND l.day<?''', (user['id'], start, end)):
                records.append(dict(row))
        # Do not expose internal scopes or membership IDs in calendar results.
        records = [{key: row[key] for key in ('day', 'option_name', 'color', 'rerolls', 'chosen_at', 'group_name', 'group_id')} for row in records]
        group_data = None
        if group:
            members = [dict(r) for r in db.execute('''SELECT u.id,u.username,m.joined_at FROM memberships m
                JOIN users u ON u.id=m.user_id WHERE m.group_id=? AND m.left_at IS NULL ORDER BY m.id''', (group['id'],))]
            group_data = {key: group[key] for key in ('id', 'name', 'code', 'owner_id', 'joined_at')}
            group_data['members'] = members
        current_start, current_end = (d.isoformat() for d in month_bounds(now.year, now.month))
        # Count visible days in the actual current month even when browsing other months.
        current_days = {r['day'] for r in db.execute('SELECT day FROM lunches WHERE scope_id=? AND day>=? AND day<?',
                                                   (personal, current_start, current_end))}
        if user:
            current_days.update(r['day'] for r in db.execute('''SELECT l.day FROM memberships m
                JOIN groups g ON g.id=m.group_id JOIN lunches l ON l.scope_id=g.scope_id
                WHERE m.user_id=? AND m.left_at IS NULL AND l.day>=substr(m.joined_at,1,10) AND l.day>=? AND l.day<?''',
                (user['id'], current_start, current_end)))
            current_days.update(r['day'] for r in db.execute('''SELECT l.day FROM memberships m
                JOIN membership_lunches l ON l.membership_id=m.id WHERE m.user_id=? AND l.day>=? AND l.day<?''',
                (user['id'], current_start, current_end)))
        return {'csrf': session['csrf'], 'user': {'id': user['id'], 'username': user['username']} if user else None,
                'group': group_data, 'scope': scope, 'day': now.date().isoformat(), 'timezone': 'UTC+8',
                'today': record_json(repo.record(now.date())), 'options': [asdict(o) for o in repo.options()],
                'records': sorted(records, key=lambda r: (r['day'], r['chosen_at'])), 'year': year, 'month': month,
                'current_month_days': len(current_days)}
