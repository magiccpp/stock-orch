import { useState, useMemo, useEffect } from 'react';
import { ChevronLeft, ChevronRight, Layers3, ChevronUp, ChevronDown, TrendingUp, TrendingDown, Minus, Info, X } from 'lucide-react';
import { normalizeHoldings, withWeightDeltas, formatPercent, formatDelta, findClosestPortfolioIndex } from '../lib/normalizers.js';
import { stockApi } from '../lib/api.js';

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

function NextChangeReturnCell({ value, isCurrent }) {
  if (value == null) return <span className="muted">—</span>;
  const rounded = Math.round(value * 100 * 100) / 100;
  const trend = rounded > 0 ? 'is-up' : rounded < 0 ? 'is-down' : 'is-flat';
  return (
    <div className={`next-change-return ${trend}`}>
      <span>{formatDelta(value)}</span>
      {isCurrent && <span className="muted-hint">so far</span>}
    </div>
  );
}

export default function PortfolioPanel({ modelName, history, index, onIndexChange, loading, rangeSelection, onClearRange }) {
  const entries = history?.portfolios || [];
  const entry = entries[index];
  const inRangeMode = Boolean(rangeSelection);

  const prevHoldings = useMemo(() => normalizeHoldings(entries[index - 1]?.portfolio), [entries, index]);

  // Range mode: union of every asset held by any portfolio that was in
  // effect at some point during the selected range, with a time-weighted
  // average weight -- each portfolio's weight for a stock is multiplied by
  // how many days of the range it was actually in effect for, then
  // normalized against the sum of every such (weight * days) term so the
  // results still add up to 100%.
  const rangeWeights = useMemo(() => {
    if (!inRangeMode || !entries.length) return { ids: [], weights: {} };
    const startIdx = findClosestPortfolioIndex(entries, rangeSelection.start);
    const endIdx = findClosestPortfolioIndex(entries, rangeSelection.end);
    const DAY_MS = 24 * 60 * 60 * 1000;
    const rangeStartMs = new Date(rangeSelection.start).getTime();
    const rangeEndMs = new Date(rangeSelection.end).getTime();

    const ids = new Set();
    const weightedSums = new Map();
    let total = 0;

    for (let i = startIdx; i <= endIdx; i++) {
      const portfolioEntry = entries[i];
      const entryStartMs = Math.max(new Date(portfolioEntry.portfolio_date).getTime(), rangeStartMs);
      const entryEndCandidateMs = portfolioEntry.return_until_date
        ? new Date(portfolioEntry.return_until_date).getTime()
        : rangeEndMs;
      const entryEndMs = Math.min(entryEndCandidateMs, rangeEndMs);
      const durationDays = Math.floor((entryEndMs - entryStartMs) / DAY_MS) + 1;

      normalizeHoldings(portfolioEntry.portfolio).forEach((h) => {
        ids.add(h.id);
        if (h.weight == null || durationDays <= 0) return;
        const term = h.weight * durationDays;
        weightedSums.set(h.id, (weightedSums.get(h.id) || 0) + term);
        total += term;
      });
    }

    const weights = {};
    if (total > 0) {
      for (const [id, sum] of weightedSums) weights[id] = sum / total;
    }
    return { ids: [...ids].sort(), weights };
  }, [inRangeMode, entries, rangeSelection]);
  const rangeAssetIds = rangeWeights.ids;

  const [rangeReturns, setRangeReturns] = useState({});
  useEffect(() => {
    if (!inRangeMode || !modelName || !rangeAssetIds.length) {
      setRangeReturns({});
      return;
    }
    const controller = new AbortController();
    stockApi.getReturnsInRange(rangeAssetIds, rangeSelection.start, rangeSelection.end, controller.signal)
      .then((payload) => setRangeReturns(payload.returns || {}))
      .catch((e) => { if (e.name !== 'AbortError') console.error(e); });
    return () => controller.abort();
  }, [inRangeMode, modelName, rangeAssetIds, rangeSelection]);

  const holdings = useMemo(() => {
    if (inRangeMode) {
      return rangeAssetIds.map((id) => ({
        id,
        key: id,
        weight: rangeWeights.weights[id] ?? null,
        returnUntilNextChange: rangeReturns[id] ?? null,
      }));
    }
    const returnsUntilNextChange = entry?.returns_until_next_change || {};
    return withWeightDeltas(normalizeHoldings(entry?.portfolio), prevHoldings).map((holding) => ({
      ...holding,
      returnUntilNextChange: returnsUntilNextChange[holding.id] ?? null,
    }));
  }, [inRangeMode, rangeAssetIds, rangeWeights, rangeReturns, entry, prevHoldings]);
  const latest = entries.length ? index === entries.length - 1 : false;

  const [sortCol, setSortCol] = useState('weight');
  const [sortDir, setSortDir] = useState('desc');

  const [description, setDescription] = useState('');
  useEffect(() => {
    setDescription('');
    if (!modelName) return;
    const controller = new AbortController();
    stockApi.getDescription(modelName, controller.signal)
      .then((payload) => setDescription(payload.description || ''))
      .catch((e) => { if (e.name !== 'AbortError') console.error(e); });
    return () => controller.abort();
  }, [modelName]);

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
      const returnA = a.returnUntilNextChange == null ? -Infinity : Math.round(a.returnUntilNextChange * 10000) / 100;
      const returnB = b.returnUntilNextChange == null ? -Infinity : Math.round(b.returnUntilNextChange * 10000) / 100;

      if (sortCol === 'asset') {
        return assetA < assetB ? (sortDir === 'asc' ? -1 : 1) : (sortDir === 'asc' ? 1 : -1);
      }

      const valueA = sortCol === 'return' ? returnA : weightA;
      const valueB = sortCol === 'return' ? returnB : weightB;

      if (valueA < valueB) return sortDir === 'asc' ? -1 : 1;
      if (valueA > valueB) return sortDir === 'asc' ? 1 : -1;
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
          {inRangeMode ? (
            <>
              <div className="date-block range-block"><strong>{rangeSelection.start} &rarr; {rangeSelection.end}</strong><span>Selected range</span></div>
              <button className="icon-button" onClick={onClearRange} aria-label="Clear range selection"><X size={16} /></button>
            </>
          ) : (
            <>
              <button className="icon-button" disabled={index <= 0 || loading} onClick={() => onIndexChange(index - 1)} aria-label="Previous portfolio"><ChevronLeft size={18} /></button>
              <div className="date-block"><strong>{entry?.portfolio_date || '—'}</strong><span>{latest ? 'Current portfolio' : 'Historical portfolio'}</span></div>
              <button className="icon-button" disabled={index >= entries.length - 1 || loading} onClick={() => onIndexChange(index + 1)} aria-label="Next portfolio"><ChevronRight size={18} /></button>
            </>
          )}
        </div>
      </div>

      {description && (
        <div className="model-description"><Info size={14} /><span>{description}</span></div>
      )}

      {holdings.length ? (
        <div className="holdings-table">
          <div className="table-row table-head">
            <span className="sortable-col" onClick={() => handleSort('asset')}>Asset<SortIcon column="asset" sortCol={sortCol} sortDir={sortDir} /></span>
            <span className="sortable-col" onClick={() => handleSort('weight')}>{inRangeMode ? 'Avg. weight' : 'Weight'}<SortIcon column="weight" sortCol={sortCol} sortDir={sortDir} /></span>
            <span className="sortable-col" onClick={() => handleSort('return')}>{inRangeMode ? 'Returns in the time range' : 'Return until next change'}<SortIcon column="return" sortCol={sortCol} sortDir={sortDir} /></span>
            <span>Allocation</span>
          </div>
          {sorted.map((holding) => (
            <div className="table-row" key={holding.key}>
              <div className="asset-cell"><div className="asset-logo">{holding.id.slice(0, 2).toUpperCase()}</div><strong>{holding.id}</strong></div>
              <div className="weight-cell"><strong>{formatPercent(holding.weight)}</strong><DeltaBadge holding={holding} /></div>
              <NextChangeReturnCell value={holding.returnUntilNextChange} isCurrent={!inRangeMode && entry?.is_current_portfolio} />
              <div className="allocation"><div style={{ width: holding.weight == null ? '0%' : `${Math.max(0, Math.min(100, holding.weight * 100))}%` }} /></div>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty-state compact">
          <Layers3 size={28} />
          <strong>{loading ? 'Loading portfolio…' : inRangeMode ? 'No holdings found in this range' : 'No recognizable holdings'}</strong>
          <span>
            {inRangeMode
              ? 'No portfolio held any assets during the selected time range.'
              : <>Supported arrays: root array, <code>holdings</code>, <code>positions</code>, <code>stocks</code>, or <code>portfolio</code>.</>}
          </span>
        </div>
      )}
    </section>
  );
}
