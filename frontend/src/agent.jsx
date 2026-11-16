import React, { useEffect, useRef, useState } from 'react';
import { BarChart3, ChevronRight, ClipboardList, Headset, LogOut, RefreshCw, Ticket, UserRoundCog } from 'lucide-react';
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
const transactionStatuses = { posted: '已入账', pending: '处理中', completed: '已完成', processing: '处理中', pending_review: '审核中', failed: '失败', returned: '已退回', reversed: '已撤销' };
const transactionDate = value => value ? String(value).replace('T', ' ').slice(0, 16) : '—';

export function Agent() {
  const [auth, setAuth] = useState(null), [authError, setAuthError] = useState('');
  const [items, setItems] = useState([]), [selectedId, setSelectedId] = useState(null);
  const [status, setStatus] = useState(''), [level, setLevel] = useState('');
  const [view, setView] = useState('tickets');
  const [scope, setScope] = useState('');
  const [users, setUsers] = useState([]);
  const [assignUser, setAssignUser] = useState('');
  const [userFormOpen, setUserFormOpen] = useState(false);
  const [reportFilters, setReportFilters] = useState({ start_date: '', end_date: '', intent: '' });
  const [draft, setDraft] = useState(''), [notice, setNotice] = useState(''), [error, setError] = useState('');
  const [working, setWorking] = useState(false), [loading, setLoading] = useState(false);
  const sequence = useRef(0);
  const selected = items.find(t => t.ticket_id === selectedId);
  const profile = selected?.profile || {};
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
  function select(t) { setSelectedId(t.ticket_id); setNotice(''); }
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
  async function update(next) {
    if (!selected || working) return;
    if (next === 'closed' && !confirm(`确定关闭工单 #${selected.ticket_id}？`)) return;
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
      const userResult = await api('/api/agent/users'); setUsers(userResult.users || []);
    }
  }
  useEffect(() => {
    if (!auth?.authenticated) return;
    if (!scope) setScope(auth.identity?.role === 'supervisor' ? 'all' : 'queue');
    loadTeamsAndUsers().catch(e => setError(e.message));
  }, [auth?.authenticated]);
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
      await api('/api/agent/users', 'POST', { username: fields.get('username'), password: fields.get('password') });
      await loadTeamsAndUsers(); form.reset(); setUserFormOpen(false); setNotice('坐席账号已创建。');
    } catch (err) { setError(err.message); } finally { setWorking(false); }
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
      <div className="desk-heading"><div><h1>坐席工作台</h1><p>查看并处理自动分配给你的客户工单。</p></div>
        <div className="desk-nav" role="tablist" aria-label="坐席工作台视图">
          <button type="button" role="tab" aria-selected={view === 'tickets'} className={view === 'tickets' ? 'active' : ''}
            onClick={() => setView('tickets')}><Ticket size={16}/>工单处理</button>
          <button type="button" role="tab" aria-selected={view === 'reports'} className={view === 'reports' ? 'active' : ''}
            onClick={() => setView('reports')}><BarChart3 size={16}/>运营报表</button>
          {auth.identity?.role === 'supervisor' && <button type="button" role="tab" aria-selected={view === 'users'} className={view === 'users' ? 'active' : ''}
            onClick={() => setView('users')}><UserRoundCog size={16}/>坐席管理</button>}
        </div>
        {view === 'tickets' && <button className="button outline" onClick={load} disabled={loading || working}><RefreshCw size={16}/> 刷新工单</button>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="success" role="status">{notice}</p>}
      {view === 'reports' ? <Reports onDrillDown={showTickets} onUnauthorized={expire}/> : view === 'users' ? <section className="user-admin">
        <div className="list-heading"><div><h2>坐席账号</h2><p className="muted">新工单会自动均衡分配；坐席只能查看分配给自己的工单。</p></div>
          <button className="button primary" onClick={() => setUserFormOpen(v => !v)}>{userFormOpen ? '收起' : '添加坐席'}</button></div>
        {userFormOpen && <form className="user-create-form" onSubmit={createAgentUser}>
          <label>坐席账号<input name="username" required minLength={3} maxLength={32} pattern="[A-Za-z0-9_.-]+"/></label>
          <label>初始密码<input name="password" type="password" required minLength={8} maxLength={128}/></label>
          <button className="button primary" disabled={working}>创建账号</button>
        </form>}
        <div className="user-list">{users.map(u => <div key={u.username}><strong>{u.username}</strong><span>{u.role === 'supervisor' ? '主管' : '自动派单坐席'}</span></div>)}</div>
      </section> : <>
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
          {auth.identity?.role === 'supervisor' ? <div className="scope-tabs" aria-label="工单范围">
            {[['all','全部工单'],['queue','未分配工单']].map(([key,label]) =>
              <button key={key} className={scope === key ? 'active' : ''} onClick={() => { setScope(key); setSelectedId(null); }} type="button">{label}</button>)}
          </div> : <p className="assignment-scope-note">仅显示自动分配给账号 <strong>{auth.identity?.username}</strong> 的工单</p>}
          <div className="ticket-list">{items.map(t => <button key={t.ticket_id} className="ticket-row" type="button"
            aria-pressed={selectedId === t.ticket_id} onClick={() => select(t)} disabled={working || loading}>
            <span><strong>{title(t)}</strong><small>#{t.ticket_id} · {t.customer_id} · {t.assigned_agent ? `坐席 ${t.assigned_agent}` : '等待主管分配'}</small></span>
            <span className={`tag ${t.priority}`}>{priority(t)}</span><ChevronRight size={15}/>
          </button>)}
          {!loading && items.length === 0 && <p className="empty-list">没有符合当前筛选条件的工单。</p>}</div>
        </section>
        <section className="desk-detail" aria-label="工单详情">
          {selected ? <>
            <div className="detail-heading"><div><p className="ticket-number">{profile.category || '客户服务'} · 工单 #{selected.ticket_id}</p><h2>{profile.title || title(selected)}</h2>
              {profile.sub_intent_label && profile.sub_intent_label !== profile.title &&
                <p className="case-subtitle">具体问题：{profile.sub_intent_label}</p>}</div>
              <div className="case-tags"><span className={`tag ${selected.priority}`}>{priority(selected)}</span>
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
                <button className="button outline" disabled={working || loading} onClick={assignSelected}>确认改派</button>
              </div>}
            </section>
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
            <label className="reply-label">坐席回复<textarea rows={5} maxLength={2000} value={draft} disabled={selected.status !== 'in_progress' || working}
              onChange={e => setDraft(e.target.value)} placeholder="输入客户验证后可见的回复…"/></label>
            <div className="detail-actions">
              {selected.status === 'open' && <button className="button primary" disabled={working || loading} onClick={() => update('in_progress')}>开始处理</button>}
              {selected.status === 'in_progress' && <button className="button primary" disabled={working || loading || !draft.trim()} onClick={() => update('replied')}>保存回复</button>}
              <button className="button outline" disabled={working || loading || selected.status === 'closed'} onClick={() => update('closed')}>关闭工单</button>
            </div>
          </> : <div className="empty-detail"><ClipboardList size={34} strokeWidth={1.4}/><h2>选择一张工单</h2><p>选择工单后可以查看详情和记录回复。</p></div>}
        </section>
      </div>
      </>}
    </section>}
  </Shell>;
}
