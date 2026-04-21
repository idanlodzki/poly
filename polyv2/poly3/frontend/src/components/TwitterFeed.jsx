import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchTweets, clearTweets, fetchPlayersDb, fetchTransitionConfig, fetchTwitterLog, fetchTwitterStatus, toggleTwitter } from '../api.js';
import { formatDateTime, formatScore, scoreClass, statusBadgeClass } from '../utils.js';

function nameLookupKeys(raw) {
  if (!raw) return [];
  const norm = raw.replace(/[''`.]+/g, '').toLowerCase().trim();
  const tokens = norm.split(/\s+/).filter(Boolean);
  if (!tokens.length) return [];
  const keys = new Set();
  keys.add(tokens.join(' '));
  keys.add([...tokens].sort().join(' '));
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

// Map tweet status to transition type/states
function transitionForTweetEvent(status) {
  // Tweet events are always "added to report" since we get status from the tweet
  const s = (status || '').toLowerCase();
  const statusMap = { out: 'Out', doubtful: 'Doubtful', questionable: 'Questionable', probable: 'Probable', available: 'Available' };
  const mapped = statusMap[s];
  if (!mapped) return null;
  return { type: 'added', from: 'Not On Report', to: mapped };
}

export default function TwitterFeed({ refreshKey }) {
  const [tweets, setTweets] = useState([]);
  const [playersDb, setPlayersDb] = useState([]);
  const [transitionConfigs, setTransitionConfigs] = useState([]);
  const [streamLog, setStreamLog] = useState([]);
  const [twStatus, setTwStatus] = useState({ enabled: true, blackout: false, blackout_remaining_seconds: 0, status: 'active' });
  const [logOpen, setLogOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const load = useCallback(() => {
    Promise.all([fetchTweets(), fetchPlayersDb(), fetchTransitionConfig(), fetchTwitterLog(), fetchTwitterStatus()])
      .then(([tData, pData, tcData, logData, statusData]) => {
        setTweets(tData.tweets || []);
        setPlayersDb(pData.players || []);
        setTransitionConfigs(tcData.rows || []);
        setStreamLog(logData.logs || []);
        setTwStatus(statusData || {});
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [refreshKey, load]);

  const handleClear = () => { clearTweets().then(() => setTweets([])); };
  const handleToggle = () => { toggleTwitter().then((d) => setTwStatus((p) => ({ ...p, enabled: d.enabled }))); };

  // Build lookup maps
  const importanceMap = useMemo(() => {
    const map = {};
    playersDb.forEach((p) => {
      for (const key of nameLookupKeys(p.player_name)) {
        if (!map[key] || p.importance > map[key]) map[key] = p.importance;
      }
    });
    return map;
  }, [playersDb]);

  const transitionMap = useMemo(() => {
    const map = {};
    transitionConfigs.forEach((t) => {
      map[`${t.transition_type}|${t.from_state}|${t.to_state}`] = t.score;
    });
    return map;
  }, [transitionConfigs]);

  const getImportance = (name) => {
    for (const key of nameLookupKeys(name)) {
      if (importanceMap[key]) return importanceMap[key];
    }
    return 0;
  };

  const getScore = (playerName, status) => {
    const imp = getImportance(playerName);
    if (!imp) return 0;
    const t = transitionForTweetEvent(status);
    if (!t) return 0;
    const tScore = transitionMap[`${t.type}|${t.from}|${t.to}`] || 0;
    return Math.round((imp * imp * tScore) / 100 * 100) / 100;
  };

  const filtered = tweets.filter((t) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (t.raw_text || '').toLowerCase().includes(q) ||
      (t.parsed_events || []).some((e) => (e.player_name || '').toLowerCase().includes(q));
  });

  if (loading) return <div className="loading"><div className="spinner" /></div>;

  const statusColor = twStatus.status === 'active' ? '#16a34a' : twStatus.status === 'blackout' ? '#f59e0b' : '#94a3b8';
  const statusBg = twStatus.status === 'active' ? '#f0fdf4' : twStatus.status === 'blackout' ? '#fffbeb' : '#f8fafc';
  const statusLabel = twStatus.status === 'active' ? 'Active — listening for tweets'
    : twStatus.status === 'blackout' ? `Blackout — resuming in ${twStatus.blackout_remaining_seconds}s (report window)`
    : 'Off';

  return (
    <div>
      {/* Twitter status banner */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderRadius: 10, marginBottom: 12,
        background: statusBg, border: `1px solid ${statusColor}33`, color: statusColor, fontWeight: 600, fontSize: 13 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusColor, flexShrink: 0,
          animation: twStatus.status === 'active' ? 'pulse 1.5s infinite' : 'none' }} />
        {statusLabel}
        <button className={`btn btn-sm ${twStatus.enabled ? 'btn-danger' : 'btn-primary'}`}
          onClick={handleToggle} style={{ marginLeft: 'auto', fontSize: 11, padding: '3px 12px' }}>
          {twStatus.enabled ? 'Turn Off' : 'Turn On'}
        </button>
      </div>

      <div className="filter-bar">
        <input className="form-input" type="text" placeholder="Search tweets or player..."
          value={search} onChange={(e) => setSearch(e.target.value)} />
        <span className="count-label"><strong>{filtered.length}</strong> of <strong>{tweets.length}</strong> tweet(s)</span>
        <button className={`btn btn-sm ${logOpen ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setLogOpen(!logOpen)} style={{ marginLeft: 'auto' }}>
          {logOpen ? 'Hide Log' : 'Stream Log'} ({streamLog.length})
        </button>
        <button className="btn btn-danger btn-sm" onClick={handleClear}>Clear All</button>
      </div>

      {/* Stream log viewer */}
      {logOpen && (
        <div className="card" style={{ marginBottom: 16, maxHeight: 300, overflow: 'auto', padding: 0 }}>
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong style={{ fontSize: 13 }}>Twitter Stream Log</strong>
            <span style={{ fontSize: 12, color: '#94a3b8' }}>{streamLog.length} entries</span>
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6, padding: '8px 16px' }}>
            {streamLog.length === 0 ? (
              <div style={{ color: '#94a3b8', padding: 12 }}>No log entries yet</div>
            ) : (
              [...streamLog].reverse().map((entry, i) => {
                const levelColor = entry.level === 'ERROR' ? '#ef4444'
                  : entry.level === 'WARN' ? '#f59e0b'
                  : entry.level === 'DEBUG' ? '#94a3b8'
                  : '#16a34a';
                return (
                  <div key={i} style={{ borderBottom: '1px solid #f1f5f9', padding: '3px 0' }}>
                    <span style={{ color: '#94a3b8' }}>{formatDateTime(entry.ts)}</span>
                    {' '}
                    <span style={{ color: levelColor, fontWeight: 600 }}>[{entry.level}]</span>
                    {' '}
                    <span style={{ color: entry.level === 'ERROR' ? '#ef4444' : '#0f172a' }}>{entry.msg}</span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="card"><div className="empty-state">No tweets captured yet. Listening for @UnderdogNBA...</div></div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {filtered.map((t) => {
            const events = t.parsed_events || [];
            const hasEvents = events.length > 0;
            return (
              <div className="card" key={t.id} style={{ padding: 16 }}>
                {/* Tweet header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span style={{ fontWeight: 700, fontSize: 14, color: '#1d9bf0' }}>@{t.source || 'UnderdogNBA'}</span>
                  <span style={{ fontSize: 12, color: '#94a3b8' }}>{formatDateTime(t.created_at || t.received_at)}</span>
                  {t.lag_seconds != null && (
                    <span style={{ fontSize: 11, color: '#94a3b8', background: '#f1f5f9', padding: '1px 6px', borderRadius: 8 }}>
                      {t.lag_seconds.toFixed(1)}s lag
                    </span>
                  )}
                  {hasEvents ? (
                    <span style={{ fontSize: 11, fontWeight: 600, color: '#16a34a', background: '#f0fdf4', padding: '1px 8px', borderRadius: 8 }}>
                      {events.length} event{events.length !== 1 ? 's' : ''}
                    </span>
                  ) : (
                    <span style={{ fontSize: 11, color: '#94a3b8', background: '#f8fafc', padding: '1px 8px', borderRadius: 8 }}>
                      no match
                    </span>
                  )}
                </div>

                {/* Raw tweet text */}
                <div style={{ fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap', color: '#0f172a', marginBottom: hasEvents ? 12 : 0 }}>
                  {t.raw_text}
                </div>

                {/* Parsed events with importance & score */}
                {hasEvents && (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ marginBottom: 0 }}>
                      <thead>
                        <tr>
                          <th>Player</th>
                          <th>Injury</th>
                          <th>Status</th>
                          <th>Importance</th>
                          <th>Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {events.map((e, i) => {
                          const imp = getImportance(e.player_name);
                          const score = getScore(e.player_name, e.status);
                          return (
                            <tr key={i}>
                              <td><strong>{e.player_name}</strong></td>
                              <td style={{ color: '#64748b' }}>{e.injury || '\u2014'}</td>
                              <td>
                                <span className={statusBadgeClass(e.status)} style={{ fontSize: 11, padding: '2px 8px' }}>
                                  {e.status}
                                </span>
                              </td>
                              <td style={{ fontWeight: 600 }}>{imp || 0}</td>
                              <td className={scoreClass(score)} style={{ fontWeight: 700 }}>{formatScore(score)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
