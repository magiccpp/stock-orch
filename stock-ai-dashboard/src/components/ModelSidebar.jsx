import { BrainCircuit, Check, RefreshCw } from 'lucide-react';

export default function ModelSidebar({ models, selected, focusedModel, loading, onToggle, onFocus, onRefresh }) {
  return (
    <aside className="sidebar panel">
      <div className="brand">
        <div className="brand-icon"><BrainCircuit size={22} /></div>
        <div><strong>Stock AI Orchestrator</strong><span>Model intelligence</span></div>
      </div>

      <div className="section-heading">
        <div><span className="eyebrow">Models</span><h2>Strategies</h2></div>
        <button className="icon-button" onClick={onRefresh} title="Refresh models" aria-label="Refresh models">
          <RefreshCw size={16} className={loading ? 'spin' : ''} />
        </button>
      </div>

      <div className="model-list">
        {models.map((model) => {
          const isSelected = selected.includes(model);
          const isFocused = focusedModel === model;
          return (
            <div key={model} className={`model-row ${isFocused ? 'focused' : ''}`} onClick={() => onFocus(model)}>
              <button
                className={`checkbox ${isSelected ? 'checked' : ''}`}
                aria-label={`${isSelected ? 'Deselect' : 'Select'} ${model}`}
                onClick={(event) => { event.stopPropagation(); onToggle(model); }}
              >
                {isSelected && <Check size={13} strokeWidth={3} />}
              </button>
              <div className="model-copy"><strong>{model}</strong><span>{isSelected ? 'Visible in chart' : 'Not selected'}</span></div>
              <span className={`status-dot ${isSelected ? 'online' : ''}`} />
            </div>
          );
        })}
        {!loading && models.length === 0 && <p className="muted empty-side">No model directories found.</p>}
      </div>

      <div className="sidebar-note">Select several strategies to compare returns. Click a row to inspect its portfolio.</div>
    </aside>
  );
}
