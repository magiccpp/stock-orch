import { useState, useMemo } from 'react';
import { ChevronLeft, ChevronRight, Layers3, ChevronUp, ChevronDown, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { normalizeHoldings, withWeightDeltas, formatPercent, formatDelta } from '../lib/normalizers.js';

function SortIcon({ column, sortCol, sortDir }) {
  if (sortCol !== column) return <span className="sort-icon sort-icon--idle"><ChevronUp size={10} /><ChevronDown size={10} /></span>;
  return sortDir === 'asc'
    ? <span className="sort-icon sort-icon--active"><ChevronUp size={10} /></span>
    : <span className="sort-icon sort-icon--active"><ChevronDown size={10} /></span>;
}

function DeltaBadge({ holding }) {
  if (holding.isNew) return <span className="delta-badge delta-new">New</span>;
  if (holding.delta == null) return null;
  const rounded = Math.round(holding.delta * 100 * 100) / 100;
  if (rounded > 0) return <span className="delta-badge delta-up"><TrendingUp size={11} />{formatDelta(holding.delta)}</span>;
  if (rounded < 0) return <span className="delta-badge delta-down"><TrendingDown size={11} />{formatDelta(holding.delta)}</span>;
  return <span className="delta-badge delta-flat"><Minus size={11} />0.00%</span>;
}

export default function PortfolioPanel({ modelName, history, index, onIndexChange, loading }) {
  const entries = history?.portfolios || [];
  const entry = entries[index];
  const prevHoldings = useMemo(() => normalizeHoldings(entries[index - 1]?.portfolio), [entries, index]);
  const holdings = useMemo(() => withWeightDeltas(normalizeHoldings(entry?.portfolio), prevHoldings), [entry, prevHoldings]);
  const latest = entries.length ? index === entries.length - 1 : false;

  const [sortCol, setSortCol] = useState(null);
  const [sortDir, setSortDir] = useState('asc');

  function handleSort(col) {
    if (sortCol === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir('asc');
    }
  }

  const sorted = useMemo(() => {
    if (!sortCol) return holdings;
    return [...holdings].sort((a, b) => {
      const assetA = a.id.toLowerCase();
      const assetB = b.id.toLowerCase();
      const weightA = a.weight == null ? -Infinity : Math.round(a.weight * 10000) / 100;
      const weightB = b.weight == null ? -Infinity : Math.round(b.weight * 10000) / 100;

      if (sortCol === 'asset') {
        return assetA < assetB ? (sortDir === 'asc' ? -1 : 1) : (sortDir === 'asc' ? 1 : -1);
      }

      if (weightA < weightB) return sortDir === 'asc' ? -1 : 1;
      if (weightA > weightB) return sortDir === 'asc' ? 1 : -1;
      if (assetA < assetB) return -1;
      if (assetA > assetB) return 1;
      return 0; // tie-break: asset, descending
    });
  }, [holdings, sortCol, sortDir]);

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
          <div className="table-row table-head">
            <span className="sortable-col" onClick={() => handleSort('asset')}>Asset<SortIcon column="asset" sortCol={sortCol} sortDir={sortDir} /></span>
            <span className="sortable-col" onClick={() => handleSort('weight')}>Weight<SortIcon column="weight" sortCol={sortCol} sortDir={sortDir} /></span>
            <span>Allocation</span>
          </div>
          {sorted.map((holding) => (
            <div className="table-row" key={holding.key}>
              <div className="asset-cell"><div className="asset-logo">{holding.id.slice(0, 2).toUpperCase()}</div><strong>{holding.id}</strong></div>
              <div className="weight-cell"><strong>{formatPercent(holding.weight)}</strong><DeltaBadge holding={holding} /></div>
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
