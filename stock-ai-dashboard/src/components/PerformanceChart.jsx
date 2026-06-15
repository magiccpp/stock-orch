import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { Activity, TrendingUp } from 'lucide-react';
import { formatPercent } from '../lib/normalizers.js';

const LINE_COLORS = ['#58a6ff', '#a78bfa', '#2dd4bf', '#f59e0b', '#fb7185', '#84cc16', '#22d3ee'];

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

export default function PerformanceChart({ models, loading: externalLoading }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

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
          return { model, returns: data.returns };
        });

        const allModelReturns = await Promise.all(modelReturnsPromises);
        
        // Also fetch S&P 500 data from the first model (they should all have the same S&P 500 data)
        const sp500Data = allModelReturns[0]?.returns || [];
        
        // Combine data from all models by date
        const dateMap = new Map();
        
        // Add S&P 500 data
        sp500Data.forEach(item => {
          dateMap.set(item.date, {
            date: item.date,
            'S&P 500': item.sp500_cumulative_return
          });
        });
        
        // Add each model's data
        allModelReturns.forEach(({ model, returns }) => {
          returns.forEach(item => {
            const existing = dateMap.get(item.date) || { date: item.date };
            existing[model] = item.model_cumulative_return;
            dateMap.set(item.date, existing);
          });
        });
        
        // Convert to array and sort by date
        const combinedData = Array.from(dateMap.values())
          .sort((a, b) => new Date(a.date) - new Date(b.date));
        
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
  const hasData = data.length > 0 && !error;
  const allSeries = hasData ? ['S&P 500', ...models] : [];

  return (
    <section className="chart-panel panel">
      <div className="panel-header">
        <div>
          <span className="eyebrow">Performance</span>
          <h1>Cumulative Return</h1>
          <p>Historical strategy performance compared to S&P 500 benchmark.</p>
        </div>
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

      <div className="chart-wrap">
        {error ? (
          <div className="error-state">
            <TrendingUp size={30} />
            <strong>Error loading returns data</strong>
            <span>{error}</span>
          </div>
        ) : hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 12, right: 18, left: 2, bottom: 4 }}>
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
                  return date.toLocaleDateString('en-US', { 
                    month: 'short', 
                    year: '2-digit' 
                  });
                }}
              />
              <YAxis 
                tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} 
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