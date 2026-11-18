import React, { useEffect, useRef, useState } from 'react';
import { BarChart3, ChevronRight, ClipboardList, Clock3, Headset, LogOut, RefreshCw, Ticket, UserRoundCog, Workflow, Send } from 'lucide-react';
import { Shell } from './shell.jsx';
import { api } from './api.js';
import { Reports } from './reports.jsx';

const statuses = { open: '待处理', in_progress: '处理中', replied: '已有回复', closed: '已关闭' };
const names = { complaint: '投诉', fraud_report: '疑似盗刷', dispute_refund: '交易争议',
  card_lost_freeze: '银行卡挂失', transfer_status: '汇款查询', balance_inquiry: '余额查询',
  card_transaction_inquiry: '银行卡交易查询', fees_rates: '费用咨询' };
const title = t => t.profile?.title || names[t.intent] || '客户咨询';
const date = t => t ? t.replace('T', ' ').slice(0, 16) : '—';
const priority = t => t.priority === 'urgent' ? '紧急' : '普通';
const slaLabels = { pre_timeout: '准备超时', timeout: '超时', severe_timeout: '严重超时' };
function slaState(ticket, now, sampledAt) {
  if (ticket.status !== 'open') return null;
  const elapsed = Number(ticket.sla_elapsed_seconds || 0) + Math.max(0, now - sampledAt) / 1000;
  let level = null;
  if (ticket.priority === 'urgent') {
    if (elapsed >= 300) level = 'severe_timeout';
    else if (elapsed >= 240) level = 'pre_timeout';
  } else if (elapsed >= 3600) level = 'severe_timeout';
  else if (elapsed >= 1800) level = 'timeout';
  else if (elapsed >= 1440) level = 'pre_timeout';
  return level ? { level, label: slaLabels[level], minutes: Math.floor(elapsed / 60) } : null;
}
const transactionStatuses = { posted: '已入账', pending: '处理中', completed: '已完成', processing: '处理中', pending_review: '审核中', failed: '失败', returned: '已退回', reversed: '已撤销' };
const transactionDate = value => value ? String(value).replace('T', ' ').slice(0, 16) : '—';
function assignmentText(event) {
  if (event.actor_type === 'supervisor')
    return `${event.actor_username || '主管'} 将工单从 ${event.from_agent || '未分配'} 改派给 ${event.to_agent}`;
  if (event.source === 'ai') return `智能体按未结工单量自动分配给 ${event.to_agent}`;
  return `系统按负载均衡规则分配给 ${event.to_agent}`;
}

export function Agent() {
  const [auth, setAuth] = useState(null), [authError, setAuthError] = useState('');
  const [items, setItems] = useState([]), [selectedId, setSelectedId] = useState(null);
  const [itemsSampledAt, setItemsSampledAt] = useState(Date.now()), [slaNow, setSlaNow] = useState(Date.now());
  const [assignmentHistory, setAssignmentHistory] = useState([]);
  const [assignmentHistoryLoading, setAssignmentHistoryLoading] = useState(false);
  const [status, setStatus] = useState(''), [level, setLevel] = useState('');
  const [view, setView] = useState('tickets');
  const [scope, setScope] = useState('');
  const [users, setUsers] = useState([]);
  const [emailDrafts, setEmailDrafts] = useState({});
  const [automation, setAutomation] = useState(null), [automationLoading, setAutomationLoading] = useState(false);
  const [automationConfirm, setAutomationConfirm] = useState(false), [automationRunning, setAutomationRunning] = useState(false);
  const [retryingTicket, setRetryingTicket] = useState(null);
  const [assignUser, setAssignUser] = useState('');
  const [userFormOpen, setUserFormOpen] = useState(false);
  const [reportFilters, setReportFilters] = useState({ start_date: '', end_date: '', intent: '' });
  const [draft, setDraft] = useState(''), [notice, setNotice] = useState(''), [error, setError] = useState('');
  const [working, setWorking] = useState(false), [loading, setLoading] = useState(false);
  const sequence = useRef(0), detailRef = useRef(null);
  const [closing, setClosing] = useState(false);
  const selected = items.find(t => t.ticket_id === selectedId);
  const profile = selected?.profile || {};
  const selectedSla = selected ? slaState(selected, slaNow, itemsSampledAt) : null;
  async function check() {
    setAuthError('');
    try { setAuth(await api('/api/agent/auth/status')); } catch (e) { setAuthError(e.message); }
  }
  function expire() {
    sequence.current++;
    setItems([]); setSelectedId(null); setDraft(''); setLoading(false);
    setAuth({ configured: true, authenticated: false });
    setAuthError('登录已过期，请重新登录。');
  }
  useEffect(() => { check(); }, []);
  useEffect(() => {
    const timer = window.setInterval(() => setSlaNow(Date.now()), 15000);
    return () => window.clearInterval(timer);
  }, []);
  async function load() {
    const token = ++sequence.current;
    setLoading(true); setError('');
    const query = new URLSearchParams();
    if (status) query.set('status', status);
    if (level) query.set('priority', level);
    if (reportFilters.start_date) query.set('start_date', reportFilters.start_date);
    if (reportFilters.end_date) query.set('end_date', reportFilters.end_date);
    if (reportFilters.intent) query.set('intent', reportFilters.intent);
    try {
      if (scope) query.set('scope', scope);
      const data = await api('/api/agent/tickets?' + query);
      if (sequence.current !== token) return;
      setItemsSampledAt(Date.now());
      setItems(data.tickets);
      setSelectedId(id => data.tickets.some(t => t.ticket_id === id) ? id : null);
    } catch (e) {
      if (sequence.current !== token) return;
      if (e.status === 401) expire(); else setError(e.message);
    } finally { if (sequence.current === token) setLoading(false); }
  }
  useEffect(() => { if (auth?.authenticated && view === 'tickets') load(); },
    [auth?.authenticated, status, level, scope, view, reportFilters.start_date, reportFilters.end_date, reportFilters.intent]);
  useEffect(() => { setDraft(selected?.agent_reply || ''); }, [selected?.ticket_id, selected?.agent_reply]);
  useEffect(() => {
    let active = true;
    setAssignmentHistory([]);
    if (!selected || !auth?.authenticated) { setAssignmentHistoryLoading(false); return () => { active = false; }; }
    setAssignmentHistoryLoading(true);
    api(`/api/agent/tickets/${selected.ticket_id}/assignment-history`)
      .then(data => { if (active) setAssignmentHistory(data.events || []); })
      .catch(e => {
        if (!active) return;
        if (e.status === 401) expire(); else setError(e.message);
      }).finally(() => { if (active) setAssignmentHistoryLoading(false); });
    return () => { active = false; };
  }, [selected?.ticket_id, selected?.assigned_agent, auth?.authenticated]);
  function select(t) {
    setSelectedId(t.ticket_id); setNotice('');
    if (window.matchMedia('(max-width: 760px)').matches)
      requestAnimationFrame(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }
  async function authSubmit(e) {
    e.preventDefault();
    const fields = new FormData(e.currentTarget);
    setWorking(true); setAuthError('');
    try {
      const data = await api('/api/agent/auth/' + (auth.configured ? 'login' : 'setup'), 'POST', {
        username: fields.get('username').trim(), password: fields.get('password'),
      });
      setAuth({ configured: true, authenticated: true, identity: data.identity });
      setScope(data.identity?.role === 'supervisor' ? 'all' : 'queue');
    } catch (err) { setAuthError(err.message); if (err.status === 409) await check(); }
    finally { setWorking(false); }
  }
  async function logout() {
    setWorking(true);
    try {
      await api('/api/agent/auth/logout', 'POST');
      expire(); setAuthError('');
    } catch (e) { setError(e.message); } finally { setWorking(false); }
  }
  async function update(next, confirmed = false) {
    if (!selected || working) return;
    if (next === 'closed' && !confirmed) { setClosing(true); return; }
    setClosing(false);
    setWorking(true); setNotice(''); setError('');
    try {
      await api(`/api/agent/tickets/${selected.ticket_id}`, 'PATCH', {
        status: next, expected_status: selected.status, reply: next === 'replied' ? draft.trim() : null,
      });
      await load();
      setNotice(next === 'in_progress' ? '工单已开始处理。' : next === 'replied' ? '回复已保存，客户验证后可以查看。' : '工单已关闭。');
    } catch (e) {
      if (e.status === 401) expire();
      else {
        if (e.status === 409) await load();
        setError(e.message);
      }
    } finally { setWorking(false); }
  }
  function showTickets(filters = {}) {
    setReportFilters({ start_date: filters.start_date || '', end_date: filters.end_date || '', intent: filters.intent || '' });
    setStatus(filters.status || ''); setLevel(filters.priority || ''); setSelectedId(null); setView('tickets');
  }
  async function loadTeamsAndUsers() {
    if (auth?.identity?.role === 'supervisor') {
      const userResult = await api('/api/agent/users');
      const nextUsers = userResult.users || [];
      setUsers(nextUsers);
      setEmailDrafts(Object.fromEntries(nextUsers.map(u => [u.username, u.email || ''])));
    }
  }
  async function loadAutomation() {
    setAutomationLoading(true); setError('');
    try { setAutomation(await api('/api/agent/automation/preview')); }
    catch (e) { if (e.status === 401) expire(); else setError(e.message); }
    finally { setAutomationLoading(false); }
  }
  async function runAutomation() {
    setAutomationRunning(true); setError(''); setNotice('');
    try {
      const result = await api('/api/agent/automation/run', 'POST', { action: 'notify', confirm: true });
      setAutomationConfirm(false); setNotice(result.message); await loadAutomation();
    } catch (e) { if (e.status === 401) expire(); else setError(e.message); }
    finally { setAutomationRunning(false); }
  }
  async function retryNotification(ticket) {
    const recipients = [
      !ticket.notification_agent_sent_at && '坐席',
      !ticket.notification_supervisor_sent_at && '主管',
    ].filter(Boolean);
    if (!window.confirm(`工单 #${ticket.ticket_id} 将重新加入通知队列。只会发送给尚未成功送达的${recipients.join('和')}；n8n 可能在下一轮定时扫描时发送。确认重试吗？`)) return;
    setRetryingTicket(ticket.ticket_id); setError(''); setNotice('');
    try {
      const result = await api(`/api/agent/tickets/${ticket.ticket_id}/retry-notification`, 'POST');
      setNotice(result.message); await loadAutomation();
    } catch (e) { if (e.status === 401) expire(); else setError(e.message); }
    finally { setRetryingTicket(null); }
  }
  useEffect(() => {
    if (!auth?.authenticated) return;
    if (!scope) setScope(auth.identity?.role === 'supervisor' ? 'all' : 'queue');
    loadTeamsAndUsers().catch(e => setError(e.message));
  }, [auth?.authenticated]);
  useEffect(() => {
    if (auth?.authenticated && auth.identity?.role === 'supervisor' && view === 'automation') loadAutomation();
  }, [auth?.authenticated, auth?.identity?.role, view]);
  useEffect(() => {
    if (selected) setAssignUser(selected.assigned_agent || '');
  }, [selected?.ticket_id, selected?.assigned_agent]);
  async function assignSelected() {
    if (!selected || working) return;
    setWorking(true); setError(''); setNotice('');
    try {
      await api(`/api/agent/tickets/${selected.ticket_id}/assignment`, 'PATCH', { username: assignUser || null });
      await load(); setNotice(`工单 #${selected.ticket_id} 已改派给坐席 ${assignUser}。`);
    } catch (e) { setError(e.message); } finally { setWorking(false); }
  }
  async function createAgentUser(e) {
    e.preventDefault(); const form = e.currentTarget; const fields = new FormData(form);
    setWorking(true); setError(''); setNotice('');
    try {
      await api('/api/agent/users', 'POST', { username: fields.get('username'), password: fields.get('password'), email: fields.get('email') });
      await loadTeamsAndUsers(); form.reset(); setUserFormOpen(false); setNotice('坐席账号已创建。');
    } catch (err) { setError(err.message); } finally { setWorking(false); }
  }
  async function saveUserEmail(username) {
    setWorking(true); setError(''); setNotice('');
    try {
      await api(`/api/agent/users/${encodeURIComponent(username)}/email`, 'PATCH', { email: emailDrafts[username]?.trim() });
      await loadTeamsAndUsers(); setNotice(`${username} 的通知邮箱已保存。`);
    } catch (e) { if (e.status === 401) expire(); else setError(e.message); }
    finally { setWorking(false); }
  }

  return <Shell title="坐席工作台" action={auth?.authenticated && <button className="button outline" onClick={logout} disabled={working}><LogOut size={16}/> 退出登录</button>}>
    {!auth?.authenticated ? <section className="auth-card">
      <div className="welcome-mark"><Headset size={28}/></div>
      <h1>{auth && !auth.configured ? '设置坐席账号' : '坐席登录'}</h1>
      <p className="muted">{auth && !auth.configured ? '创建本机演示账号，密码至少 10 位。' : '使用本机演示账号查看和处理客户工单。'}</p>
      {auth ? <form className="auth-form" onSubmit={authSubmit}>
        <label>账号<input name="username" autoComplete="username" required minLength={3} maxLength={32}
          pattern={String.raw`[a-zA-Z0-9_.\-]+`}/></label>
        <label>密码<input name="password" type="password" required minLength={auth.configured ? undefined : 10}
          maxLength={128} autoComplete={auth.configured ? 'current-password' : 'new-password'}/></label>
        <button className="button primary" disabled={working}>{working ? '正在提交…' : auth.configured ? '登录' : '创建账号'}</button>
      </form> : authError ? <button className="button outline" onClick={check}>重试</button> : <p role="status">正在检查登录状态…</p>}
      {authError && <p className="error" role="alert">{authError}</p>}
    </section> : <section className="desk">
      <div className="desk-heading"><div><h1>坐席工作台</h1><p>{auth.identity?.role === 'supervisor' ? '处理工单、查看运营数据并管理自动化。' : '查看并处理自动分配给你的客户工单。'}</p></div>
        <div className="desk-nav" role="tablist" aria-label="坐席工作台视图">
          <button type="button" role="tab" aria-selected={view === 'tickets'} className={view === 'tickets' ? 'active' : ''}
            onClick={() => setView('tickets')}><Ticket size={16}/>工单处理</button>
          <button type="button" role="tab" aria-selected={view === 'reports'} className={view === 'reports' ? 'active' : ''}
            onClick={() => setView('reports')}><BarChart3 size={16}/>运营报表</button>
          {auth.identity?.role === 'supervisor' && <button type="button" role="tab" aria-selected={view === 'users'} className={view === 'users' ? 'active' : ''}
            onClick={() => setView('users')}><UserRoundCog size={16}/>坐席管理</button>}
          {auth.identity?.role === 'supervisor' && <button type="button" role="tab" aria-selected={view === 'automation'} className={view === 'automation' ? 'active' : ''}
            onClick={() => { setAutomationConfirm(false); setView('automation'); }}><Workflow size={16}/>自动化</button>}
        </div>
        {view === 'tickets' && <button className="button outline" onClick={load} disabled={loading || working}><RefreshCw className={loading ? 'spin-icon' : ''} size={16}/> {loading ? '正在刷新…' : '刷新工单'}</button>}
        {view === 'automation' && <button className="button outline" onClick={loadAutomation} disabled={automationLoading || automationRunning}><RefreshCw className={automationLoading ? 'spin-icon' : ''} size={16}/> {automationLoading ? '正在刷新…' : '刷新状态'}</button>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="success" role="status">{notice}</p>}
      <div className="reports-tab" hidden={view !== 'reports'}>
        <Reports onDrillDown={showTickets} onUnauthorized={expire}/>
      </div>
      {view === 'automation' ? <section className="automation-panel">
        <header className="automation-heading"><div><h2>工单邮件通知</h2><p>先检查待通知队列；手动触发后，邮件由 n8n 工作流发送。</p></div>
          <span className={`automation-status ${automation?.n8n_configured ? 'ready' : 'unconfigured'}`}>{automation?.n8n_configured ? 'Webhook 参数已填写' : 'n8n 未配置'}</span></header>
        {!automation?.n8n_configured && <p className="automation-help" role="status">先在本机设置 n8n Webhook 地址并导入/启用通知工作流。页面目前只提供只读预览，发送操作不可用。</p>}
        <div className="automation-counts" aria-label="通知状态数量">
          {[['pending','待通知'],['processing','处理中'],['sent','已发送'],['failed','失败']].map(([key,label]) => <div key={key}><span>{label}</span><strong>{automationLoading && !automation ? '…' : automation?.counts?.[key] || 0}</strong></div>)}
        </div>
        <div className="automation-queue-heading"><h3>待发送工单</h3><span>{automation?.pending_tickets?.length || 0}{(automation?.counts?.pending || 0) > 25 ? ' / 25' : ''} 条预览</span></div>
        {automationLoading && !automation ? <p className="muted" role="status">正在读取通知队列…</p> : automation?.pending_tickets?.length ? <div className="automation-table-wrap"><table className="automation-table"><thead><tr><th>工单</th><th>分类</th><th>优先级</th><th>创建时间</th></tr></thead><tbody>{automation.pending_tickets.map(t => <tr key={t.ticket_id}><td>#{t.ticket_id}</td><td>{names[t.intent] || '客户咨询'}</td><td>{priority(t)}</td><td>{date(t.created_at)}</td></tr>)}</tbody></table></div> : <p className="automation-empty">当前没有待发送工单。</p>}
        <div className="automation-queue-heading"><h3>可安全重试的失败通知</h3><span>{automation?.retryable_failed_tickets?.length || 0}{(automation?.counts?.failed || 0) > 25 ? ' / 25' : ''} 条</span></div>
        {automation?.retryable_failed_tickets?.length ? <div className="automation-table-wrap"><table className="automation-table"><thead><tr><th>工单</th><th>已送达</th><th>尝试次数</th><th>操作</th></tr></thead><tbody>{automation.retryable_failed_tickets.map(t => <tr key={t.ticket_id}>
          <td>#{t.ticket_id}</td><td>{[t.notification_agent_sent_at && '坐席', t.notification_supervisor_sent_at && '主管'].filter(Boolean).join('、') || '尚无'}</td>
          <td>{t.notification_attempts} / 3</td><td><button className="button outline" disabled={!automation?.n8n_configured || automationRunning || automationLoading || retryingTicket !== null}
            onClick={() => retryNotification(t)}>{retryingTicket === t.ticket_id ? '正在排队…' : '确认重试'}</button></td></tr>)}</tbody></table></div> : <p className="automation-empty">当前没有符合安全重试条件的失败通知。旧版失败工单需先人工核对邮箱送达情况。</p>}
        <div className="automation-actions">
          {!automationConfirm ? <button className="button primary" disabled={!automation?.n8n_configured || automationRunning || !automation?.counts?.pending} onClick={() => setAutomationConfirm(true)}><Send size={16}/>手动发送待通知邮件</button> : <div className="automation-confirm" role="group" aria-label="确认发送通知"><p>将触发 n8n 通知工作流，可能会向客户发送邮件。</p><button className="button primary" disabled={automationRunning} onClick={runAutomation}>{automationRunning ? '正在触发…' : '确认发送'}</button><button className="button outline" disabled={automationRunning} onClick={() => setAutomationConfirm(false)}>取消</button></div>}
          <span>发送结果与失败原因请在 n8n 执行记录中查看。</span>
        </div>
      </section> : view === 'users' ? <section className="user-admin">
        <div className="list-heading"><div><h2>坐席账号</h2><p className="muted">新工单会自动均衡分配；坐席只能查看分配给自己的工单。</p></div>
          <button className="button primary" onClick={() => setUserFormOpen(v => !v)}>{userFormOpen ? '收起' : '添加坐席'}</button></div>
        {userFormOpen && <form className="user-create-form" onSubmit={createAgentUser}>
          <label>坐席账号<input name="username" required minLength={3} maxLength={32} pattern="[A-Za-z0-9_.-]+"/></label>
          <label>初始密码<input name="password" type="password" required minLength={8} maxLength={128}/></label>
          <label>坐席通知邮箱<input name="email" type="email" required maxLength={254}/></label>
          <button className="button primary" disabled={working}>创建账号</button>
        </form>}
        <p className="user-email-note">新工单会分别通知被分配坐席和主管；演示时两者可填写同一个邮箱，邮件主题会区分。</p>
        <div className="user-list">{users.map(u => <div className="user-email-row" key={u.username}>
          <div className="user-email-identity"><strong>{u.username}</strong><span>{u.role === 'supervisor' ? '主管通知邮箱' : '坐席通知邮箱'}</span></div>
          <label><span className="sr-only">{u.username} 的通知邮箱</span><input type="email" required maxLength={254}
            value={emailDrafts[u.username] ?? ''} onChange={e => setEmailDrafts(v => ({ ...v, [u.username]: e.target.value }))}/></label>
          <button className="button outline" type="button" disabled={working || !emailDrafts[u.username]?.trim()}
            onClick={() => saveUserEmail(u.username)}>保存</button>
        </div>)}</div>
      </section> : view === 'tickets' ? <>
      <div className="desk-grid">
        <section className="desk-list" aria-label="工单列表">
          <div className="list-heading"><h2>客户工单</h2><span>{loading ? '正在加载…' : `${items.length} 张`}</span></div>
          <div className="filters">
            <select aria-label="工单状态" value={status} disabled={working} onChange={e => { setStatus(e.target.value); setNotice(''); }}>
              <option value="">全部状态</option>{Object.entries(statuses).map(([key, label]) => <option value={key} key={key}>{label}</option>)}
            </select>
            <select aria-label="工单优先级" value={level} disabled={working} onChange={e => { setLevel(e.target.value); setNotice(''); }}>
              <option value="">全部优先级</option><option value="urgent">紧急</option><option value="normal">普通</option>
            </select>
          </div>
          <p className="sla-legend">首次响应：普通 30 分钟 · 紧急 5 分钟；点“开始处理”即视为响应</p>
          {auth.identity?.role === 'supervisor' ? <div className="scope-tabs" aria-label="工单范围">
            {[['all','全部工单'],['queue','未分配工单']].map(([key,label]) =>
              <button key={key} className={scope === key ? 'active' : ''} onClick={() => { setScope(key); setSelectedId(null); }} type="button">{label}</button>)}
          </div> : <p className="assignment-scope-note">仅显示自动分配给账号 <strong>{auth.identity?.username}</strong> 的工单</p>}
          {loading && items.length > 0 && <div className="load-line" role="status"><span className="spinner"/>正在同步工单列表…</div>}
          <div className="ticket-list" aria-busy={loading}>{loading && items.length === 0 && <AgentTicketSkeleton/>}{items.map(t => {
            const sla = slaState(t, slaNow, itemsSampledAt);
            return <button key={t.ticket_id} className="ticket-row" type="button"
            aria-pressed={selectedId === t.ticket_id} onClick={() => select(t)} disabled={working || loading}>
            <span><strong>{title(t)}</strong><small>#{t.ticket_id} · {t.customer_id} · {t.assigned_agent ? `坐席 ${t.assigned_agent}` : '等待主管分配'}</small><small>{date(t.created_at)}</small></span>
            <span className="row-tags">{t.priority === 'urgent' && <span className="tag urgent">紧急</span>}{sla && <span className={`tag sla sla-${sla.level}`} aria-label={`${sla.label}，已等待 ${sla.minutes} 分钟`}>{sla.label} · {sla.minutes}分</span>}<span className={`tag status-${t.status}`}>{statuses[t.status]}</span></span><ChevronRight size={15}/>
          </button>})}
          {!loading && items.length === 0 && <p className="empty-list">没有符合当前筛选条件的工单。</p>}</div>
        </section>
        <section className="desk-detail" aria-label="工单详情" ref={detailRef}>
          {selected ? <>
            <div className="detail-scroll">
            <div className="detail-heading"><div><p className="ticket-number">{profile.category || '客户服务'} · 工单 #{selected.ticket_id}</p><h2>{profile.title || title(selected)}</h2>
              {profile.sub_intent_label && profile.sub_intent_label !== profile.title &&
                <p className="case-subtitle">具体问题：{profile.sub_intent_label}</p>}</div>
              <div className="case-tags"><span className={`tag ${selected.priority}`}>{priority(selected)}</span>
                {selectedSla && <span className={`tag sla sla-${selectedSla.level}`}>{selectedSla.label} · 已等 {selectedSla.minutes} 分钟</span>}
                <span className={`tag status-${selected.status}`}>{statuses[selected.status]}</span></div></div>
            <dl className="detail-meta"><div><dt>客户</dt><dd>{selected.customer_id}</dd></div>
              <div><dt>身份核验</dt><dd>{profile.identity_verified ? '已通过' : '未验证'}</dd></div>
              <div><dt>创建时间</dt><dd>{date(selected.created_at)}</dd></div></dl>
            <section className="assignment-card" aria-label="工单派单信息">
              <div><span>分配范围</span><strong>坐席专属工单</strong></div>
              <div><span>处理坐席</span><strong>{selected.assigned_agent || '等待主管分配'}</strong></div>
              <div><span>分配方式</span><strong>{selected.dispatch_source === 'ai' ? '智能体均衡分配' : selected.dispatch_source === 'manual' ? '主管改派' : '负载均衡兜底'}</strong></div>
              {auth.identity?.role === 'supervisor' && selected.status !== 'closed' && <div className="assignment-controls">
                <select aria-label="指定坐席" value={assignUser} onChange={e => setAssignUser(e.target.value)}>
                  {users.filter(u => u.role === 'agent').map(u => <option value={u.username} key={u.username}>{u.username}</option>)}</select>
                <button className="button outline" disabled={working || loading} onClick={assignSelected}>{working && <span className="spinner"/>}{working ? '正在改派…' : '确认改派'}</button>
              </div>}
            </section>
            <details className="assignment-history">
              <summary><Clock3 size={16}/> 派单记录（{assignmentHistory.length}）</summary>
              {assignmentHistoryLoading ? <p className="loading-caption"><span className="spinner"/>正在读取派单记录…</p> : assignmentHistory.length ? <ol>{assignmentHistory.map(event => <li key={event.event_id}>
                <span className="assignment-history-dot" aria-hidden="true"/>
                <div><strong>{assignmentText(event)}</strong><time>{date(event.created_at)}</time>
                  {event.note && <small>{event.note}</small>}</div>
              </li>)}</ol> : <p className="muted">暂无派单记录。</p>}
            </details>
            {selected.related_transaction && <section className="related-transaction" aria-label="关联账单交易">
              <header><div><span>账单核查</span><h3>关联交易</h3></div><span className="tag">{selected.related_transaction.kind === 'card' ? '银行卡' : '转账'}</span></header>
              <dl><div><dt>交易编号</dt><dd>{selected.related_transaction.transaction_id}</dd></div>
                <div><dt>交易说明</dt><dd>{selected.related_transaction.description}</dd></div>
                <div><dt>交易金额</dt><dd>{selected.related_transaction.currency} {Number(selected.related_transaction.amount).toFixed(2)}</dd></div>
                <div><dt>账单状态</dt><dd>{transactionStatuses[selected.related_transaction.status] || selected.related_transaction.status}</dd></div>
                <div><dt>交易时间</dt><dd>{transactionDate(selected.related_transaction.occurred_at)}</dd></div>
                {selected.related_transaction.fee_amount != null && <div><dt>记录手续费</dt><dd>{selected.related_transaction.fee_currency || 'SGD'} {Number(selected.related_transaction.fee_amount).toFixed(2)}</dd></div>}
              </dl>
            </section>}
            <section className="case-summary" aria-label="案件摘要">
              <header><h3>案件摘要</h3><span>{profile.summary_source || '历史工单'}</span></header>
              <p>{profile.summary || selected.summary || '暂无摘要。'}</p>
            </section>
            <div className="case-context">
              <section><h3>客户本轮诉求</h3><p>{profile.customer_request || selected.summary || '暂无客户描述。'}</p></section>
              {!!profile.conversation_context?.length && <section><h3>前序对话</h3>
                <ul>{profile.conversation_context.map((line, index) => <li key={index}>{line}</li>)}</ul></section>}
            </div>
            <section className="case-checklist"><h3>坐席处理重点</h3>
              <ol>{(profile.handling_points || ['核对客户描述与相关记录。', '确认客户希望的处理结果。', '向客户说明处理进展和下一步。'])
                .map((point, index) => <li key={index}><span>{index + 1}</span>{point}</li>)}</ol>
            </section>
            </div>
            <div className="reply-dock">
            <label className="reply-label">坐席回复<textarea rows={4} maxLength={2000} value={draft} disabled={selected.status !== 'in_progress' || working}
              onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && draft.trim() && !working && !loading) { e.preventDefault(); update('replied'); } }}
              onChange={e => setDraft(e.target.value)} placeholder={selected.status === 'in_progress' ? '输入客户验证后可见的回复…' : '开始处理后才能填写回复'}/>
              <small className="reply-hint">{draft.length} / 2000{selected.status === 'in_progress' && ' · Ctrl/⌘ + Enter 保存回复'}</small></label>
            <div className="detail-actions">
              {selected.status === 'open' && <button className="button primary" disabled={working || loading} onClick={() => update('in_progress')}>{working && <span className="spinner"/>}{working ? '正在处理中…' : '开始处理'}</button>}
              {selected.status === 'in_progress' && <button className="button primary" disabled={working || loading || !draft.trim()} onClick={() => update('replied')}>{working && <span className="spinner"/>}{working ? '正在保存…' : '保存回复'}</button>}
              <button className="button outline" disabled={working || loading || selected.status === 'closed'} onClick={() => update('closed')}>{working && <span className="spinner"/>}{working ? '正在关闭…' : '关闭工单'}</button>
            </div>
            </div>
          </> : <div className="empty-detail"><ClipboardList size={34} strokeWidth={1.4}/><h2>选择一张工单</h2><p>选择工单后可以查看详情和记录回复。</p></div>}
        </section>
      </div>
      </> : null}
    </section>}
    {closing && selected && <ConfirmClose id={selected.ticket_id} onCancel={() => setClosing(false)} onConfirm={() => update('closed', true)}/>}
  </Shell>;
}

function AgentTicketSkeleton() {
  return <div className="agent-ticket-skeleton" role="status" aria-label="正在加载工单">
    {[0, 1, 2, 3].map(index => <div className="agent-skeleton-row" key={index}>
      <i className="skeleton-block skeleton-title"/><i className="skeleton-block skeleton-meta"/><i className="skeleton-block skeleton-summary"/>
    </div>)}
  </div>;
}

function ConfirmClose({ id, onCancel, onConfirm }) {
  const dialog = useRef(null);
  useEffect(() => { dialog.current.showModal(); }, []);
  return <dialog ref={dialog} className="ticket-dialog confirm-dialog" aria-labelledby="confirm-title" onCancel={onCancel}
    onClick={e => { if (e.target === dialog.current) onCancel(); }}>
    <h2 id="confirm-title">关闭工单 #{id}？</h2>
    <p className="muted">关闭后客户会看到“已关闭”状态，坐席无法再修改回复。</p>
    <div className="dialog-footer confirm-actions">
      <button type="button" className="button outline" autoFocus onClick={onCancel}>取消</button>
      <button type="button" className="button primary" onClick={onConfirm}>确认关闭</button>
    </div>
  </dialog>;
}
