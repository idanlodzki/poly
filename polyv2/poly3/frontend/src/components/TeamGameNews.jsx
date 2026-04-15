import React, { useState, useEffect } from 'react';
import { fetchLatestBatch } from '../api.js';
import { teamColor, formatDateTime, scoreClass, formatScore, statusBadgeClass } from '../utils.js';

export default function TeamGameNews({ refreshKey }) {
  const [batches, setBatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState({});

  useEffect(() => {
    fetchLatestBatch()
      .then((d) => setBatches(d.batches || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const toggle = (key) => setExpanded((p) => ({ ...p, [key]: !p[key] }));

  if (loading) return <div className="loading"><div className="spinner" /></div>;

  if (!batches.length) {
    return <div className="card"><div className="empty-state">No game-impact batches found</div></div>;
  }

  return (
    <div>
      <p className="count-label" style={{ marginBottom: 16 }}>
        Showing <strong>{batches.length}</strong> batch(es) across upcoming games
      </p>

      {batches.map((b) => {
        const items = b.items || [];
        const isExpanded = expanded[b.key];

        const edgeAbs = Math.abs(b.edge_score || 0);
        const edgeTeam = b.edge_score > 0 ? b.home_tricode : b.edge_score < 0 ? b.away_tricode : '';
        const edgeLabel = b.edge_score === 0 ? 'Even' :
          `${b.edge_score > 0 ? 'Home' : 'Away'} edge: ${edgeTeam} +${formatScore(edgeAbs)}`;

        return (
          <div className="card" key={b.key} style={{ marginBottom: 12 }}>
            {/* Header — always visible */}
            <div style={{ cursor: 'pointer' }} onClick={() => toggle(b.key)}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                {/* Matchup */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontWeight: 700, fontSize: 16, color: teamColor(b.away_tricode) }}>{b.away_tricode}</span>
                  <span style={{ color: '#94a3b8' }}>@</span>
                  <span style={{ fontWeight: 700, fontSize: 16, color: teamColor(b.home_tricode) }}>{b.home_tricode}</span>
                  <span style={{ fontSize: 13, color: '#94a3b8', marginLeft: 4 }}>
                    {formatDateTime(b.game_datetime_et)}
                  </span>
                </div>

                {/* Batch time */}
                <span style={{ fontSize: 12, color: '#94a3b8' }}>
                  Batch: {formatDateTime(b.batch_time)}
                </span>
              </div>

              {/* Impact summary row */}
              <div style={{ display: 'flex', gap: 20, marginTop: 8, fontSize: 13, flexWrap: 'wrap', alignItems: 'center' }}>
                <span>
                  <span style={{ color: teamColor(b.away_tricode), fontWeight: 600 }}>{b.away_tricode}</span>
                  {' impact: '}
                  <strong className={scoreClass(b.away_score)}>{formatScore(b.away_score)}</strong>
                </span>
                <span>
                  <span style={{ color: teamColor(b.home_tricode), fontWeight: 600 }}>{b.home_tricode}</span>
                  {' impact: '}
                  <strong className={scoreClass(b.home_score)}>{formatScore(b.home_score)}</strong>
                </span>
                <span style={{ fontWeight: 700, color: edgeTeam ? teamColor(edgeTeam) : '#94a3b8' }}>
                  {edgeLabel}
                </span>
                <span style={{ color: '#94a3b8' }}>
                  {items.length} item{items.length !== 1 ? 's' : ''}
                </span>

                <span style={{ marginLeft: 'auto', fontSize: 12, color: '#94a3b8' }}>
                  {isExpanded ? '\u25B2' : '\u25BC'}
                </span>
              </div>
            </div>

            {/* Expanded detail table */}
            {isExpanded && (
              <div style={{ marginTop: 14, overflowX: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Team</th>
                      <th>Type</th>
                      <th>Detail</th>
                      <th>Status</th>
                      <th>Score</th>
                      <th>Credited To</th>
                      <th>Impact</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item, i) => (
                      <tr key={i}>
                        <td><strong>{item.player || ''}</strong></td>
                        <td style={{ color: teamColor(item.team_tricode), fontWeight: 600 }}>
                          {item.team_tricode || item.team || ''}
                        </td>
                        <td>{item.type || ''}</td>
                        <td>{item.detail || '\u2014'}</td>
                        <td>
                          {(item.status || item.to_status) ? (
                            <span className={statusBadgeClass(item.status || item.to_status)}>
                              {item.status || item.to_status}
                            </span>
                          ) : '\u2014'}
                        </td>
                        <td className={scoreClass(item.score)}>{formatScore(item.score)}</td>
                        <td style={{ color: item.credited_team ? teamColor(item.credited_team) : '#94a3b8', fontWeight: 600 }}>
                          {item.credited_team || '\u2014'}
                        </td>
                        <td className={scoreClass(item.impact_value)}>{formatScore(item.impact_value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
