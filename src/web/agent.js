const list = document.querySelector('#list');
const count = document.querySelector('#count');
const statusFilter = document.querySelector('#status-filter');
const priorityFilter = document.querySelector('#priority-filter');
const empty = document.querySelector('#empty-detail');
const detail = document.querySelector('#detail-content');
const feedback = document.querySelector('#feedback');
const reply = document.querySelector('#reply');
const save = document.querySelector('#save');
const close = document.querySelector('#close');
const refresh = document.querySelector('#refresh');
const workspace = document.querySelector('#workspace');
const authPanel = document.querySelector('#auth-panel');
const authForm = document.querySelector('#auth-form');
const username = document.querySelector('#username');
const password = document.querySelector('#password');
const authError = document.querySelector('#auth-error');
const authSubmit = document.querySelector('#auth-submit');
const logout = document.querySelector('#logout');
const names = {complaint:'客户投诉', fraud_report:'疑似盗刷', dispute_refund:'交易争议',
  card_lost_freeze:'银行卡挂失', transfer_status:'汇款查询', balance_inquiry:'余额查询', unknown:'待分类问题'};
const statuses = {open:'待处理', replied:'已记录回复', closed:'已关闭'};
let tickets = [];
let selected = null;
let saving = false;
let needsSetup = false;

function showLogin(configured) {
  needsSetup = !configured;
  tickets = []; selected = null; list.replaceChildren();
  document.querySelector('#detail-summary').textContent = '';
  reply.value = ''; detail.hidden = true; empty.hidden = false;
  workspace.hidden = true; logout.hidden = true; authPanel.hidden = false;
  password.value = '';
  password.autocomplete = needsSetup ? 'new-password' : 'current-password';
  password.minLength = needsSetup ? 10 : 1;
  document.querySelector('#auth-title').textContent = needsSetup ? '设置坐席账号' : '坐席登录';
  document.querySelector('#auth-intro').textContent = needsSetup ? '首次使用，请为本机演示设置一个坐席账号。' : '请输入本机演示账号和密码。';
  document.querySelector('#auth-help').textContent = needsSetup ? '账号使用 3–32 位英文、数字或 . _ -；密码至少 10 个字符。' : '账号和密码仅用于此本地演示。';
  authSubmit.textContent = needsSetup ? '创建账号并登录' : '登录';
  username.focus();
}

async function checkAuth() {
  try {
    const response = await fetch('/api/agent/auth/status', {cache:'no-store'});
    if (!response.ok) throw new Error('无法检查登录状态，请稍后刷新。');
    const state = await response.json();
    if (!state.authenticated) { showLogin(state.configured); return; }
    authPanel.hidden = true; workspace.hidden = false; logout.hidden = false;
    authError.textContent = '';
    await loadTickets();
  } catch (error) {
    showLogin(true); authError.textContent = error.message;
  }
}

function dateText(value) { return value ? String(value).replace('T', ' ').slice(0, 16) : '—'; }
function displayName(ticket) { return names[ticket.intent] || ticket.intent || '待分类问题'; }
function showFeedback(message, error = false) {
  feedback.textContent = message;
  feedback.classList.toggle('error', error);
}
function renderList() {
  list.replaceChildren();
  count.textContent = `显示 ${tickets.length} 条工单（最多 100 条）`;
  if (!tickets.length) {
    const message = document.createElement('p');
    message.className = 'empty-list';
    message.textContent = '没有符合条件的工单。你可以调整筛选条件，或从客服对话创建一张工单。';
    list.append(message);
    return;
  }
  for (const ticket of tickets) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'ticket-item';
    button.setAttribute('aria-pressed', String(selected?.ticket_id === ticket.ticket_id));
    const top = document.createElement('div'); top.className = 'ticket-top';
    const number = document.createElement('span'); number.className = 'ticket-number'; number.textContent = `工单 #${ticket.ticket_id}`;
    const priority = document.createElement('span'); priority.className = `tag ${ticket.priority === 'urgent' ? 'urgent' : ''}`;
    priority.textContent = ticket.priority === 'urgent' ? '紧急' : '普通'; top.append(number, priority);
    const title = document.createElement('div'); title.className = 'ticket-name'; title.textContent = displayName(ticket);
    const bottom = document.createElement('div'); bottom.className = 'ticket-bottom';
    bottom.textContent = `${statuses[ticket.status] || ticket.status}  ·  ${dateText(ticket.created_at)}`;
    button.append(top, title, bottom); button.addEventListener('click', () => selectTicket(ticket)); list.append(button);
  }
}
function selectTicket(ticket) {
  selected = ticket; empty.hidden = true; detail.hidden = false;
  document.querySelector('#detail-id').textContent = `工单 #${ticket.ticket_id}`;
  document.querySelector('#detail-title').textContent = displayName(ticket);
  const priority = document.querySelector('#detail-priority');
  priority.textContent = ticket.priority === 'urgent' ? '紧急' : '普通';
  priority.className = `tag ${ticket.priority === 'urgent' ? 'urgent' : ''}`;
  document.querySelector('#detail-customer').textContent = ticket.customer_id || '—';
  document.querySelector('#detail-status').textContent = statuses[ticket.status] || ticket.status;
  document.querySelector('#detail-created').textContent = dateText(ticket.created_at);
  document.querySelector('#detail-summary').textContent = ticket.summary || '暂无描述';
  reply.value = ticket.agent_reply || '';
  reply.disabled = ticket.status !== 'open';
  save.disabled = ticket.status !== 'open';
  close.disabled = ticket.status === 'closed';
  showFeedback(''); renderList();
}
async function loadTickets(keepSelection = true) {
  const query = new URLSearchParams();
  if (statusFilter.value) query.set('status', statusFilter.value);
  if (priorityFilter.value) query.set('priority', priorityFilter.value);
  count.textContent = '正在加载…';
  try {
    const response = await fetch(`/api/agent/tickets?${query}`);
    const data = await response.json();
    if (response.status === 401) { showLogin(true); authError.textContent = '登录已过期，请重新登录。'; return; }
    if (!response.ok) throw new Error(data.detail || '工单读取失败。');
    tickets = data.tickets;
    const current = keepSelection && tickets.find(t => t.ticket_id === selected?.ticket_id);
    if (current) selectTicket(current);
    else { selected = null; empty.hidden = false; detail.hidden = true; renderList(); }
  } catch (error) {
    tickets = []; selected = null; detail.hidden = true; empty.hidden = false;
    list.replaceChildren();
    const message = document.createElement('p'); message.className = 'empty-list'; message.textContent = error.message;
    list.append(message); count.textContent = '加载失败';
  }
}
async function update(status) {
  if (!selected || saving) return;
  const content = reply.value.trim();
  if (status === 'replied' && !content) { showFeedback('请先填写处理回复。', true); reply.focus(); return; }
  if (status === 'closed' && !window.confirm('确定关闭这张工单吗？')) return;
  saving = true; save.disabled = true; close.disabled = true; showFeedback('正在保存…');
  try {
    const response = await fetch(`/api/agent/tickets/${selected.ticket_id}`, {method:'PATCH',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_status:selected.status,
        status, reply:status === 'replied' ? content : null})});
    const data = await response.json();
    if (response.status === 401) { showLogin(true); authError.textContent = '登录已过期，请重新登录。'; return; }
    if (!response.ok) throw new Error(data.detail || '保存失败，请重试。');
    await loadTickets();
    if (selected) showFeedback(status === 'replied' ? '回复已保存，客户验证后可主动查看。' : '工单已关闭。');
  } catch (error) { showFeedback(error.message, true); }
  finally { saving = false; if (selected) { save.disabled = selected.status !== 'open'; close.disabled = selected.status === 'closed'; } }
}
statusFilter.addEventListener('change', () => loadTickets(false));
priorityFilter.addEventListener('change', () => loadTickets(false));
refresh.addEventListener('click', () => loadTickets());
save.addEventListener('click', () => update('replied'));
close.addEventListener('click', () => update('closed'));
authForm.addEventListener('submit', async event => {
  event.preventDefault(); authSubmit.disabled = true; authError.textContent = '';
  try {
    const response = await fetch(`/api/agent/auth/${needsSetup ? 'setup' : 'login'}`, {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:username.value.trim(), password:password.value})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查账号和密码格式。');
    password.value = ''; await checkAuth();
  } catch (error) { authError.textContent = error.message; }
  finally { authSubmit.disabled = false; }
});
logout.addEventListener('click', async () => {
  logout.disabled = true;
  try {
    const response = await fetch('/api/agent/auth/logout', {method:'POST'});
    if (!response.ok) throw new Error('退出失败，请重试。');
    showLogin(true); authError.textContent = '已退出登录。';
  } catch (error) { showFeedback(error.message, true); }
  finally { logout.disabled = false; }
});
checkAuth();
