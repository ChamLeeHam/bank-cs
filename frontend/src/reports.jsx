import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, BarChart3, CalendarDays, Clock3, RefreshCw, ShieldAlert, TicketCheck } from 'lucide-react';
import { api } from './api.js';

const categories = {
  fraud_report: '账户安全', dispute_refund: '交易争议', complaint: '客户投诉',
  card_lost_freeze: '银行卡服务', transfer_status: '转账查询', balance_inquiry: '账户查询',
  card_transaction_inquiry: '银行卡交易', fees_rates: '费用核查',
};
const pad = n => String(n).padStart(2, '0');
const localDate = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
function initialRange(days = 30) {
  const end = new Date(); const start = new Date(end);
  start.setDate(start.getDate() - days + 1);
  return { start: localDate(start), end: localDate(end) };
}
function filledDays(rows, start, end) {
  const values = new Map(rows.map(row => [String(row.day).slice(0, 10), row]));
  const output = [];
  const cursor = new Date(`${start}T12:00:00`), last = new Date(`${end}T12:00:00`);
  while (cursor <= last) {
    const key = localDate(cursor);
    output.push({ day: key, new_count: 0, complaint_count: 0, closed_count: 0, ...values.get(key) });
    cursor.setDate(cursor.getDate() + 1);
  }
  return output;
}
function chartBuckets(days) {
  if (days.length <= 60) return days;
  const weeks = new Map();
  for (const day of days) {
    const date = new Date(`${day.day}T12:00:00`);
    date.setDate(date.getDate() - ((date.getDay() + 6) % 7));
    const key = localDate(date);
    const bucket = weeks.get(key) || { day: key, new_count: 0, complaint_count: 0, closed_count: 0 };
    bucket.new_count += Number(day.new_count || 0);
    bucket.complaint_count += Number(day.complaint_count || 0);
    bucket.closed_count += Number(day.closed_count || 0);
    weeks.set(key, bucket);
  }
  return [...weeks.values()];
}
const count = value => Number(value || 0).toLocaleString('zh-CN');
function duration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return '暂无已结案数据';
  const minutes = Math.round(Number(seconds) / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60), remainder = minutes % 60;
  if (hours < 48) return remainder ? `${hours} 小时 ${remainder} 分钟` : `${hours} 小时`;
  const days = Math.floor(hours / 24), rest = hours % 24;
  return rest ? `${days} 天 ${rest} 小时` : `${days} 天`;
}

export function Reports({ onDrillDown, onUnauthorized }) {
  const [range, setRange] = useState(() => initialRange());
  const [report, setReport] = useState(null), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const requestSequence = useRef(0);
  const days = useMemo(() => filledDays(report?.trend || [], range.start, range.end), [report, range]);
  const buckets = useMemo(() => chartBuckets(days), [days]);
  async function load() {
    const current = ++requestSequence.current;
    setLoading(true); setError(''); setReport(null);
    try {
      const query = new URLSearchParams({ start_date: range.start, end_date: range.end });
      const result = await api(`/api/agent/reports?${query}`);
      if (requestSequence.current === current) setReport(result);
    } catch (e) {
      if (requestSequence.current === current) {
        if (e.status === 401) onUnauthorized(); else setError(e.message);
      }
    } finally { if (requestSequence.current === current) setLoading(false); }
  }
  useEffect(() => {
    load();
    return () => { requestSequence.current++; };
  }, [range.start, range.end]);
  const summary = report?.summary || {};
  const maxTrend = Math.max(1, ...buckets.flatMap(day => [Number(day.new_count), Number(day.complaint_count), Number(day.closed_count)]));
  const maxCategory = Math.max(1, ...(report?.categories || []).map(row => Number(row.count)));
  const drill = filters => onDrillDown({ ...filters, start_date: range.start, end_date: range.end });

  return <section className="reports" aria-label="运营数据报表">
    <header className="report-heading">
      <div><h1>运营数据</h1><p>查看工单规模、处理状态与客户投诉趋势。</p></div>
      <div className="report-controls">
        <label><CalendarDays size={15}/><span>开始</span><input type="date" value={range.start} max={range.end}
          onChange={e => setRange(r => ({ ...r, start: e.target.value }))}/></label>
        <label><span>结束</span><input type="date" value={range.end} min={range.start} max={localDate(new Date())}
          onChange={e => setRange(r => ({ ...r, end: e.target.value }))}/></label>
        <div className="report-presets" aria-label="快速选择日期范围">
          {[7, 30, 90].map(n => <button key={n} className="button outline" type="button"
            aria-pressed={range.start === initialRange(n).start && range.end === initialRange(n).end}
            onClick={() => setRange(initialRange(n))}>{n}天</button>)}
        </div>
        <button className="button outline report-refresh" type="button" onClick={load} disabled={loading} aria-label="刷新报表">
          <RefreshCw size={16}/><span>{loading ? '更新中' : '刷新'}</span>
        </button>
      </div>
    </header>
    <p className="report-scope">统计范围：{range.start} 至 {range.end} · 演示工单数据</p>
    {error && <p className="error" role="alert"><AlertCircle size={16}/>{error}</p>}
    {loading && !report ? <div className="report-loading" role="status">正在汇总工单数据…</div> : report && <>
      <div className="metric-strip" aria-busy={loading}>
        <button type="button" className="metric" onClick={() => drill({})}>
          <span className="metric-label"><BarChart3 size={16}/>新建工单</span><strong>{count(summary.total)}</strong><small>按创建日期统计</small>
        </button>
        <button type="button" className="metric" onClick={() => drill({ intent: 'complaint' })}>
          <span className="metric-label"><AlertCircle size={16}/>投诉率</span>
          <strong>{summary.total ? `${(Number(summary.complaints || 0) / Number(summary.total) * 100).toFixed(1)}%` : '—'}</strong>
          <small>{count(summary.complaints)} 张投诉 ÷ {count(summary.total)} 张工单</small>
        </button>
        <button type="button" className="metric" onClick={() => drill({ status: 'closed' })}>
          <span className="metric-label"><TicketCheck size={16}/>已关闭</span><strong>{count(summary.closed_count)}</strong><small>统计期内创建且当前已关闭</small>
        </button>
        <button type="button" className="metric" onClick={() => drill({ priority: 'urgent' })}>
          <span className="metric-label"><ShieldAlert size={16}/>紧急工单</span><strong>{count(summary.urgent_count)}</strong><small>按创建日期统计</small>
        </button>
        <button type="button" className="metric metric-duration" onClick={() => drill({ status: 'closed', start_date: range.start, end_date: range.end })}>
          <span className="metric-label"><Clock3 size={16}/>平均处理时长</span><strong>{duration(summary.avg_resolution_seconds)}</strong>
          <small>仅统计已关闭工单</small>
        </button>
      </div>

      <div className="report-main-grid">
        <section className="report-panel trend-panel">
          <header><div><h2>工单趋势</h2><p>{buckets.length > 60 ? '每周汇总新建、投诉与关闭数量' : '每日新建、投诉与关闭数量'}</p></div><span className="report-unit">张 / {buckets.length > 60 ? '周' : '日'}</span></header>
          <div className="trend-legend"><span><i className="legend-new"/>新建</span><span><i className="legend-complaint"/>投诉</span><span><i className="legend-closed"/>关闭</span></div>
          <div className="trend-chart" role="img" aria-label={`按${buckets.length > 60 ? '周' : '日'}显示新建工单、投诉和关闭工单数量的柱状图`}>
            {buckets.map((day, index) => <div className="trend-day" key={day.day} title={`${day.day}：新建 ${day.new_count}，投诉 ${day.complaint_count}，关闭 ${day.closed_count}`}>
              <div className="trend-bars">
                <i className="bar-new" style={{ height: `${Math.max(day.new_count ? 5 : 0, Number(day.new_count) / maxTrend * 100)}%` }}/>
                <i className="bar-complaint" style={{ height: `${Math.max(day.complaint_count ? 5 : 0, Number(day.complaint_count) / maxTrend * 100)}%` }}/>
                <i className="bar-closed" style={{ height: `${Math.max(day.closed_count ? 5 : 0, Number(day.closed_count) / maxTrend * 100)}%` }}/>
              </div>
              {(index === 0 || index === buckets.length - 1 || index % Math.max(1, Math.ceil(buckets.length / 6)) === 0) &&
                <small>{day.day.slice(5)}</small>}
            </div>)}
            {!days.some(day => Number(day.new_count) || Number(day.closed_count)) && <p className="chart-empty">该时段暂无工单记录</p>}
          </div>
          <footer>新建与投诉按工单创建日统计；关闭按首次关闭日统计。点击分类或指标可查看对应工单。</footer>
        </section>
        <section className="report-panel category-panel">
          <header><div><h2>工单分类</h2><p>按统计期内创建数量排序</p></div></header>
          <div className="category-bars">
            {(report.categories || []).map(row => <button type="button" className="category-bar-row" key={row.intent}
              onClick={() => drill({ intent: row.intent })}>
              <span className="category-bar-label">{categories[row.intent] || row.intent}</span>
              <span className="category-track"><i style={{ width: `${Number(row.count) / maxCategory * 100}%` }}/></span>
              <strong>{count(row.count)}</strong>
            </button>)}
            {!report.categories?.length && <p className="chart-empty">该时段暂无工单分类数据</p>}
          </div>
          <footer>分类依据工单的一级意图；点击一项可筛选工单列表。</footer>
        </section>
      </div>
      <section className="report-status-line" aria-label="统计期内工单状态">
        <h2>区间内工单状态</h2>
        {[['open', '待处理', summary.open_count], ['in_progress', '处理中', summary.in_progress_count],
          ['replied', '已有回复', summary.replied_count], ['closed', '已关闭', summary.closed_count]].map(([key, label, value]) =>
          <button type="button" key={key} onClick={() => drill({ status: key })}><span>{label}</span><strong>{count(value)}</strong></button>)}
      </section>
    </>}
  </section>;
}
