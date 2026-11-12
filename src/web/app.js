const messages = document.querySelector('#messages');
const form = document.querySelector('#form');
const input = document.querySelector('#input');
const send = document.querySelector('#send');
const customer = document.querySelector('#customer');
const restart = document.querySelector('#new-chat');
const suggestions = document.querySelector('#suggestions');
const pendingActions = document.querySelector('#pending-actions');
const transferOptions = document.querySelector('#transfer-options');
const cancelSelection = document.querySelector('#cancel-selection');
const ticketDialog = document.querySelector('#ticket-dialog');
const ticketVerify = document.querySelector('#ticket-verify');
const ticketOtp = document.querySelector('#ticket-otp');
const ticketFeedback = document.querySelector('#ticket-feedback');
const customerTicketList = document.querySelector('#customer-ticket-list');
let sessionId = null;
let pending = null;
let busy = false;

function addMessage(text, role) {
  const row = document.createElement('div');
  row.className = `message-row ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  row.append(bubble);
  messages.append(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function setBusy(value) {
  busy = value;
  input.disabled = value;
  send.disabled = value;
  customer.disabled = value;
  restart.disabled = value;
  suggestions.querySelectorAll('button').forEach(button => { button.disabled = value; });
  pendingActions.querySelectorAll('button').forEach(button => { button.disabled = value; });
}

function setPending(type, answer = '') {
  pending = type;
  input.type = type === 'otp' ? 'password' : 'text';
  input.inputMode = type === 'otp' || type === 'card_last4' ? 'numeric' : 'text';
  input.placeholder = ({otp: '输入 6 位演示验证码', select_transfer: '输入完整交易编号',
    card_last4: '输入银行卡后四位', confirm_freeze: '输入“确认”或“取消”',
    transfer_scope: '输入“同一收款人”或“所有收款人”'})[type] || '输入你想了解的问题…';
  suggestions.hidden = Boolean(type);
  pendingActions.hidden = type !== 'select_transfer' && type !== 'transfer_scope';
  transferOptions.replaceChildren();
  pendingActions.querySelector('span').textContent = type === 'transfer_scope'
    ? '请选择查询范围' : '可选择一笔交易，或取消后继续提问';
  if (type === 'select_transfer') {
    for (const line of answer.split('\n').slice(1)) {
      const reference = line.split(' · ')[0].trim();
      if (!reference || !line.includes(' · ')) continue;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = line;
      button.addEventListener('click', () => submit(reference));
      transferOptions.append(button);
    }
  }
  if (type === 'transfer_scope') {
    for (const choice of ['同一收款人', '所有收款人']) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = choice;
      button.addEventListener('click', () => submit(choice));
      transferOptions.append(button);
    }
  }
}

async function request(url, body) {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '请求失败，请稍后重试。');
  return data;
}

async function streamReply(url, body, bubble) {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!response.ok || !response.body) throw new Error('无法连接服务，请检查服务后重试。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let answer = '';
  let finished = false;
  while (true) {
    const part = await reader.read();
    buffer += decoder.decode(part.value || new Uint8Array(), {stream: !part.done});
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.type === 'error') throw new Error(event.message);
      if (event.type === 'status') bubble.textContent = event.text;
      if (event.type === 'delta') {
        answer += event.text;
        bubble.textContent = answer;
        messages.scrollTop = messages.scrollHeight;
        await new Promise(resolve => setTimeout(resolve, 18));
      }
      if (event.type === 'done') {
        setPending(event.pending, answer);
        finished = true;
      }
    }
    if (part.done) break;
  }
  if (!finished) throw new Error('回复中断，请开始新对话后重试。');
}

async function newChat() {
  setBusy(true);
  try {
    if (ticketDialog.open) ticketDialog.close();
    ticketOtp.value = '';
    customerTicketList.replaceChildren();
    const data = await request('/api/sessions', {customer_id: customer.value});
    sessionId = data.session_id;
    messages.replaceChildren();
    setPending(null);
    addMessage('你好！我是狮城银行的 AI 客服。请问有什么可以帮你？', 'assistant');
  } catch (error) {
    sessionId = null;
    addMessage(error.message, 'error');
  } finally {
    setBusy(false);
    input.focus();
  }
}

function renderTickets(tickets) {
  customerTicketList.replaceChildren();
  if (!tickets.length) {
    ticketFeedback.textContent = '当前演示客户还没有工单。';
    return;
  }
  ticketFeedback.textContent = `共 ${tickets.length} 条工单（最多显示最近 50 条）。`;
  for (const ticket of tickets) {
    const item = document.createElement('article'); item.className = 'customer-ticket';
    const heading = document.createElement('div'); heading.className = 'customer-ticket-heading';
    const number = document.createElement('strong'); number.textContent = `工单 #${ticket.ticket_id}`;
    const status = document.createElement('span');
    status.textContent = ({open:'等待处理', replied:'已有回复', closed:'已关闭'})[ticket.status] || '状态未知';
    heading.append(number, status);
    const created = document.createElement('p'); created.className = 'customer-ticket-time';
    created.textContent = `创建时间：${String(ticket.created_at || '').replace('T', ' ').slice(0, 16) || '—'}`;
    const answer = document.createElement('p'); answer.className = 'customer-ticket-answer';
    answer.textContent = ticket.agent_reply || (ticket.status === 'closed' ? '工单已关闭，暂无坐席回复。' : '暂无坐席回复，请稍后刷新查看。');
    item.append(heading, created, answer); customerTicketList.append(item);
  }
}

async function loadCustomerTickets() {
  if (!sessionId) { ticketFeedback.textContent = '会话不可用，请重新开始对话。'; return; }
  const currentSession = sessionId;
  ticketFeedback.textContent = '正在读取…';
  customerTicketList.replaceChildren();
  try {
    const response = await fetch(`/api/sessions/${currentSession}/tickets`, {cache:'no-store'});
    const data = await response.json();
    if (currentSession !== sessionId) return;
    if (response.status === 403) {
      ticketVerify.hidden = false;
      ticketFeedback.textContent = '';
      ticketOtp.focus();
      return;
    }
    if (!response.ok) throw new Error(data.detail || '工单暂时无法读取。');
    ticketVerify.hidden = true;
    renderTickets(data.tickets);
  } catch (error) { ticketFeedback.textContent = error.message; }
}

document.querySelector('#my-tickets').addEventListener('click', () => {
  ticketVerify.hidden = true; ticketOtp.value = '';
  if (!ticketDialog.open) ticketDialog.showModal();
  loadCustomerTickets();
});
document.querySelector('#ticket-close').addEventListener('click', () => ticketDialog.close());
document.querySelector('#ticket-refresh').addEventListener('click', loadCustomerTickets);
document.querySelector('#ticket-verify-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button'); button.disabled = true;
  ticketFeedback.textContent = '正在验证…';
  try {
    const response = await fetch(`/api/sessions/${sessionId}/tickets/verify`, {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({code:ticketOtp.value})});
    const data = await response.json(); ticketOtp.value = '';
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请输入 6 位验证码。');
    await loadCustomerTickets();
  } catch (error) { ticketFeedback.textContent = error.message; }
  finally { button.disabled = false; }
});

async function submit(text) {
  if (busy || !sessionId || !text.trim()) return;
  const value = text.trim();
  addMessage(pending === 'otp' ? '已输入演示验证码' : value, 'user');
  input.value = '';
  setBusy(true);
  const loading = addMessage('正在处理…', 'assistant loading');
  try {
    await streamReply(`/api/sessions/${sessionId}/messages/stream`, {text: value}, loading.querySelector('.bubble'));
    loading.classList.remove('loading');
  } catch (error) {
    loading.remove();
    addMessage(error.message, 'error');
  } finally {
    setBusy(false);
    input.focus();
  }
}

form.addEventListener('submit', event => { event.preventDefault(); submit(input.value); });
suggestions.addEventListener('click', event => {
  const button = event.target.closest('button[data-message]');
  if (button) submit(button.dataset.message);
});
restart.addEventListener('click', newChat);
cancelSelection.addEventListener('click', () => submit('取消'));
customer.addEventListener('change', newChat);
newChat();
