import React, { useEffect, useRef, useState } from 'react';
import { ChevronRight, ClipboardList, Headset, LogOut, RefreshCw } from 'lucide-react';
import { Shell } from './shell.jsx';
import { api } from './api.js';

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
    try {
      const data = await api('/api/agent/tickets?' + query);
      if (sequence.current !== token) return;
      setItems(data.tickets);
      setSelectedId(id => data.tickets.some(t => t.ticket_id === id) ? id : null);
    } catch (e) {
      if (sequence.current !== token) return;
      if (e.status === 401) expire(); else setError(e.message);
    } finally { if (sequence.current === token) setLoading(false); }
  }
  useEffect(() => { if (auth?.authenticated) load(); }, [auth?.authenticated, status, level]);
  useEffect(() => { setDraft(selected?.agent_reply || ''); }, [selected?.ticket_id, selected?.agent_reply]);
  function select(t) { setSelectedId(t.ticket_id); setNotice(''); }
  async function authSubmit(e) {
    e.preventDefault();
    const fields = new FormData(e.currentTarget);
    setWorking(true); setAuthError('');
    try {
      await api('/api/agent/auth/' + (auth.configured ? 'login' : 'setup'), 'POST', {
        username: fields.get('username').trim(), password: fields.get('password'),
      });
      setAuth({ configured: true, authenticated: true });
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
      <div className="desk-heading"><div><h1>工单工作台</h1><p>查看客户问题并记录处理回复。</p></div>
        <button className="button outline" onClick={load} disabled={loading || working}><RefreshCw size={16}/> 刷新工单</button></div>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="success" role="status">{notice}</p>}
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
          <div className="ticket-list">{items.map(t => <button key={t.ticket_id} className="ticket-row" type="button"
            aria-pressed={selectedId === t.ticket_id} onClick={() => select(t)} disabled={working || loading}>
            <span><strong>{title(t)}</strong><small>#{t.ticket_id} · {t.customer_id} · {t.profile?.category || '客户服务'}</small></span>
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
    </section>}
  </Shell>;
}
