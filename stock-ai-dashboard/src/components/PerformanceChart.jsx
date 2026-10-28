import React, { useState, useEffect, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceArea,
} from 'recharts';
import { Activity, TrendingUp, MousePointerSquareDashed, X } from 'lucide-react';
import { formatPercent } from '../lib/normalizers.js';

// Cool colours for the models first, so they never look like the warm IBKR ones.
const LINE_COLORS = ['#58a6ff', '#a78bfa', '#2dd4bf', '#84cc16', '#22d3ee', '#f59e0b', '#fb7185'];
// Real IBKR lines get their own warm palette so they never share a model's colour.
const IBKR_COLORS = ['#ff8c42', '#ffd60a', '#ff4d9d', '#ff5c5c', '#f8fafc'];
const HINT_DISMISSED_KEY = 'chartDragHintDismissed';
const SINCE_IBKR_KEY = 'chartSinceIbkrStart';
const SP500 = 'S&P 500';

export const ibkrSeriesName = (model) => `${model} · IBKR`;

// The real IBKR curve starts from the theoretical curve's value on the day
// before trading began, so the two lines share a starting point and their
// gap is the live tracking error.
function addIbkrSeries(dateMap, model, returns) {
  let anchor = 0;
  for (const item of returns) {
    if (item.ibkr_cumulative_return == null) {
      if (item.model_cumulative_return != null) anchor = item.model_cumulative_return;
      continue;
    }
    const existing = dateMap.get(item.date) || { date: item.date };
    existing[ibkrSeriesName(model)] = (1 + anchor) * (1 + item.ibkr_cumulative_return) - 1;
    dateMap.set(item.date, existing);
  }
}

// Restart every series at 0% on `baseDate` (the last day before IBKR
// trading began), dropping earlier dates.
function rebase(rows, baseDate, keys) {
  const base = {};
  for (const row of rows) {
    if (row.date > baseDate) break;
    for (const key of keys) if (row[key] != null) base[key] = row[key];
  }
  return rows
    .filter((row) => row.date >= baseDate)
    .map((row) => {
      const next = { date: row.date };
      for (const key of keys) {
        if (row[key] == null) continue;
        const from = key.endsWith(' · IBKR') ? base[key.replace(' · IBKR', '')] : base[key];
        next[key] = (1 + row[key]) / (1 + (from ?? 0)) - 1;
      }
      return next;
    });
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <strong>{label}</strong>
      {payload.map((item) => (
        <div key={item.dataKey}>
          <span style={{ background: item.stroke }} />
          {item.name}: {formatPercent(item.value)}
        </div>
      ))}
    </div>
  );
}

export default function PerformanceChart({ models, loading: externalLoading, onSelectDate, onSelectRange, selectedRange }) {
  const [data, setData] = useState([]);
  const [ibkrBaseDate, setIbkrBaseDate] = useState(null);
  const [liveModels, setLiveModels] = useState([]);
  const [sinceIbkr, setSinceIbkr] = useState(() => localStorage.getItem(SINCE_IBKR_KEY) === 'true');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [drag, setDrag] = useState({ left: null, right: null, active: false });
  const [hintDismissed, setHintDismissed] = useState(() => localStorage.getItem(HINT_DISMISSED_KEY) === 'true');

  function toggleSinceIbkr() {
    setSinceIbkr((current) => {
      localStorage.setItem(SINCE_IBKR_KEY, String(!current));
      return !current;
    });
  }

  function dismissHint() {
    setHintDismissed(true);
    localStorage.setItem(HINT_DISMISSED_KEY, 'true');
  }

  function handleMouseDown(chartState) {
    if (!chartState?.activeLabel) return;
    setDrag({ left: chartState.activeLabel, right: chartState.activeLabel, active: true });
  }

  function handleMouseMove(chartState) {
    if (!drag.active || !chartState?.activeLabel) return;
    setDrag((current) => ({ ...current, right: chartState.activeLabel }));
  }

  function finishDrag() {
    if (!drag.active) return;
    const { left, right } = drag;
    setDrag({ left: null, right: null, active: false });
    if (!left) return;
    if (right && right !== left) {
      const [start, end] = left < right ? [left, right] : [right, left];
      onSelectRange?.(start, end);
    } else {
      onSelectDate?.(left);
    }
  }

  useEffect(() => {
    if (!models || models.length === 0) {
      setData([]);
      return;
    }

    const fetchReturnsData = async () => {
      setLoading(true);
      setError(null);
      
      try {
        // Fetch returns data for all models
        const modelReturnsPromises = models.map(async (model) => {
          const response = await fetch(`/api/models/${encodeURIComponent(model)}/returns`);
          if (!response.ok) {
            throw new Error(`Failed to fetch returns for ${model}: ${response.statusText}`);
          }
          const data = await response.json();
          return { model, returns: data.returns, ibkrStart: data.ibkr_start_date };
        });

        const allModelReturns = await Promise.all(modelReturnsPromises);

        // Also fetch S&P 500 data from the first model (they should all have the same S&P 500 data)
        const sp500Data = allModelReturns[0]?.returns || [];

        // Combine data from all models by date
        const dateMap = new Map();

        // Add S&P 500 data
        sp500Data.forEach(item => {
          if (item.sp500_cumulative_return == null) return;
          dateMap.set(item.date, {
            date: item.date,
            [SP500]: item.sp500_cumulative_return
          });
        });

        // Add each model's data
        allModelReturns.forEach(({ model, returns }) => {
          returns.forEach(item => {
            if (item.model_cumulative_return == null) return;
            const existing = dateMap.get(item.date) || { date: item.date };
            existing[model] = item.model_cumulative_return;
            dateMap.set(item.date, existing);
          });
        });

        // Add the real IBKR curve for models that trade there
        const live = allModelReturns.filter(({ ibkrStart }) => ibkrStart);
        live.forEach(({ model, returns }) => addIbkrSeries(dateMap, model, returns));

        // Convert to array and sort by date
        const combinedData = Array.from(dateMap.values())
          .sort((a, b) => new Date(a.date) - new Date(b.date));

        // Base date for the "since IBKR start" view: the last trading day
        // before the earliest IBKR start among the selected models.
        const firstStart = live.map(({ ibkrStart }) => ibkrStart).sort()[0];
        const before = firstStart ? combinedData.filter((row) => row.date < firstStart) : [];
        setIbkrBaseDate(before.length ? before[before.length - 1].date : firstStart || null);
        setLiveModels(live.map(({ model }) => model));
        setData(combinedData);
        
      } catch (err) {
        console.error('Error fetching returns data:', err);
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchReturnsData();
  }, [models]);

  const isLoading = loading || externalLoading;
  const hasLive = liveModels.length > 0 && ibkrBaseDate;
  const seriesKeys = [SP500, ...models, ...liveModels.map(ibkrSeriesName)];
  const chartData = useMemo(
    () => (sinceIbkr && hasLive ? rebase(data, ibkrBaseDate, seriesKeys) : data),
    // seriesKeys is derived from models/liveModels
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, sinceIbkr, hasLive, ibkrBaseDate, models, liveModels],
  );
  const hasData = chartData.length > 0 && !error;
  const shortRange = chartData.length < 90;

  // While actively dragging, preview the in-progress selection. Otherwise
  // fall back to the persisted selection from the parent, so the
  // highlighted area stays visible after mouseup until a new plain click
  // (or an explicit clear) resets it.
  const displayRange = drag.active
    ? (drag.left && drag.right && drag.left !== drag.right ? { left: drag.left, right: drag.right } : null)
    : (selectedRange ? { left: selectedRange.start, right: selectedRange.end } : null);

  return (
    <section className="chart-panel panel">
      <div className="panel-header">
        <div>
          <span className="eyebrow">Performance</span>
          <h1>Cumulative Return</h1>
          <p>
            Historical strategy performance compared to S&P 500 benchmark.
            {hasLive && ' Dotted lines are real returns from the IBKR paper account.'}
          </p>
        </div>
        <div className="chart-actions">
        {hasLive && (
          <button
            className={`range-toggle ${sinceIbkr ? 'active' : ''}`}
            onClick={toggleSinceIbkr}
            title={`Restart all curves at 0% on ${ibkrBaseDate}`}
          >
            {sinceIbkr ? 'Showing since IBKR start' : 'Since IBKR start'}
          </button>
        )}
        <div className="live-pill">
          {isLoading ? (
            <>
              <Activity size={14} className="animate-spin" /> Loading
            </>
          ) : error ? (
            <>
              <TrendingUp size={14} /> Error
            </>
          ) : (
            <>
              <Activity size={14} /> API connected
            </>
          )}
        </div>
        </div>
      </div>

      {hasData && !hintDismissed && (onSelectDate || onSelectRange) && (
        <div className="chart-hint">
          <MousePointerSquareDashed size={14} />
          <span>Click a date to jump the portfolio explorer there, or <strong>drag across the chart</strong> to inspect a whole date range.</span>
          <button className="chart-hint-close" onClick={dismissHint} aria-label="Dismiss hint"><X size={13} /></button>
        </div>
      )}

      <div className="chart-wrap">
        {error ? (
          <div className="error-state">
            <TrendingUp size={30} />
            <strong>Error loading returns data</strong>
            <span>{error}</span>
          </div>
        ) : hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={chartData}
              margin={{ top: 12, right: 18, left: 2, bottom: 4 }}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={finishDrag}
              onMouseLeave={() => setDrag({ left: null, right: null, active: false })}
              style={{ cursor: onSelectDate || onSelectRange ? 'crosshair' : undefined, userSelect: 'none' }}
            >
              <CartesianGrid 
                strokeDasharray="3 6" 
                vertical={false} 
                stroke="rgba(148,163,184,.16)" 
              />
              <XAxis 
                dataKey="date" 
                tick={{ fill: '#8fa1b8', fontSize: 12 }} 
                tickLine={false} 
                axisLine={false} 
                minTickGap={32}
                tickFormatter={(value) => {
                  const date = new Date(value);
                  return date.toLocaleDateString('en-US', shortRange
                    ? { month: 'short', day: 'numeric' }
                    : { month: 'short', year: '2-digit' });
                }}
              />
              <YAxis 
                // One decimal for fractional ticks (a short IBKR range has 0.5% steps).
                tickFormatter={(v) => {
                  const pct = v * 100;
                  return `${pct.toFixed(Math.abs(pct - Math.round(pct)) < 1e-6 ? 0 : 1)}%`;
                }}
                tick={{ fill: '#8fa1b8', fontSize: 12 }} 
                tickLine={false} 
                axisLine={false} 
                width={54} 
              />
              <Tooltip content={<ChartTooltip />} />
              <Legend 
                iconType="circle" 
                wrapperStyle={{ 
                  color: '#aab7c8', 
                  fontSize: 12, 
                  paddingTop: 12 
                }} 
              />
              
              {/* S&P 500 benchmark line */}
              <Line 
                key="S&P 500"
                type="monotone" 
                dataKey="S&P 500" 
                name="S&P 500" 
                stroke="#6b7280" 
                strokeWidth={2} 
                strokeDasharray="5 5"
                dot={false} 
                connectNulls 
                activeDot={{ r: 4 }} 
              />
              
              {/* Model lines */}
              {models.map((model, index) => (
                <Line 
                  key={model} 
                  type="monotone" 
                  dataKey={model} 
                  name={model} 
                  stroke={LINE_COLORS[index % LINE_COLORS.length]} 
                  strokeWidth={2.4} 
                  dot={false} 
                  connectNulls 
                  activeDot={{ r: 5 }}
                />
              ))}

              {/* Real IBKR lines */}
              {liveModels.map((model, index) => (
                <Line
                  key={ibkrSeriesName(model)}
                  type="monotone"
                  dataKey={ibkrSeriesName(model)}
                  name={ibkrSeriesName(model)}
                  stroke={IBKR_COLORS[index % IBKR_COLORS.length]}
                  strokeWidth={2.6}
                  strokeDasharray="2 4"
                  strokeLinecap="round"
                  // Dots while the real history is short, so a first day or two is visible at all.
                  dot={chartData.filter((row) => row[ibkrSeriesName(model)] != null).length < 90
                    ? { r: 3.5, fill: IBKR_COLORS[index % IBKR_COLORS.length] }
                    : false}
                  connectNulls
                  activeDot={{ r: 5 }}
                />
              ))}

              {displayRange && (
                <ReferenceArea x1={displayRange.left} x2={displayRange.right} strokeOpacity={0.4} stroke="#60a5fa" fill="#60a5fa" fillOpacity={0.12} />
              )}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="empty-state">
            <Activity size={30} />
            <strong>No return data available</strong>
            <span>
              {models?.length > 0 
                ? 'Loading portfolio performance data...' 
                : 'Select models to view their historical performance.'
              }
            </span>
          </div>
        )}
      </div>
    </section>
  );
}