import React from 'react';

const GRADE = { PASS: 'PASS', FAIL: 'FAIL', DRAFT: 'DRAFT' };

function pctText(pct) {
  if (pct == null || Number.isNaN(Number(pct))) return '—';
  return `${Number(pct).toFixed(1)}%`;
}

function Post({ source }) {
  if (!source) return null;
  return (
    <section className="fse-tcard__panel">
      <h3 className="fse-tcard__h">Source <span>{source.author} · {source.post_id}</span></h3>
      <p className="fse-scard__post">{source.text}</p>
      {source.url && <a className="fse-scard__link" href={source.url}>{source.url}</a>}
    </section>
  );
}

function Rules({ rules }) {
  const lines = rules || [];
  return (
    <section className="fse-tcard__panel">
      <h3 className="fse-tcard__h">Rules</h3>
      {lines.length === 0
        ? <span className="fse-tcard__unavailable">No rules on this card.</span>
        : <ul className="fse-scard__rules">{lines.map((line) => <li key={line}>{line}</li>)}</ul>}
    </section>
  );
}

export function BacktestCard({ card, className }) {
  const c = card || {};
  const probs = c.probabilities || [];
  const checks = c.judge || [];
  const equity = c.equity || {};
  const grade = GRADE[c.grade] || 'DRAFT';
  return (
    <section className={'fse-fcard fse-tcard fse-scard' + (className ? ' ' + className : '')} aria-label="Backtest">
      <header className="fse-tcard__head">
        <div className="fse-tcard__tile" data-grade={grade}>{grade === 'DRAFT' ? '—' : grade}</div>
        <div className="fse-tcard__id">
          <div className="fse-tcard__sym">Backtest</div>
          <div className="fse-tcard__sub">P(R before the stop) · judge · equity</div>
        </div>
      </header>
      <div className="fse-tcard__stats">
        {probs.map((item) => (
          <div key={item.label}><span>P({item.label})</span><b>{pctText(item.pct)}</b></div>
        ))}
        {probs.length === 0 && <div><span>P(R)</span><b>—</b></div>}
      </div>
      <div className="fse-tcard__panel">
        <h3 className="fse-tcard__h">Judge</h3>
        {checks.length === 0
          ? <span className="fse-tcard__unavailable">Not judged yet.</span>
          : (
            <div className="fse-scard__judge">
              {checks.map((item) => (
                <div key={item.name} className="fse-scard__check">
                  <span className={item.passed ? 'fse-scard__ok' : 'fse-scard__bad'}>{item.passed ? 'pass' : 'fail'}</span>
                  <b>{item.name}</b>
                  <span>{item.detail}</span>
                </div>
              ))}
            </div>
          )}
      </div>
      <div className="fse-tcard__stats">
        <div><span>Trades</span><b>{equity.trades == null ? '—' : equity.trades}</b></div>
        <div><span>Net</span><b>{equity.net == null ? '—' : equity.net}</b></div>
        <div><span>Expectancy</span><b>{equity.expectancy_r == null ? '—' : `${equity.expectancy_r}R`}</b></div>
      </div>
    </section>
  );
}

export function StrategyCard({ card = {}, className }) {
  const grade = GRADE[card.grade] || 'DRAFT';
  const symbols = (card.symbols || []).join(' ');
  return (
    <article className={'fse-fcard fse-tcard fse-scard' + (grade === 'FAIL' ? ' fse-scard--fail' : '') + (className ? ' ' + className : '')}>
      <header className="fse-tcard__head">
        <div className="fse-tcard__tile" data-grade={grade}>{grade === 'DRAFT' ? '—' : grade}</div>
        <div className="fse-tcard__id">
          <div className="fse-tcard__sym">{card.name || 'Strategy'}<span>{(card.side || '').toUpperCase()}</span></div>
          <div className="fse-tcard__sub">{symbols} · {card.timeframe} · {card.id}</div>
        </div>
        <div className="fse-tcard__pill" style={{ '--pc': grade === 'PASS' ? 'var(--color-bull)' : grade === 'FAIL' ? 'var(--color-bear)' : 'var(--text-secondary)' }}>{grade}</div>
      </header>
      {card.hypothesis && <p className="fse-scard__hypothesis">{card.hypothesis}</p>}
      <div className="fse-tcard__grid">
        <Post source={card.source} />
        <Rules rules={card.rules} />
      </div>
      <BacktestCard card={card} />
    </article>
  );
}
