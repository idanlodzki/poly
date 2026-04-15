import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchNewsLog, clearNewsLog, fetchPlayersDb, fetchTransitionConfig } from '../api.js';
import { statusBadgeClass, formatDateTime, formatScore, scoreClass } from '../utils.js';
import RangePopover from './RangePopover.jsx';

const TYPE_OPTIONS = ['All', 'added', 'removed', 'status_change', 'injury_change'];
const TYPE_LABELS = {
  added: 'Added',
  removed: 'Removed',
  status_change: 'Status Change',
  injury_change: 'Injury Change',
};

function formatGameTime(value) {
  if (!value) return '';
  const dt = new Date(value);
  if (isNaN(dt.getTime())) return value;
  return dt.toLocaleString([], { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function formatAffectedGame(row) {
  const matchup = (row.scheduled_matchup || '').trim();
  const gameTime = formatGameTime(row.scheduled_game_datetime || '');
  if (matchup && gameTime) return `${matchup} | ${gameTime}`;
  return matchup || '';
}

function transitionStatesForRow(row) {
  const rtype = row.type || '';
  if (rtype === 'added') return { type: 'added', from: row.from_status || 'Not On Report', to: row.to_status || row.status || '' };
  if (rtype === 'removed') return { type: 'removed', from: row.from_status || row.status || '', to: row.to_status || 'Removed' };
  if (rtype === 'status_change') return { type: 'status_change', from: row.from_status || '', to: row.to_status || row.status || '' };
  return { type: rtype, from: row.from_status || '', to: row.to_status || '' };
}

export default function CaughtNews({ refreshKey }) {
  const [rows, setRows] = useState([]);
  const [playersDb, setPlayersDb] = useState([]);
  const [transitionConfigs, setTransitionConfigs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('All');
  const [impMin, setImpMin] = useState(0);
  const [impMax, setImpMax] = useState(10);
  const [scoreMin, setScoreMin] = useState(0);
  const [scoreMax, setScoreMax] = useState(100);
  const [sortCol, setSortCol] = useState('timestamp_at');
  const [sortAsc, setSortAsc] = useState(false);

  const [impBounds, setImpBounds] = useState({ min: 0, max: 10 });
  const [scoreBounds, setScoreBounds] = useState({ min: 0, max: 100 });

  const load = useCallback(() => {
    Promise.all([fetchNewsLog(), fetchPlayersDb(), fetchTransitionConfig()])
      .then(([nData, pData, tData]) => {
        setRows(nData.rows || []);
        setPlayersDb(pData.players || []);
        setTransitionConfigs(tData.rows || []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [refreshKey, load]);

  // Name normalization: generate all lookup keys for a name
  // Handles "Last, First" ↔ "First Last", case-insensitive, sorted tokens
  function nameLookupKeys(raw) {
    if (!raw) return [];
    const norm = raw.replace(/[''`.]+/g, '').toLowerCase().trim();
    const tokens = norm.split(/\s+/).filter(Boolean);
    if (!tokens.length) return [];
    const keys = new Set();
    keys.add(tokens.join(' '));
    keys.add([...tokens].sort().join(' '));
    // Handle "Last, First" → "First Last"
    if (raw.includes(',')) {
      const parts = raw.split(',').map((s) => s.trim()).filter(Boolean);
      if (parts.length >= 2) {
        const reordered = (parts.slice(1).join(' ') + ' ' + parts[0])
          .replace(/[''`.]+/g, '').toLowerCase().split(/\s+/).filter(Boolean);
        keys.add(reordered.join(' '));
        keys.add([...reordered].sort().join(' '));
      }
    }
    return [...keys];
  }

  // Build lookup maps
  const importanceMap = {};
  playersDb.forEach((p) => {
    for (const key of nameLookupKeys(p.player_name)) {
      if (!importanceMap[key] || p.importance > importanceMap[key]) {
        importanceMap[key] = p.importance;
      }
    }
  });

  const transitionMap = {};
  transitionConfigs.forEach((t) => {
    transitionMap[`${t.transition_type}|${t.from_state}|${t.to_state}`] = t.score;
  });

  const getImportance = (name) => {
    for (const key of nameLookupKeys(name)) {
      if (importanceMap[key]) return importanceMap[key];
    }
    return 0;
  };

  const getScore = (row) => {
    const imp = getImportance(row.player);
    if (!imp || row.type === 'injury_change') return 0;
    const t = transitionStatesForRow(row);
    const tScore = transitionMap[`${t.type}|${t.from}|${t.to}`] || 0;
    return Math.round((imp * imp * tScore) / 100 * 100) / 100;
  };

  // Decorate rows — memoize so bounds effect has a stable dep
  const decorated = useMemo(() => rows.map((r) => ({
    ...r,
    _importance: getImportance(r.player),
    _score: getScore(r),
    _game: formatAffectedGame(r),
  })), [rows, playersDb, transitionConfigs]);

  // Recalculate bounds whenever underlying data changes
  useEffect(() => {
    if (!rows.length || !playersDb.length) return;
    const imps = decorated.map((r) => r._importance);
    const scores = decorated.map((r) => Math.abs(r._score));
    const iMax = Math.max(10, ...imps);
    const sMax = Math.max(100, ...scores);
    setImpBounds({ min: 0, max: iMax });
    setScoreBounds({ min: 0, max: Math.ceil(sMax) });
    setImpMin(0); setImpMax(iMax);
    setScoreMin(0); setScoreMax(Math.ceil(sMax));
  }, [rows, playersDb, transitionConfigs]);

  const handleClear = () => { clearNewsLog().then(() => setRows([])); };

  const filtered = decorated.filter((r) => {
    if (search && !r.player?.toLowerCase().includes(search.toLowerCase())) return false;
    if (typeFilter !== 'All' && r.type !== typeFilter) return false;
    if (r._importance < impMin || r._importance > impMax) return false;
    if (Math.abs(r._score) < scoreMin || Math.abs(r._score) > scoreMax) return false;
    return true;
  });

  const handleSort = useCallback((col) => {
    if (sortCol === col) setSortAsc((p) => !p);
    else { setSortCol(col); setSortAsc(col !== 'timestamp_at'); }
  }, [sortCol]);

  const sorted = [...filtered].sort((a, b) => {
    let av, bv;
    if (sortCol === '_importance' || sortCol === '_score') { av = a[sortCol]; bv = b[sortCol]; }
    else { av = a[sortCol] ?? ''; bv = b[sortCol] ?? ''; }
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av < bv) return sortAsc ? -1 : 1;
    if (av > bv) return sortAsc ? 1 : -1;
    return 0;
  });

  const sortInd = (col) => sortCol !== col ? '' : sortAsc ? ' \u25B2' : ' \u25BC';
  const toneColor = (tone) => tone === 'positive' ? '#16a34a' : tone === 'negative' ? '#dc2626' : '#64748b';

  if (loading) return <div className="loading"><div className="spinner" /></div>;

  const COLS = [
    { key: 'timestamp_at', label: 'Caught At' },
    { key: 'type', label: 'Type' },
    { key: 'team', label: 'Team' },
    { key: 'player', label: 'Player' },
    { key: '_game', label: 'Game Affected' },
    { key: '_importance', label: 'Player Importance' },
    { key: '_score', label: 'Score' },
    { key: 'status', label: 'Status' },
    { key: 'detail', label: 'News' },
  ];

  return (
    <div className="card">
      <div className="filter-bar">
        <input className="form-input" type="text" placeholder="Search caught news..."
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <select className="form-select" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          {TYPE_OPTIONS.map((t) => (
            <option key={t} value={t}>{t === 'All' ? 'All News Types' : TYPE_LABELS[t] || t}</option>
          ))}
        </select>
        <RangePopover
          label="Player Importance"
          min={impBounds.min} max={impBounds.max} step={1}
          valueMin={impMin} valueMax={impMax}
          onChangeMin={setImpMin} onChangeMax={setImpMax}
        />
        <RangePopover
          label="Score Abs"
          min={scoreBounds.min} max={scoreBounds.max} step={1}
          valueMin={scoreMin} valueMax={scoreMax}
          onChangeMin={setScoreMin} onChangeMax={setScoreMax}
        />
        <button className="btn btn-danger btn-sm" onClick={handleClear}>Clear News Log</button>
      </div>

      <p className="count-label">
        Showing <strong>{sorted.length}</strong> of <strong>{rows.length}</strong> caught event(s)
      </p>

      {sorted.length === 0 ? (
        <div className="empty-state">No caught news yet.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                {COLS.map((col) => (
                  <th key={col.key} className="sortable" onClick={() => handleSort(col.key)}>
                    {col.label}{sortInd(col.key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <tr key={i}>
                  <td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(r.timestamp_at)}</td>
                  <td>{TYPE_LABELS[r.type] || r.type}</td>
                  <td>{r.team || ''}</td>
                  <td><strong>{r.player || ''}</strong></td>
                  <td>{r._game || '\u2014'}</td>
                  <td>{r._importance}</td>
                  <td className={scoreClass(r._score)}>{formatScore(r._score)}</td>
                  <td>
                    {r.status ? <span className={statusBadgeClass(r.status)}>{r.status}</span> : '\u2014'}
                  </td>
                  <td style={{ color: toneColor(r.tone), fontWeight: 600 }}>
                    {r.detail || r.injury || '\u2014'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
