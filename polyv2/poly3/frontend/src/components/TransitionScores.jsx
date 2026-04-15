import React, { useState, useEffect, useRef, useCallback } from 'react';
import { fetchTransitionConfig, upsertTransitionConfig } from '../api.js';
import { STATUS_ORDER, statusBadgeClass } from '../utils.js';

const STATUS_COLORS = {
  'Out': { color: '#ef4444', bg: '#fef2f2' },
  'Doubtful': { color: '#f97316', bg: '#fff7ed' },
  'Questionable': { color: '#eab308', bg: '#fefce8' },
  'Probable': { color: '#22c55e', bg: '#f0fdf4' },
  'Available': { color: '#3b82f6', bg: '#eff6ff' },
  'Not On Report': { color: '#64748b', bg: '#f8fafc' },
  'Removed': { color: '#10b981', bg: '#ecfdf5' },
};

function StatusBadge({ status }) {
  if (STATUS_ORDER.includes(status)) {
    return <span className={statusBadgeClass(status)}>{status}</span>;
  }
  const sc = STATUS_COLORS[status];
  if (sc) {
    return <span style={{ display: 'inline-block', padding: '3px 10px', borderRadius: 20, fontSize: 12, fontWeight: 600, background: sc.bg, color: sc.color }}>{status}</span>;
  }
  return <span>{status}</span>;
}

function scoreInputStyle(value) {
  const n = Number(value);
  if (isNaN(n) || n === 0) return {};
  if (n > 0) return { color: '#16a34a', borderColor: '#bbf7d0' };
  return { color: '#ef4444', borderColor: '#fecaca' };
}

export default function TransitionScores({ refreshKey }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [localScores, setLocalScores] = useState({});
  const debounceTimers = useRef({});

  useEffect(() => {
    fetchTransitionConfig()
      .then((data) => {
        const r = data.rows || [];
        setRows(r);
        const map = {};
        r.forEach((row) => {
          map[`${row.transition_type}|${row.from_state}|${row.to_state}`] = row.score;
        });
        setLocalScores(map);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const getScore = (type, from, to) => localScores[`${type}|${from}|${to}`] ?? '';

  const handleChange = useCallback((type, from, to, value) => {
    const k = `${type}|${from}|${to}`;
    setLocalScores((prev) => ({ ...prev, [k]: value }));
    if (debounceTimers.current[k]) clearTimeout(debounceTimers.current[k]);
    debounceTimers.current[k] = setTimeout(() => {
      const numVal = parseInt(value, 10);
      if (!isNaN(numVal)) {
        upsertTransitionConfig({ transition_type: type, from_state: from, to_state: to, score: numVal });
      }
    }, 500);
  }, []);

  useEffect(() => {
    const timers = debounceTimers.current;
    return () => { Object.values(timers).forEach(clearTimeout); };
  }, []);

  if (loading) return <div className="loading"><div className="spinner" /></div>;

  return (
    <div>
      {/* Status Change */}
      <div className="card transition-section">
        <h3>Status Change</h3>
        <p>Rows are the current status, columns are the new status.</p>
        <div style={{ overflowX: 'auto' }}>
          <table className="transition-grid">
            <thead>
              <tr>
                <th style={{ textAlign: 'left' }}>From \ To</th>
                {STATUS_ORDER.map((s) => <th key={s}><StatusBadge status={s} /></th>)}
              </tr>
            </thead>
            <tbody>
              {STATUS_ORDER.map((from) => (
                <tr key={from}>
                  <td className="state-cell"><StatusBadge status={from} /></td>
                  {STATUS_ORDER.map((to) => {
                    if (from === to) return <td key={to} className="same-state-cell">&mdash;</td>;
                    const val = getScore('status_change', from, to);
                    return (
                      <td key={to}>
                        <input className="score-input" type="number" value={val}
                          style={scoreInputStyle(val)}
                          onChange={(e) => handleChange('status_change', from, to, e.target.value)} />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Added to Report */}
      <div className="card transition-section">
        <h3>Added to Report</h3>
        <p>Scores for a player appearing on the report from not being on it before.</p>
        <div style={{ overflowX: 'auto' }}>
          <table className="transition-grid">
            <thead>
              <tr>
                <th style={{ textAlign: 'left' }}>From \ To</th>
                {STATUS_ORDER.map((s) => <th key={s}><StatusBadge status={s} /></th>)}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="state-cell"><StatusBadge status="Not On Report" /></td>
                {STATUS_ORDER.map((to) => {
                  const val = getScore('added', 'Not On Report', to);
                  return (
                    <td key={to}>
                      <input className="score-input" type="number" value={val}
                        style={scoreInputStyle(val)}
                        onChange={(e) => handleChange('added', 'Not On Report', to, e.target.value)} />
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Removed from Report */}
      <div className="card transition-section">
        <h3>Removed from Report</h3>
        <p>Scores for a player leaving the report from each prior status.</p>
        <div style={{ overflowX: 'auto' }}>
          <table className="transition-grid">
            <thead>
              <tr>
                <th style={{ textAlign: 'left' }}>From \ To</th>
                {STATUS_ORDER.map((s) => <th key={s}><StatusBadge status={s} /></th>)}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="state-cell"><StatusBadge status="Removed" /></td>
                {STATUS_ORDER.map((from) => {
                  const val = getScore('removed', from, 'Removed');
                  return (
                    <td key={from}>
                      <input className="score-input" type="number" value={val}
                        style={scoreInputStyle(val)}
                        onChange={(e) => handleChange('removed', from, 'Removed', e.target.value)} />
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
