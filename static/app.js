'use strict';

const $ = (id) => document.getElementById(id);
let state = null, selectedMonth = null, busy = false, polling = false, revision = 0;
let optionSignature = '', groupSignature = '', calendarSignature = '', authMode = 'login';
let toastTimer, resolveConfirm;

function node(tag, className, value) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (value !== undefined) n.textContent = value;
  return n;
}
function palette(name) {
  let hash = 0;
  for (const c of name) hash = (hash * 31 + c.codePointAt(0)) | 0;
  return `color-${Math.abs(hash) % 8}`;
}
function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}
function stored(key) { try { return localStorage.getItem(key); } catch { return null; } }
function store(key, value) { try { localStorage.setItem(key, value); } catch { /* Private browsers may disable storage. */ } }
function monthQuery() {
  return selectedMonth ? `?year=${selectedMonth[0]}&month=${selectedMonth[1]}` : '';
}
async function request(path, payload) {
  const response = await fetch(path, payload === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state?.csrf || ''},
    body: JSON.stringify({...payload, scope: state?.scope}), cache: 'no-store'
  });
  let data;
  try { data = await response.json(); } catch { throw new Error('服务暂时无法访问，请稍后重试。'); }
  if (!response.ok) {
    const error = new Error(data.error || '操作失败，请重试。');
    error.status = response.status;
    throw error;
  }
  return data;
}
function setConnection(connected) {
  $('sync-status').textContent = connected ? (state?.group ? '同桌已同步 · 2 秒刷新' : '已连接 · 自动保存') : '连接中断 · 正在重试';
  $('sync-status').classList.toggle('offline', !connected);
  $('connection-error').hidden = connected;
}
async function refresh({force = false} = {}) {
  if (polling && !force) return;
  polling = true;
  const current = revision;
  const query = monthQuery();
  try {
    const data = await request('/api/state' + query);
    if (current !== revision || query !== monthQuery()) return;
    applyState(data);
    setConnection(true);
  } catch (error) {
    setConnection(false);
    if (force) toast(error.message);
  } finally { polling = false; }
}
async function mutate(path, payload = {}, success = '') {
  if (busy || !state) return false;
  busy = true;
  revision++;
  $('draw-button').disabled = true;
  try {
    const data = await request(path + monthQuery(), payload);
    applyState(data);
    setConnection(true);
    if (success) toast(success);
    return true;
  } catch (error) {
    if (path.startsWith('/api/auth/') && error.status !== 403) $('auth-error').textContent = error.message;
    else toast(error.message);
    if ([401, 403, 409].includes(error.status)) await refresh({force: true});
    return false;
  } finally {
    busy = false;
    if (state) renderToday();
  }
}
function showView(view) {
  for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== `view-${view}`;
  for (const button of document.querySelectorAll('.nav-item')) {
    const active = button.dataset.view === view;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  }
}
document.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));

function applyState(data) {
  const firstState = state === null;
  const expiredAccount = state?.user && !data.user;
  const previousIdentity = state ? `${state.user?.id}:${state.scope}` : null;
  state = data;
  if (!selectedMonth) selectedMonth = [data.year, data.month];
  if (previousIdentity && previousIdentity !== `${data.user?.id}:${data.scope}`) {
    $('day-dialog').close();
    $('search-result').replaceChildren();
    $('new-options').value = '';
  }
  $('username').textContent = data.user?.username || '游客';
  $('avatar').textContent = Array.from(data.user?.username || '客')[0];
  $('account-caption').textContent = data.group ? '和同桌一起吃饭' : '个人午餐空间';
  $('account-button').textContent = data.user ? '退出' : '登录';
  const day = new Date(data.day + 'T12:00:00+08:00');
  $('date-label').textContent = day.toLocaleDateString('zh-CN', {year: 'numeric', month: 'long', day: 'numeric', weekday: 'long', timeZone: 'Asia/Shanghai'});
  $('mode-badge').textContent = data.group ? `同桌 · ${data.group.name}` : '个人餐桌';
  renderToday();
  renderOptions();
  renderCalendar();
  renderGroup();
  if ((firstState && !data.user && stored('w2e-guest') !== '1') || expiredAccount) openAuth();
}

function renderToday() {
  const r = state.today, count = state.options.length, remaining = 3 - (r?.rerolls || 0);
  $('result-caption').textContent = r ? (state.group ? '今天，同桌一起吃' : '今天就吃') : '今天的午餐，等你揭晓';
  $('result').textContent = r ? r.option_name : (count ? '准备好开饭了吗？' : '先加几道想吃的吧');
  $('result-hint').textContent = r ? '已自动保存到午餐日历。祝你吃得开心。' : '从你的午餐清单里，选一份刚刚好的快乐。';
  $('draw-button').textContent = !count && !r ? '＋ 添加午餐选项' : !r ? '帮我选午餐 ↗' : remaining ? '再选一次 ↻' : '今日反悔次数已用完';
  $('draw-button').disabled = busy || Boolean(r && (!remaining || !count));
  $('reroll-status').textContent = r ? `今天还可以反悔 ${remaining} 次${state.group ? ' · 全体成员共用' : ''}` : '首次抽签不消耗反悔机会';
  $('option-count').textContent = count;
  $('remaining-count').textContent = remaining;
  $('record-count').textContent = state.current_month_days;
  $('preview-count').textContent = `${count} 项`;
  const chips = state.options.slice(0, 10).map(o => {
    const chip = node('span', 'food-chip ' + palette(o.name));
    chip.append(node('span', 'dot'), document.createTextNode(o.name));
    return chip;
  });
  if (!count) chips.push(node('p', 'muted small', '还没有选项。去「午餐清单」录入你喜欢的午餐吧。'));
  if (count > 10) chips.push(node('span', 'food-chip', `还有 ${count - 10} 项…`));
  $('option-preview').replaceChildren(...chips);
  $('together-title').textContent = state.group ? `正在和「${state.group.name}」一起吃饭` : '和同桌一起，少一点纠结';
  $('together-caption').textContent = state.group ? `${state.group.members.length} 位同桌 · 午餐结果和反悔次数自动同步` : '创建一个群组，让大家的午餐选择同步发生。';
}

function renderOptions() {
  const signature = JSON.stringify([state.scope, state.options]);
  if (signature === optionSignature) return;
  optionSignature = signature;
  $('list-count').textContent = `${state.options.length} 项`;
  $('options-caption').textContent = state.group ? '这是同桌共用的清单。修改后所有群成员都会同步。' : '把想吃的都放进来，再给喜欢的多一点机会。';
  $('reset-weights').disabled = !state.options.length;
  const rows = state.options.map(o => {
    const row = node('div', 'option-row ' + palette(o.name));
    const name = node('span', 'option-name', o.name);
    const form = node('form', 'weight-form');
    const input = node('input');
    input.type = 'number'; input.min = '0.000001'; input.max = state.options.length === 1 ? '100' : '99.999999'; input.step = '0.000001';
    input.value = Number((o.weight * 100).toFixed(6)); input.required = true; input.disabled = state.options.length === 1;
    input.setAttribute('aria-label', `${o.name} 的权重百分比`);
    const save = node('button', '', '保存'); save.type = 'submit'; save.disabled = input.disabled;
    form.append(input, node('span', 'small muted', '%'), save);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const scope = state.scope;
      if (await confirmAction('调整偏好权重', `将「${o.name}」设为 ${input.value}%，其余选项按原比例调整。`)) {
        if (scope !== state.scope) return toast('群组已变化，请重新操作。');
        await mutate('/api/options/weight', {id: o.id, percent: Number(input.value)}, '偏好已保存');
      }
    });
    const remove = node('button', 'delete-button', '×'); remove.setAttribute('aria-label', `删除 ${o.name}`);
    remove.addEventListener('click', async () => {
      const scope = state.scope;
      if (await confirmAction('删除午餐选项', `从清单中删除「${o.name}」？已有日历会保留。${state.group ? '此操作会同步到所有同桌。' : ''}`)) {
        if (scope !== state.scope) return toast('群组已变化，请重新操作。');
        await mutate('/api/options/delete', {ids: [o.id]}, '已从清单移除');
      }
    });
    row.append(node('span', 'dot'), name, form, remove);
    return row;
  });
  if (!rows.length) rows.push(node('p', 'muted small', '清单还是空的。添加一些喜欢的午餐，就可以开始抽签了。'));
  $('options-list').replaceChildren(...rows);
}

function renderCalendar() {
  const signature = JSON.stringify([state.day, state.year, state.month, state.records]);
  if (signature === calendarSignature) return;
  calendarSignature = signature;
  $('month-input').value = `${state.year}-${String(state.month).padStart(2, '0')}`;
  $('previous-month').disabled = state.year === 1900 && state.month === 1;
  $('next-month').disabled = state.year === 9998 && state.month === 12;
  const start = (new Date(state.year, state.month - 1, 1).getDay() + 6) % 7;
  const days = new Date(state.year, state.month, 0).getDate();
  const cells = [];
  for (let n = 0; n < start; n++) cells.push(node('div', 'calendar-blank'));
  for (let n = 1; n <= days; n++) {
    const day = `${state.year}-${String(state.month).padStart(2, '0')}-${String(n).padStart(2, '0')}`;
    const records = state.records.filter(r => r.day === day);
    const cell = node('button', `calendar-day${records.length ? ' has-record' : ''}${day === state.day ? ' is-today' : ''}`);
    cell.append(node('span', 'day-number', n));
    cell.setAttribute('aria-label', `${day}，${records.length ? records.map(r => `${r.option_name}${r.group_name ? '，群组 ' + r.group_name : ''}`).join('；') : '暂无记录'}`);
    for (const r of records.slice(0, 2)) {
      const tag = node('span', 'calendar-tag ' + palette(r.option_name), r.option_name);
      if (r.group_name) tag.append(node('small', '', r.group_name));
      cell.append(tag);
    }
    if (records.length > 2) cell.append(node('small', 'muted', `另 ${records.length - 2} 条`));
    cell.addEventListener('click', () => showDay(day));
    cells.push(cell);
  }
  while (cells.length % 7) cells.push(node('div', 'calendar-blank'));
  $('calendar-grid').replaceChildren(...cells);
  if ($('day-dialog').open) showDay($('day-dialog').dataset.day, false);
}
function showDay(day, open = true) {
  const records = state.records.filter(r => r.day === day);
  $('day-title').textContent = day + ' 的午餐';
  $('day-dialog').dataset.day = day;
  const details = records.map(r => {
    const block = node('div', 'day-detail ' + palette(r.option_name));
    block.append(node('strong', '', r.option_name));
    if (r.group_name) block.append(node('p', '', `同桌群组：${r.group_name}`));
    block.append(node('p', '', `当天已反悔 ${r.rerolls} 次`));
    return block;
  });
  $('day-records').replaceChildren(...(details.length ? details : [node('p', 'muted', '这一天还没有午餐记录。')]));
  if (open && !$('day-dialog').open) $('day-dialog').showModal();
}
async function changeMonth(year, month) {
  if (year < 1900 || year > 9998 || month < 1 || month > 12) return;
  selectedMonth = [year, month]; revision++;
  await refresh({force: true});
}
$('month-input').addEventListener('change', e => {
  const [year, month] = e.target.value.split('-').map(Number);
  if (Number.isInteger(year) && Number.isInteger(month)) changeMonth(year, month);
});
function offsetMonth(delta) {
  const day = new Date(selectedMonth[0], selectedMonth[1] - 1 + delta, 1);
  changeMonth(day.getFullYear(), day.getMonth() + 1);
}
$('previous-month').onclick = () => offsetMonth(-1);
$('next-month').onclick = () => offsetMonth(1);
$('current-month').onclick = () => changeMonth(...state.day.slice(0, 7).split('-').map(Number));
$('close-day').onclick = () => $('day-dialog').close();

function renderGroup() {
  $('group-guest').hidden = Boolean(state.user);
  $('group-forms').hidden = !state.user || Boolean(state.group);
  $('group-current').hidden = !state.group;
  const signature = JSON.stringify([state.user, state.group]);
  if (signature === groupSignature) return;
  groupSignature = signature;
  if (!state.group) return;
  const g = state.group;
  const header = node('div', 'group-header'), title = node('div');
  title.append(node('p', 'eyebrow', 'YOUR TABLE'), node('h2', '', g.name));
  const leave = node('button', 'button secondary', '退出群组');
  leave.addEventListener('click', async () => {
    const scope = state.scope;
    if (await confirmAction('离开这张餐桌？', '退群后回到个人清单，保留在群期间已有的日历快照，不再接收这个群的新记录。创建者退出时自动移交给最早加入的成员。')) {
      if (scope !== state.scope) return toast('群组已变化，请重新操作。');
      await mutate('/api/groups/leave', {}, '已退出群组，在群期间的日历已保留');
    }
  });
  header.append(title, leave);
  const code = node('p', 'group-code', g.code);
  const copy = node('button', 'text-button', '复制群组号');
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(g.code); toast('群组号已复制'); }
    catch { toast(`群组号：${g.code}，可长按或选中复制`); }
  };
  const members = node('div', 'member-list');
  for (const m of g.members) {
    const member = node('span', 'member', m.username);
    if (m.id === g.owner_id) member.append(node('small', '', '创建者'));
    members.append(member);
  }
  $('group-current').replaceChildren(header, node('p', 'muted small', '把群组号发给同桌，让他们搜索加入。'), code, document.createTextNode('  '), copy,
    node('p', 'muted small', `你于 ${g.joined_at.slice(0, 10)} 入群 · ${g.members.length} 位同桌`), members);
}
$('create-group-form').addEventListener('submit', async e => {
  e.preventDefault();
  if (await mutate('/api/groups/create', {name: $('group-name').value, code: $('create-code').value}, '群组已创建，邀请同桌加入吧')) e.target.reset();
});
$('search-group-form').addEventListener('submit', async e => {
  e.preventDefault();
  const code = $('join-code').value, scope = state.scope;
  $('search-result').replaceChildren(node('p', 'muted small', '正在查找…'));
  try {
    const data = await request('/api/groups/search?code=' + encodeURIComponent(code));
    if (scope !== state.scope || code !== $('join-code').value) return;
    const card = node('div', 'search-card');
    const join = node('button', 'button primary', '加入这张餐桌');
    join.onclick = async () => {
      if (scope !== state.scope) return toast('群组已变化，请重新搜索。');
      if (await mutate('/api/groups/join', {code}, '已加入群组，从今天开始共享午餐')) $('search-result').replaceChildren();
    };
    card.append(node('h3', '', data.group.name), node('p', '', `${data.group.members} 位同桌 · ${data.group.code}`), join);
    $('search-result').replaceChildren(card);
  } catch (error) { $('search-result').replaceChildren(node('p', 'form-error', error.message)); }
});
$('join-code').addEventListener('input', () => $('search-result').replaceChildren());
$('add-form').addEventListener('submit', async e => {
  e.preventDefault();
  const names = $('new-options').value.split(/\r?\n/).map(n => n.trim()).filter(Boolean);
  if (await mutate('/api/options/add', {names}, '午餐清单已更新')) e.target.reset();
});
$('reset-weights').onclick = async () => {
  const scope = state.scope;
  if (await confirmAction('恢复均等权重', '所有午餐选项将获得相同的抽签权重。')) {
    if (scope !== state.scope) return toast('群组已变化，请重新操作。');
    await mutate('/api/options/reset', {}, '已恢复均等权重');
  }
};
$('draw-button').onclick = async () => {
  if (busy || !state) return;
  if (!state.options.length) { showView('options'); $('new-options').focus(); return; }
  const record = state.today, scope = state.scope, day = state.day;
  let confirmed = false;
  if (record?.rerolls >= 1) {
    confirmed = await confirmAction('再给午餐一次机会？', `今天剩余 ${3 - record.rerolls} 次反悔，本次将使用 1 次。${state.group ? '全体同桌共用次数。' : ''}候选不足时可能再次抽到相同结果。`);
    if (!confirmed) return;
  }
  if (scope !== state.scope || day !== state.day) return toast('日期或群组已变化，请重新抽签。');
  await mutate('/api/draw', {day, reroll: Boolean(record), version: record?.version, confirmed}, record ? '新的午餐结果已保存' : '今天的午餐揭晓啦');
};

function confirmAction(title, message) {
  if ($('confirm-dialog').open) return Promise.resolve(false);
  $('confirm-title').textContent = title;
  $('confirm-message').textContent = message;
  $('confirm-dialog').showModal();
  $('confirm-no').focus();
  return new Promise(resolve => { resolveConfirm = resolve; });
}
function finishConfirm(answer) {
  $('confirm-dialog').close();
  resolveConfirm?.(answer); resolveConfirm = null;
}
$('confirm-no').onclick = () => finishConfirm(false);
$('confirm-yes').onclick = () => finishConfirm(true);
$('confirm-dialog').addEventListener('cancel', e => { e.preventDefault(); finishConfirm(false); });
function openAuth() { $('auth-error').textContent = ''; if (!$('auth-dialog').open) $('auth-dialog').showModal(); }
function setAuthMode(mode) {
  authMode = mode;
  $('login-tab').classList.toggle('selected', mode === 'login');
  $('register-tab').classList.toggle('selected', mode === 'register');
  $('auth-password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('auth-submit').textContent = mode === 'login' ? '登录，开始今天的一餐' : '注册并登录';
  $('auth-error').textContent = '';
}
$('login-tab').onclick = () => setAuthMode('login');
$('register-tab').onclick = () => setAuthMode('register');
$('auth-form').addEventListener('submit', async e => {
  e.preventDefault();
  const password = $('auth-password').value;
  if (Array.from(password).length < 4 || Array.from(password).length > 16) {
    $('auth-error').textContent = '密码必须为 4～16 位，字符种类不限。'; return;
  }
  $('auth-submit').disabled = true;
  const ok = await mutate('/api/auth/' + authMode, {username: $('auth-username').value, password, remember: $('remember').checked}, authMode === 'login' ? '欢迎回来' : '注册成功，欢迎入座');
  $('auth-submit').disabled = false;
  if (ok) { $('auth-password').value = ''; $('auth-dialog').close(); store('w2e-guest', '0'); }
});
$('guest-button').onclick = () => { store('w2e-guest', '1'); $('auth-dialog').close(); };
$('auth-dialog').addEventListener('cancel', () => store('w2e-guest', '1'));
$('group-login').onclick = openAuth;
$('account-button').onclick = async () => {
  if (!state?.user) { openAuth(); return; }
  if (await confirmAction('退出登录？', '当前登录凭证会立即失效，账号里的清单、群组和日历会保留。')) {
    if (await mutate('/api/auth/logout', {}, '已退出登录')) { store('w2e-guest', '0'); openAuth(); }
  }
};
document.addEventListener('visibilitychange', () => { if (!document.hidden && !busy) refresh({force: true}); });
window.addEventListener('online', () => refresh({force: true}));
async function init() {
  await refresh({force: true});
  setInterval(() => { if (!document.hidden && !busy) refresh(); }, 2000);
}
init();
