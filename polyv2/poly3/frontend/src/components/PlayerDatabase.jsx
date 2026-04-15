import React, { useState, useEffect, useCallback, useRef } from 'react';
import { fetchPlayersDb, upsertPlayer, deletePlayer } from '../api.js';
import { TEAM_TRICODE_MAP, teamColor } from '../utils.js';
import RangePopover from './RangePopover.jsx';

const ALL_TEAMS = Object.keys(TEAM_TRICODE_MAP).sort();

function InlineImportance({ player, onSaved }) {
  const [value, setValue] = useState(String(player.importance ?? 0));
  const timer = useRef(null);
  const lastSaved = useRef(String(player.importance ?? 0));

  // Sync if parent data changes
  useEffect(() => {
    const v = String(player.importance ?? 0);
    if (v !== lastSaved.current) {
      setValue(v);
      lastSaved.current = v;
    }
  }, [player.importance]);

  const save = useCallback((val) => {
    const num = Number(val);
    if (isNaN(num) || String(num) === lastSaved.current) return;
    lastSaved.current = String(num);
    upsertPlayer({ player_name: player.player_name, nba_team: player.nba_team, importance: num })
      .then(() => onSaved && onSaved());
  }, [player, onSaved]);

  const handleChange = (e) => {
    const v = e.target.value;
    setValue(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => save(v), 800);
  };

  const handleBlur = () => {
    clearTimeout(timer.current);
    save(value);
  };

  return (
    <input
      type="number"
      className="form-input"
      value={value}
      onChange={handleChange}
      onBlur={handleBlur}
      style={{ width: 70, textAlign: 'center', padding: '4px 6px', fontSize: 13 }}
    />
  );
}

export default function PlayerDatabase({ refreshKey }) {
  const [players, setPlayers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [teamFilter, setTeamFilter] = useState('All');
  const [impMin, setImpMin] = useState(0);
  const [impMax, setImpMax] = useState(10);
  const [impBounds, setImpBounds] = useState({ min: 0, max: 10 });
  const [sortCol, setSortCol] = useState('importance');
  const [sortAsc, setSortAsc] = useState(false);

  // Add form
  const [formName, setFormName] = useState('');
  const [formTeam, setFormTeam] = useState('');
  const [formImportance, setFormImportance] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    fetchPlayersDb()
      .then((data) => setPlayers(data.players || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [refreshKey, load]);

  // Auto-detect importance bounds
  useEffect(() => {
    if (!players.length) return;
    const vals = players.map((p) => p.importance || 0);
    const iMax = Math.max(10, ...vals);
    setImpBounds({ min: 0, max: iMax });
    setImpMin(0);
    setImpMax(iMax);
  }, [players]);

  const teamsInData = [...new Set(players.map((p) => p.nba_team).filter(Boolean))].sort();

  const filtered = players.filter((p) => {
    if (search && !p.player_name?.toLowerCase().includes(search.toLowerCase())) return false;
    if (teamFilter !== 'All' && p.nba_team !== teamFilter) return false;
    if ((p.importance || 0) < impMin || (p.importance || 0) > impMax) return false;
    return true;
  });

  const handleSort = useCallback((col) => {
    if (sortCol === col) setSortAsc((p) => !p);
    else { setSortCol(col); setSortAsc(col === 'player_name'); }
  }, [sortCol]);

  const sorted = [...filtered].sort((a, b) => {
    let av = a[sortCol] ?? '';
    let bv = b[sortCol] ?? '';
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av < bv) return sortAsc ? -1 : 1;
    if (av > bv) return sortAsc ? 1 : -1;
    return 0;
  });

  const sortInd = (col) => sortCol !== col ? '' : sortAsc ? ' \u25B2' : ' \u25BC';

  const handleDelete = (name) => {
    if (!window.confirm(`Delete ${name}?`)) return;
    deletePlayer(name).then(() => load());
  };

  const handleAdd = () => {
    if (!formName.trim() || !formTeam) return;
    setSaving(true);
    upsertPlayer({ player_name: formName.trim(), nba_team: formTeam, importance: Number(formImportance) || 0 })
      .then(() => { load(); setFormName(''); setFormTeam(''); setFormImportance(''); })
      .finally(() => setSaving(false));
  };

  if (loading) return <div className="loading"><div className="spinner" /></div>;

  return (
    <div className="card">
      {/* Add new player */}
      <div className="filter-bar" style={{ marginBottom: 16, paddingBottom: 16, borderBottom: '1px solid var(--border)' }}>
        <input className="form-input" type="text" placeholder="Player name..."
          value={formName} onChange={(e) => setFormName(e.target.value)} style={{ width: 180 }} />
        <select className="form-select" value={formTeam} onChange={(e) => setFormTeam(e.target.value)}>
          <option value="">Team...</option>
          {ALL_TEAMS.map((t) => <option key={t} value={t}>{t} ({TEAM_TRICODE_MAP[t]})</option>)}
        </select>
        <input className="form-input" type="number" placeholder="Imp" value={formImportance}
          onChange={(e) => setFormImportance(e.target.value)} style={{ width: 70 }} />
        <button className="btn btn-primary btn-sm" onClick={handleAdd} disabled={saving}>
          {saving ? 'Adding...' : 'Add Player'}
        </button>
      </div>

      {/* Filters */}
      <div className="filter-bar">
        <input className="form-input" type="text" placeholder="Search player..."
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <select className="form-select" value={teamFilter} onChange={(e) => setTeamFilter(e.target.value)}>
          <option value="All">All Teams</option>
          {teamsInData.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <RangePopover
          label="Player Importance"
          min={impBounds.min} max={impBounds.max} step={1}
          valueMin={impMin} valueMax={impMax}
          onChangeMin={setImpMin} onChangeMax={setImpMax}
        />
        <span className="count-label">{sorted.length} players</span>
      </div>

      {sorted.length === 0 ? (
        <div className="empty-state">No players found</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th className="sortable" onClick={() => handleSort('player_name')}>Player{sortInd('player_name')}</th>
              <th className="sortable" onClick={() => handleSort('nba_team')}>Team{sortInd('nba_team')}</th>
              <th className="sortable" onClick={() => handleSort('importance')}>Importance{sortInd('importance')}</th>
              <th style={{ width: 60 }}></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => {
              const tricode = TEAM_TRICODE_MAP[p.nba_team] || p.nba_team;
              return (
                <tr key={p.player_name}>
                  <td>{p.player_name}</td>
                  <td><span style={{ color: teamColor(tricode), fontWeight: 600 }}>{tricode}</span></td>
                  <td><InlineImportance player={p} onSaved={load} /></td>
                  <td>
                    <button className="btn btn-danger btn-sm" onClick={() => handleDelete(p.player_name)}
                      style={{ padding: '3px 8px', fontSize: 12 }}>
                      &times;
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
