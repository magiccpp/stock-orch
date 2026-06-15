import { ChevronLeft, ChevronRight, Layers3 } from 'lucide-react';
import { normalizeHoldings, formatPercent } from '../lib/normalizers.js';

export default function PortfolioPanel({ modelName, history, index, onIndexChange, loading }) {
  const entries = history?.portfolios || [];
  const entry = entries[index];
  const holdings = normalizeHoldings(entry?.portfolio);
  const latest = entries.length ? index === entries.length - 1 : false;

  return (
    <section className="portfolio-panel panel">
      <div className="portfolio-header">
        <div><span className="eyebrow">Portfolio explorer</span><h2>{modelName || 'No model selected'}</h2></div>
        <div className="history-controls">
          <button className="icon-button" disabled={index <= 0 || loading} onClick={() => onIndexChange(index - 1)} aria-label="Previous portfolio"><ChevronLeft size={18} /></button>
          <div className="date-block"><strong>{entry?.portfolio_date || '—'}</strong><span>{latest ? 'Current portfolio' : 'Historical portfolio'}</span></div>
          <button className="icon-button" disabled={index >= entries.length - 1 || loading} onClick={() => onIndexChange(index + 1)} aria-label="Next portfolio"><ChevronRight size={18} /></button>
        </div>
      </div>

      {holdings.length ? (
        <div className="holdings-table">
          <div className="table-row table-head"><span>Asset</span><span>Weight</span><span>Allocation</span></div>
          {holdings.map((holding) => (
            <div className="table-row" key={holding.key}>
              <div className="asset-cell"><div className="asset-logo">{holding.id.slice(0, 2).toUpperCase()}</div><strong>{holding.id}</strong></div>
              <strong>{formatPercent(holding.weight)}</strong>
              <div className="allocation"><div style={{ width: holding.weight == null ? '0%' : `${Math.max(0, Math.min(100, holding.weight * 100))}%` }} /></div>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty-state compact"><Layers3 size={28} /><strong>{loading ? 'Loading portfolio…' : 'No recognizable holdings'}</strong><span>Supported arrays: root array, <code>holdings</code>, <code>positions</code>, <code>stocks</code>, or <code>portfolio</code>.</span></div>
      )}
    </section>
  );
}
