import React, { useState, useEffect, useCallback } from 'react';
import { fetchReport } from '../api.js';
import { STATUS_ORDER, statusBadgeClass, formatDateTime } from '../utils.js';

export default function InjuryReport({ refreshKey }) {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('All');
  const [teamFilter, setTeamFilter] = useState('All');
  const [sortCol, setSortCol] = useState('player');
  const [sortAsc, setSortAsc] = useState(true);

  useEffect(() => {
    fetchReport()
      .then((data) => setRecords(data.records || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const teams = [...new Set(records.map((r) => r.team).filter(Boolean))].sort();

  const filtered = records.filter((r) => {
    if (search && !r.player?.toLowerCase().includes(search.toLowerCase())) return false;
    if (statusFilter !== 'All' && r.status !== statusFilter) return false;
    if (teamFilter !== 'All' && r.team !== teamFilter) return false;
    return true;
  });

  const handleSort = useCallback(
    (col) => {
      if (sortCol === col) {
        setSortAsc((prev) => !prev);
      } else {
        setSortCol(col);
        setSortAsc(true);
      }
    },
    [sortCol],
  );

  const sorted = [...filtered].sort((a, b) => {
    let av = a[sortCol] ?? '';
    let bv = b[sortCol] ?? '';
    if (sortCol === 'status') {
      av = STATUS_ORDER.indexOf(av);
      bv = STATUS_ORDER.indexOf(bv);
      if (av === -1) av = 999;
      if (bv === -1) bv = 999;
    }
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av < bv) return sortAsc ? -1 : 1;
    if (av > bv) return sortAsc ? 1 : -1;
    return 0;
  });

  const sortIndicator = (col) => {
    if (sortCol !== col) return '';
    return sortAsc ? ' ▲' : ' ▼';
  };

  if (loading) {
    return (
      <div className="loading">
        <div className="spinner" />
      </div>
    );
  }

  return (
    <div className="card">
      <div className="filter-bar">
        <input
          className="form-input"
          type="text"
          placeholder="Search player..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="form-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="All">All Statuses</option>
          {STATUS_ORDER.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          className="form-select"
          value={teamFilter}
          onChange={(e) => setTeamFilter(e.target.value)}
        >
          <option value="All">All Teams</option>
          {teams.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="count-label">{sorted.length} players</span>
      </div>

      {sorted.length === 0 ? (
        <div className="empty-state">No injury records found</div>
      ) : (
        <table>
          <thead>
            <tr>
              {[
                { key: 'team', label: 'Team' },
                { key: 'player', label: 'Player' },
                { key: 'status', label: 'Status' },
                { key: 'injury', label: 'Injury' },
                { key: 'matchup', label: 'Matchup' },
                { key: 'game_time', label: 'Game Time' },
                { key: 'last_update_at', label: 'Last Update' },
              ].map((col) => (
                <th
                  key={col.key}
                  className="sortable"
                  onClick={() => handleSort(col.key)}
                >
                  {col.label}
                  {sortIndicator(col.key)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i}>
                <td>{r.team}</td>
                <td>{r.player}</td>
                <td>
                  <span className={statusBadgeClass(r.status)}>{r.status}</span>
                </td>
                <td>{r.injury || '—'}</td>
                <td>{r.matchup || '—'}</td>
                <td>{formatDateTime(r.game_datetime_et || r.game_time)}</td>
                <td>{formatDateTime(r.last_update_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
