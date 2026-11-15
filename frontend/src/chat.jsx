import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, ArrowUp, Bell, BookOpen, ChevronRight, CircleHelp, CreditCard, Download, Landmark, Plus, ReceiptText, ShieldCheck, Ticket, Wallet, X, ListChecks } from 'lucide-react';
import { api, streamMessage } from './api.js';
import { Shell } from './shell.jsx';

const promptOptions = [
  ['查看账户余额', '我的账户余额还有多少？', Wallet],
  ['查询汇款进度', '我给妈妈的汇款到了没有？', ReceiptText],
  ['了解转账费用', 'PayNow 转账要手续费吗？', CircleHelp],
  ['银行卡安全', '如果我的卡被盗刷了怎么办？', CreditCard],
];
const pendingLabels = {
  otp: '输入 6 位演示验证码', card_last4: '输入银行卡后四位', confirm_freeze: '确认是否冻结银行卡',
  select_transfer: '输入完整交易编号', transfer_scope: '请选择查询范围',
};
const statusNames = { open: '待处理', in_progress: '处理中', replied: '已有回复', closed: '已关闭' };

export function Chat() {
  const [customer, setCustomer] = useState('C001'), [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]), [draft, setDraft] = useState('');
  const [pending, setPending] = useState(null), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [ticketsOpen, setTicketsOpen] = useState(false), [statementOpen, setStatementOpen] = useState(false);
  const [ticketVerified, setTicketVerified] = useState(false), [initialTicketId, setInitialTicketId] = useState(null);
  const [notifications, setNotifications] = useState([]), [unreadEventIds, setUnreadEventIds] = useState([]), [notificationOpen, setNotificationOpen] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const lock = useRef(false), scroll = useRef(null), messageId = useRef(0);
  const notificationRef = useRef(null), eventSnapshot = useRef(new Map()), notificationLock = useRef(false);

  function acceptTicketBaseline(tickets) {
    eventSnapshot.current = new Map(tickets.map(ticket => [ticket.ticket_id, ticket.latest_event_id]));
    setTicketVerified(true);
    setNotifications([]);
    setUnreadEventIds([]);
  }
  function receiveTicketUpdates(tickets) {
    const changed = tickets.filter(ticket => {
      const previous = eventSnapshot.current.get(ticket.ticket_id);
      return previous != null && ticket.latest_event_id != null && previous !== ticket.latest_event_id;
    });
    eventSnapshot.current = new Map(tickets.map(ticket => [ticket.ticket_id, ticket.latest_event_id]));
    if (!changed.length) return;
    const incoming = changed.map(ticket => ({
      id: ticket.latest_event_id, ticket_id: ticket.ticket_id, event_type: ticket.latest_event_type,
      created_at: ticket.latest_event_at, status: ticket.status, agent_reply: ticket.agent_reply,
    }));
    setNotifications(previous => [...incoming, ...previous.filter(item => !incoming.some(next => next.id === item.id))]
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 12));
    setUnreadEventIds(previous => [...new Set([...previous, ...incoming.map(item => item.id)])]);
  }
  function showTickets(ticketId = null) {
    setInitialTicketId(ticketId);
    setTicketsOpen(true);
    setNotificationOpen(false);
  }
  function toggleNotifications() {
    const opening = !notificationOpen;
    if (opening) setUnreadEventIds([]);
    setNotificationOpen(opening);
  }

  async function start() {
    setSession(null);
    setMessages([]);
    setPending(null);
    setDraft('');
    setError('');
    setAnnouncement('');
    setTicketsOpen(false);
    setStatementOpen(false);
    setTicketVerified(false);
    setInitialTicketId(null);
    setNotifications([]);
    setUnreadEventIds([]);
    setNotificationOpen(false);
    eventSnapshot.current = new Map();
    const data = await api('/api/sessions', 'POST', { customer_id: customer });
    setSession(data.session_id);
    return data.session_id;
  }
  async function reset() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    try { await start(); } catch (e) { setError(e.message); }
    finally { lock.current = false; setBusy(false); }
  }
  useEffect(() => { reset(); }, [customer]);
  const tail = messages.at(-1);
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [messages]);
  useEffect(() => {
    if (!ticketVerified || !session || ticketsOpen) return;
    const refresh = async () => {
      if (notificationLock.current) return;
      notificationLock.current = true;
      try { receiveTicketUpdates((await api(`/api/sessions/${session}/tickets`)).tickets || []); }
      catch { /* Keep the last known notification state if the service is briefly unavailable. */ }
      finally { notificationLock.current = false; }
    };
    const timer = window.setInterval(refresh, 12000);
    return () => window.clearInterval(timer);
  }, [ticketVerified, session, ticketsOpen]);
  useEffect(() => {
    if (!notificationOpen) return;
    const outside = event => { if (!notificationRef.current?.contains(event.target)) setNotificationOpen(false); };
    const escape = event => { if (event.key === 'Escape') setNotificationOpen(false); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [notificationOpen]);

  async function send(value, options = {}) {
    const text = value.trim();
    if (!text || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setAnnouncement('');
    const id = String(++messageId.current);
    const update = (answer) => setMessages(previous => previous.map(m => m.id === id ? { ...m, text: answer } : m));
    try {
      const sid = session || await start();
      setMessages(previous => [...previous,
        { id: id + '-user', role: 'user', text: pending === 'otp' && !/^(取消|cancel)$/i.test(text) ? '已提交验证输入' : text },
        { id, role: 'assistant', text: '' },
      ]);
      setDraft('');
      const reply = options.statementTxnId
        ? await api(`/api/sessions/${sid}/statement/${encodeURIComponent(options.statementTxnId)}/${options.statementAction === 'dispute' ? 'dispute' : 'explanation'}`, options.statementAction === 'dispute' ? 'POST' : 'GET')
        : await streamMessage(sid, text, update);
      if (options.statementTxnId) update(reply.answer);
      setPending(reply.pending);
      setAnnouncement(reply.answer);
    } catch (e) {
      setMessages(previous => previous.filter(m => m.id !== id || m.text));
      setError(e.message);
    } finally { lock.current = false; setBusy(false); }
  }
  let choices = [];
  if (pending === 'select_transfer') choices = (tail?.text || '').split('\n')
    .filter(line => line.includes(' · ')).map(line => [line, line.split(' · ')[0].trim()]);
  if (pending === 'transfer_scope') choices = ['同一收款人', '所有收款人'].map(text => [text, text]);
  if (pending === 'confirm_freeze') choices = [['确认冻结', '确认']];
  if (pending) choices.push(['取消', '取消']);

  return <Shell title="客户服务" sidebar={<div className="customer-controls">
    <label htmlFor="customer">演示客户</label>
    <select id="customer" value={customer} disabled={busy} onChange={e => setCustomer(e.target.value)}>
      <option value="C001">C001 · 陈伟明</option><option value="C002">C002 · Priya Nair</option><option value="C003">C003 · 林美玲</option>
    </select>
    <button className="new-chat" onClick={reset} disabled={busy}><Plus size={17}/> 开始新对话</button>
  </div>} action={<div className="chat-actions">
    <button className="button outline" disabled={!session || busy} onClick={() => setStatementOpen(true)}><ListChecks size={17}/> 账单与交易</button>
    <button className="button outline" disabled={!session || busy} onClick={() => showTickets()}><Ticket size={17}/> 我的工单</button>
    <div className="notification-wrap" ref={notificationRef}>
      <button className={`notification-button ${unreadEventIds.length ? 'has-unread' : ''}`} type="button" disabled={!session}
        aria-label={unreadEventIds.length ? `通知，有 ${unreadEventIds.length} 条未读` : '通知'} aria-expanded={notificationOpen}
        aria-haspopup="dialog" onClick={toggleNotifications}>
        <Bell size={19}/>{unreadEventIds.length > 0 && <span className="notification-badge">{unreadEventIds.length > 9 ? '9+' : unreadEventIds.length}</span>}
      </button>
      {notificationOpen && <section className="notification-panel" role="dialog" aria-label="工单通知">
        <header><div><h2>工单通知</h2><p>{unreadEventIds.length ? `${unreadEventIds.length} 条未读更新` : '客户服务进度'}</p></div>
          <button className="icon-button" type="button" aria-label="关闭通知" onClick={() => setNotificationOpen(false)}><X size={18}/></button></header>
        {!ticketVerified ? <div className="notification-empty"><p>完成演示身份验证后，可在这里查看坐席处理进度和回复。</p>
          <button className="button outline" onClick={() => showTickets()}>验证并查看我的工单</button></div>
          : notifications.length ? <ul>{notifications.slice(0, 8).map(item => <li key={item.id}>
            <button type="button" className="notification-item" onClick={() => showTickets(item.ticket_id)}>
              <span className={`notification-dot ${unreadEventIds.includes(item.id) ? '' : 'read'}`} aria-hidden="true"/><span className="notification-copy"><strong>工单 #{item.ticket_id} · {{ started: '坐席开始处理', replied: '坐席已回复', closed: '工单已关闭' }[item.event_type] || statusNames[item.status]}</strong>
                <small>{{ started: '坐席已开始处理您的工单。', replied: '坐席已更新工单回复。', closed: '您的工单已关闭。' }[item.event_type] || '工单状态有更新。'}</small>
                <time>{item.created_at ? String(item.created_at).replace('T', ' ').slice(0, 16) : ''}</time></span>
            </button>
          </li>)}</ul> : <div className="notification-empty"><p>暂无新的工单进度通知。</p>
            <button className="button outline" onClick={() => showTickets()}>查看我的工单</button></div>}
      </section>}
    </div>
  </div>}>
    <section className="chat">
      <div className="sr-only" role="status">{announcement}</div>
      <div className="chat-body" ref={scroll}>
        {messages.length === 0 ? <div className="welcome">
          <div className="welcome-mark"><Landmark size={33} strokeWidth={1.55}/></div>
          <h1>有什么可以帮你？</h1>
          <p>查询账户、汇款进度，或了解银行服务。</p>
          <div className="quick-actions">{promptOptions.map(([title, text, Icon]) =>
            <button type="button" key={title} disabled={busy} onClick={() => send(text)}>
              <Icon size={19}/><span>{title}</span><ArrowRight size={16}/>
            </button>)}</div>
        </div> : <div className="transcript" aria-label="对话记录">
          {messages.map(m => <Message key={m.id} message={m}/>)}
        </div>}
      </div>
      <div className="compose-area">
        {error && <p className="error compose-error" role="alert">{error}</p>}
        {pending && <div className="pending"><strong>{pendingLabels[pending] || '请按提示继续'}</strong>
          <div>{choices.map(([title, value]) => <button type="button" key={value} className="button outline"
            disabled={busy} onClick={() => send(value)}>{title}</button>)}</div>
        </div>}
        <form className="composer" onSubmit={e => { e.preventDefault(); send(draft); }}>
          <input aria-label="输入消息" autoComplete={pending === 'otp' ? 'one-time-code' : 'off'}
            type={pending === 'otp' ? 'password' : 'text'} inputMode={pending === 'otp' || pending === 'card_last4' ? 'numeric' : 'text'}
            placeholder={pendingLabels[pending] || '输入你的问题…'} maxLength={2000}
            value={draft} onChange={e => setDraft(e.target.value)}/>
          <button className="send" type="submit" aria-label="发送消息" disabled={busy || !draft.trim()}><ArrowUp size={21}/></button>
        </form>
        <p className="privacy"><ShieldCheck size={14}/> 请勿输入真实卡号、密码或验证码。账户和交易均为演示数据。</p>
      </div>
    </section>
    {ticketsOpen && <CustomerTickets session={session} initialTicketId={initialTicketId} notificationReady={ticketVerified}
      onVerified={acceptTicketBaseline} onTicketsUpdated={receiveTicketUpdates}
      onClose={() => { setTicketsOpen(false); setInitialTicketId(null); }}/>}
    {statementOpen && <StatementDialog session={session} onClose={() => setStatementOpen(false)} onAsk={(text, txnId, action) => {
      setStatementOpen(false); send(text, { statementTxnId: txnId, statementAction: action });
    }}/>}
  </Shell>;
}

function Message({ message }) {
  const { role, text } = message;
  const match = role === 'assistant' && /\n\n(?:来源：|Source:\s*)([\s\S]*)$/i.exec(text);
  return <article className={`message ${role}`}>
    {role === 'assistant' && <span className="avatar"><Landmark size={16}/></span>}
    <div><h2 className="message-author">{role === 'user' ? '你' : '狮城银行客服'}</h2>
      <div className="bubble">{match ? text.slice(0, match.index) : text || <span role="status">正在处理…</span>}
        {match && <div className="sources"><BookOpen size={15}/><span>{match[1]}</span></div>}
      </div>
    </div>
  </article>;
}

function CustomerTickets({ session, onClose, initialTicketId, notificationReady, onVerified, onTicketsUpdated }) {
  const dialog = useRef(null), lock = useRef(false), listRef = useRef([]), verifiedRef = useRef(false), initialHandled = useRef(false);
  const [list, setList] = useState([]), [verify, setVerify] = useState(false);
  const [selected, setSelected] = useState(null), [detailLoading, setDetailLoading] = useState(false);
  const [verified, setVerified] = useState(false), [notice, setNotice] = useState('');
  const [code, setCode] = useState(''), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const path = `/api/sessions/${session}/tickets`;
  async function load(otp) {
    if (lock.current) return;
    lock.current = true;
    setLoading(true);
    setError('');
    try {
      if (otp) await api(path + '/verify', 'POST', { code: otp });
      const tickets = (await api(path)).tickets || [];
      listRef.current = tickets;
      setList(tickets);
      if (initialTicketId && !initialHandled.current) {
        const detail = await api(`${path}/${initialTicketId}`);
        setSelected(detail.ticket);
        initialHandled.current = true;
      }
      setVerify(false);
      setVerified(true);
      if (verifiedRef.current || notificationReady) onTicketsUpdated(tickets);
      else onVerified(tickets);
      verifiedRef.current = true;
    } catch (e) { if (!otp && e.status === 403) setVerify(true); else setError(e.message); }
    finally { lock.current = false; setLoading(false); setCode(''); }
  }
  async function openTicket(ticketId) {
    if (lock.current) return;
    lock.current = true; setDetailLoading(true); setError('');
    try { setSelected((await api(`${path}/${ticketId}`)).ticket); }
    catch (e) { setError(e.message); }
    finally { lock.current = false; setDetailLoading(false); }
  }
  useEffect(() => {
    if (!verified) return;
    const poll = async () => {
      if (lock.current) return;
      lock.current = true;
      try {
        const fresh = (await api(path)).tickets || [];
        const previousById = new Map(listRef.current.map(ticket => [ticket.ticket_id, ticket]));
        const changed = fresh.filter(ticket => {
          const old = previousById.get(ticket.ticket_id);
          return old && old.latest_event_id && ticket.latest_event_id !== old.latest_event_id;
        });
        listRef.current = fresh;
        setList(fresh);
        if (changed.length) {
          const id = changed[0].ticket_id;
          setNotice(`${changed.length} 张工单有进度更新${changed.length === 1 ? `（工单 #${id}）` : ''}，详情已同步。`);
        }
        onTicketsUpdated(fresh);
        if (selected) setSelected((await api(`${path}/${selected.ticket_id}`)).ticket);
      } catch { /* Keep the last good state; manual refresh remains available. */ }
      finally { lock.current = false; }
    };
    const timer = window.setInterval(poll, 12000);
    return () => window.clearInterval(timer);
  }, [verified, selected?.ticket_id, path]);
  useEffect(() => { dialog.current.showModal(); load(); }, []);
  return <dialog ref={dialog} className="ticket-dialog" aria-labelledby="ticket-title" onClose={onClose}>
    <div className="dialog-heading"><div>{selected && <button className="ticket-back" type="button" onClick={() => { setSelected(null); setError(''); }}>‹ 返回工单列表</button>}
      <h2 id="ticket-title">{selected ? `工单 #${selected.ticket_id}` : '我的工单'}</h2>
      <p>{selected ? '查看工单进度、处理信息和坐席回复。' : '查看处理进度和坐席回复。'}</p></div>
      <button className="icon-button" type="button" aria-label="关闭" onClick={() => dialog.current.close()}><X size={20}/></button></div>
    {verify && <form className="verify" onSubmit={e => { e.preventDefault(); load(code); }}>
      <label htmlFor="ticket-code">演示验证码</label><input id="ticket-code" type="password" inputMode="numeric" required
        pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)}/>
      <button className="button primary" disabled={loading}>验证并查看</button>
    </form>}
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="ticket-update-notice" role="status">{notice}</p>}
    {loading && <p role="status">正在加载…</p>}
    {detailLoading && <p role="status">正在加载工单详情…</p>}
    {!verify && !loading && !error && !selected && (list.length ? list.map(t => <button className="customer-ticket" key={t.ticket_id} type="button" onClick={() => openTicket(t.ticket_id)}>
      <header><strong>工单 #{t.ticket_id}</strong><span className={`ticket-status status-${t.status}`}>{statusNames[t.status] || t.status}　›</span></header>
      {t.created_at && <small className="muted">{String(t.created_at).replace('T', ' ').slice(0, 16)}</small>}
      <p>{t.agent_reply || (t.status === 'open' ? '工单已提交，等待坐席处理。' : t.status === 'in_progress' ? '坐席正在处理，请稍候。' : '查看工单详情。')}</p>
    </button>) : <p className="muted">当前客户没有工单。</p>)}
    {!verify && selected && <CustomerTicketDetail ticket={selected}/>}
    {!selected && <div className="dialog-footer"><button className="button outline" disabled={loading} onClick={() => load()}>刷新工单</button></div>}
  </dialog>;
}

function CustomerTicketDetail({ ticket }) {
  const detail = ticket.customer_view || {};
  const statusText = statusNames[ticket.status] || ticket.status;
  const money = value => value == null ? '—' : `${value.currency || 'SGD'} ${Number(value.amount).toFixed(2)}`;
  const when = value => value ? String(value).replace('T', ' ').slice(0, 16) : '';
  const eventLabels = { submitted: '工单已提交', started: '坐席开始处理', replied: '坐席已回复', closed: '工单已关闭' };
  const events = ticket.events?.length ? ticket.events : [{ event_type: 'submitted', actor_type: 'system', created_at: ticket.created_at }];
  return <div className="customer-ticket-detail">
    <section className="customer-progress" aria-label="工单处理进度">
      <header><h3>处理进度</h3><span className={`ticket-status status-${ticket.status}`}>{statusText}</span></header>
      <ol>{events.map((event, index) => <li key={`${event.event_type}-${index}`} className={`done ${ticket.status !== 'closed' && index === events.length - 1 ? 'current' : ''}`}>
        <span className="progress-marker">✓</span><div><strong>{eventLabels[event.event_type] || '状态已更新'}</strong>
          <small>{when(event.created_at)}{event.actor_type === 'agent' ? ' · 坐席' : ' · 系统'}</small></div>
      </li>)}</ol>
      {ticket.status !== 'open' && events.length === 1 && <p className="timeline-note">该工单创建于完整流转记录启用前，仅能确认提交时间；当前状态为“{statusText}”。</p>}
    </section>
    <section className="customer-ticket-info"><h3>工单信息</h3>
      <dl><div><dt>问题类型</dt><dd>{detail.title || detail.category || '客户服务'}</dd></div>
        {detail.sub_intent_label && <div><dt>具体问题</dt><dd>{detail.sub_intent_label}</dd></div>}
        <div><dt>优先级</dt><dd>{ticket.priority === 'urgent' ? '紧急' : '普通'}</dd></div>
        <div><dt>创建时间</dt><dd>{when(ticket.created_at) || '—'}</dd></div>
        {ticket.related_transaction && <><div><dt>关联交易</dt><dd>{ticket.related_transaction.transaction_id} · {ticket.related_transaction.description}</dd></div>
          <div><dt>交易金额</dt><dd>{money(ticket.related_transaction)}</dd></div>
          <div><dt>账单状态</dt><dd>{txnStatus[ticket.related_transaction.status] || ticket.related_transaction.status}</dd></div>
          {ticket.related_transaction.fee_amount != null && <div><dt>记录手续费</dt><dd>{money({ amount: ticket.related_transaction.fee_amount, currency: ticket.related_transaction.fee_currency })}</dd></div>}</>}
      </dl>
      <div className="customer-ticket-summary"><h4>问题摘要</h4><p>{detail.summary || '暂无摘要。'}</p></div>
    </section>
    <section className="customer-agent-reply"><h3>坐席回复</h3>
      {ticket.agent_reply ? <p>{ticket.agent_reply}</p> : <p className="muted">坐席处理后，回复会显示在这里。</p>}
    </section>
  </div>;
}

const txnStatus = { posted: '已入账', pending: '处理中', reversed: '已撤销', completed: '已完成', processing: '处理中', pending_review: '审核中', failed: '失败', returned: '已退回' };
const money = (amount, currency) => `${currency} ${Number(amount).toFixed(2)}`;

function StatementDialog({ session, onClose, onAsk }) {
  const dialog = useRef(null), lock = useRef(false);
  const [items, setItems] = useState([]), [verified, setVerified] = useState(false);
  const [selected, setSelected] = useState(null), [detailLoading, setDetailLoading] = useState(false);
  const [code, setCode] = useState(''), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const path = `/api/sessions/${session}/statement`;
  async function load(otp) {
    if (lock.current) return;
    lock.current = true; setLoading(true); setError('');
    try {
      if (otp) await api(`/api/sessions/${session}/tickets/verify`, 'POST', { code: otp });
      setItems((await api(path)).transactions || []); setVerified(true);
    } catch (e) { if (!otp && e.status === 403) setVerified(false); else setError(e.message); }
    finally { lock.current = false; setLoading(false); setCode(''); }
  }
  async function openTransaction(transactionId) {
    if (lock.current) return;
    lock.current = true; setDetailLoading(true); setError('');
    try { setSelected((await api(`${path}/${encodeURIComponent(transactionId)}`)).transaction); }
    catch (e) { setError(e.message); }
    finally { lock.current = false; setDetailLoading(false); }
  }
  useEffect(() => { dialog.current.showModal(); load(); }, []);
  function contextual(txn, intent) {
    const type = txn.kind === 'card' ? '银行卡消费' : txn.description === 'Overseas TT' ? '海外电汇' : `${txn.description} 转账`;
    const card = txn.card_last4 ? `，卡尾号 ${txn.card_last4}` : '';
    const fee = txn.transfer_fee != null && Number(txn.transfer_fee) > 0 ? `，记录手续费 ${money(txn.transfer_fee, 'SGD')}` : '';
    const status = txnStatus[txn.status] || txn.status;
    const base = `交易编号 ${txn.transaction_id}，${type}，金额 ${money(txn.amount, txn.currency)}${card}${fee}，状态：${status}`;
    if (intent === 'ask' && txn.kind === 'transfer' && Number(txn.transfer_fee) > 0) {
      return `我想了解账单中的这笔手续费。${base}。请说明这笔 ${money(txn.transfer_fee, 'SGD')} 是什么费用，以及它和转账当前状态有什么关系。`;
    }
    return intent === 'ask'
      ? `请帮我解释账单中的这笔交易：${base}。`
      : `我想对账单中的这笔交易提出争议并申请人工核查：${base}。请帮我核实这笔交易及相关费用。`;
  }
  return <dialog ref={dialog} className="ticket-dialog statement-dialog" aria-labelledby="statement-title" onClose={onClose}>
    <div className="dialog-heading"><div>{selected && <button className="ticket-back" type="button" onClick={() => { setSelected(null); setError(''); }}>‹ 返回交易列表</button>}
      <h2 id="statement-title">{selected ? '交易详情' : '账单与近期交易'}</h2>
      <p>{selected ? '核对交易信息，或下载本笔交易的电子回单。' : '仅展示演示数据。选择一笔交易查看详情、咨询或申请核查。'}</p></div>
      <button className="icon-button" type="button" aria-label="关闭" onClick={() => dialog.current.close()}><X size={20}/></button></div>
    {!verified && <form className="verify" onSubmit={e => { e.preventDefault(); load(code); }}>
      <label htmlFor="statement-code">演示验证码（默认 123456）</label>
      <input id="statement-code" type="password" inputMode="numeric" required pattern="[0-9]{6}" maxLength={6}
        autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)}/>
      <button className="button primary" disabled={loading}>{loading ? '正在验证…' : '验证并查看账单'}</button>
    </form>}
    {error && <p className="error" role="alert">{error}</p>}
    {loading && verified && <p role="status">正在加载账单…</p>}
    {detailLoading && <p role="status">正在加载交易详情…</p>}
    {verified && !selected && !loading && !error && (items.length ? <div className="statement-list">{items.map(txn => <article className="statement-row" key={txn.transaction_id}>
      <button type="button" className="statement-main statement-open" onClick={() => openTransaction(txn.transaction_id)} aria-label={`查看 ${txn.description} 交易详情`}>
        <div><strong>{txn.description}</strong><span className="tag">{txn.kind === 'card' ? '银行卡' : '转账'}</span></div>
        <small className="muted">{txn.transaction_id} · {txn.occurred_at ? String(txn.occurred_at).replace('T', ' ').slice(0, 16) : ''} · {txnStatus[txn.status] || txn.status}</small>
        <strong className="statement-amount">{money(txn.amount, txn.currency)}</strong>
        {txn.transfer_fee != null && Number(txn.transfer_fee) > 0 && <small className="muted">手续费记录：{money(txn.transfer_fee, 'SGD')}</small>}
        <span className="statement-view">查看详情 <ChevronRight size={15}/></span>
      </button>
    </article>)}</div> : <p className="muted">近期没有可显示的交易。</p>)}
    {selected && <TransactionDetail transaction={selected} session={session} onAsk={(intent) => {
      onAsk(contextual(selected, intent), selected.transaction_id, intent === 'ask' ? 'explanation' : 'dispute');
    }}/>}
  </dialog>;
}

function TransactionDetail({ transaction, session, onAsk }) {
  const card = transaction.kind === 'card';
  const eventTime = value => value ? String(value).replace('T', ' ').slice(0, 16) : '—';
  const typeNames = { 'Overseas TT': '海外电汇', FAST: 'FAST 即时转账', PayNow: 'PayNow 转账', GIRO: 'GIRO 自动转账' };
  const countryNames = { SG: '新加坡', CN: '中国', MY: '马来西亚', US: '美国', GB: '英国', AU: '澳大利亚' };
  const rows = card ? [
    ['交易类型', '银行卡消费'], ['商户', transaction.description], ['交易时间', eventTime(transaction.occurred_at)],
    ['交易编号', transaction.transaction_id], ['交易状态', txnStatus[transaction.status] || transaction.status],
    ['消费国家 / 地区', countryNames[transaction.country] || transaction.country || '未记录'],
    ['银行卡', `${transaction.network || ''} ${transaction.card_type === 'credit' ? '信用卡' : '借记卡'} ···· ${transaction.card_last4 || '****'}`],
    ['手续费', '该交易记录未单列手续费'],
  ] : [
    ['交易类型', typeNames[transaction.description] || transaction.description], ['交易时间', eventTime(transaction.occurred_at)],
    ['交易编号', transaction.transaction_id], ['交易状态', txnStatus[transaction.status] || transaction.status],
    ['付款账户', transaction.debit_account_id ? `${transaction.debit_account_type || '账户'} ···· ${String(transaction.debit_account_id).slice(-4)}` : '未记录'],
    ['收款人', transaction.payee_name || transaction.payee_nickname || '未记录'],
    ['收款账号', transaction.payee_account_masked || '未记录'], ['收款银行', transaction.payee_bank_name || '未记录'],
    ['国家 / 地区', countryNames[transaction.payee_country] || transaction.payee_country || '未记录'],
    ['汇款手续费', money(transaction.transfer_fee || 0, 'SGD')],
  ];
  const downloadUrl = `/api/sessions/${session}/statement/${encodeURIComponent(transaction.transaction_id)}/receipt.pdf`;
  return <div className="transaction-detail">
    <section className="transaction-hero">
      <span className="tag">{card ? '银行卡' : '转账'}</span>
      <p>交易金额</p><strong>{money(transaction.amount, transaction.currency)}</strong>
      <span className={`transaction-status status-${transaction.status}`}>{txnStatus[transaction.status] || transaction.status}</span>
    </section>
    <section className="transaction-info"><h3>交易信息</h3>
      <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      {transaction.status_note_zh && <p className="transaction-status-note">{transaction.status_note_zh}</p>}
    </section>
    <p className="receipt-note">电子回单仅包含演示账单中已有的信息。演示文件不是真实银行凭证。</p>
    <div className="transaction-detail-actions">
      <a className="button primary" href={downloadUrl}><Download size={16}/> 下载电子回单 PDF</a>
      <button className="button outline" onClick={() => onAsk('ask')}>问这笔交易</button>
      <button className="button outline" onClick={() => onAsk('dispute')}>申请核查</button>
    </div>
  </div>;
}
