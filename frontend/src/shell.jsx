import React from 'react';
import { Landmark, ShieldCheck } from 'lucide-react';

export function Shell({ title, children, sidebar, action }) {
  return <div className="shell">
    <aside className="rail">
      <div className="brand"><span className="brand-mark"><Landmark size={22}/></span>
        <span><strong>狮城银行</strong><small>LION CITY BANK</small></span></div>
      {sidebar}
      <p className="rail-note"><ShieldCheck size={16}/> 本地演示环境</p>
    </aside>
    <main className="workspace">
      <header className="topbar"><span>{title}</span>{action}</header>
      {children}
    </main>
  </div>;
}
