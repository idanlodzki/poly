import React, { useState, useEffect, useCallback } from 'react';
import { fetchUpcomingGames, fetchPolymarketLive, fetchPolymarketGame } from '../api.js';
import { teamColor, formatDateTime } from '../utils.js';

export default function UpcomingGames({ refreshKey }) {
  const [games, setGames] = useState([]);
  const [liveOdds, setLiveOdds] = useState({});
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState({});
  const [detailOdds, setDetailOdds] = useState({});
  const [detailLoading, setDetailLoading] = useState({});

  useEffect(() => {
    Promise.all([fetchUpcomingGames(), fetchPolymarketLive()])
      .then(([gData, pData]) => {
        setGames(gData.rows || []);
        setLiveOdds(pData.odds || {});
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const buildSlug = useCallback((game) => {
    const away = (game.away_tricode || '').toLowerCase();
    const home = (game.home_tricode || '').toLowerCase();
    const date = (game.game_datetime || '').slice(0, 10);
    return `nba-${away}-${home}-${date}`;
  }, []);

  const toggleExpand = useCallback(
    (gameId, game) => {
      setExpanded((prev) => {
        const next = { ...prev, [gameId]: !prev[gameId] };
        if (next[gameId] && !detailOdds[gameId]) {
          const slug = buildSlug(game);
          setDetailLoading((p) => ({ ...p, [gameId]: true }));
          fetchPolymarketGame(slug)
            .then((data) => setDetailOdds((p) => ({ ...p, [gameId]: data })))
            .catch(() => setDetailOdds((p) => ({ ...p, [gameId]: null })))
            .finally(() => setDetailLoading((p) => ({ ...p, [gameId]: false })));
        }
        return next;
      });
    },
    [buildSlug, detailOdds],
  );

  if (loading) {
    return (
      <div className="loading">
        <div className="spinner" />
      </div>
    );
  }

  if (games.length === 0) {
    return (
      <div className="card">
        <div className="empty-state">No upcoming games</div>
      </div>
    );
  }

  return (
    <div>
      {games.map((game) => {
        const gid = game.game_id || `${game.away_tricode}-${game.home_tricode}-${game.game_datetime}`;
        const slug = buildSlug(game);
        const live = liveOdds[slug];
        const isExpanded = expanded[gid];

        return (
          <div className="card" key={gid} style={{ marginBottom: '1rem' }}>
            <div
              style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
              onClick={() => toggleExpand(gid, game)}
            >
              <div>
                <span style={{ fontWeight: 700, color: teamColor(game.away_tricode) }}>
                  {game.away_tricode}
                </span>
                {' @ '}
                <span style={{ fontWeight: 700, color: teamColor(game.home_tricode) }}>
                  {game.home_tricode}
                </span>
                {game.game_label && (
                  <span style={{ marginLeft: 8, fontSize: '0.85em', opacity: 0.7 }}>
                    {game.game_label}
                  </span>
                )}
              </div>
              <div style={{ textAlign: 'right', fontSize: '0.9em' }}>
                <div>{formatDateTime(game.game_datetime)}</div>
                <div style={{ opacity: 0.7 }}>
                  {game.arena ? `${game.arena}, ${game.arena_city}` : ''}
                </div>
              </div>
            </div>

            <div style={{ marginTop: 6, fontSize: '0.85em', opacity: 0.7 }}>
              {game.broadcast && <span>TV: {game.broadcast}</span>}
              {game.status && <span style={{ marginLeft: 12 }}>Status: {game.status}</span>}
              {live && (
                <span style={{ marginLeft: 12, fontWeight: 600 }}>
                  Polymarket odds available
                </span>
              )}
            </div>

            {live && !isExpanded && (
              <div style={{ marginTop: 8, fontSize: '0.85em' }}>
                {(live.markets || []).slice(0, 1).map((m, i) => (
                  <span key={i}>
                    {(m.outcomes || []).map((o, j) => (
                      <span key={j}>
                        {o}: {((m.prices?.[j] || 0) * 100).toFixed(1)}%
                        {j < (m.outcomes || []).length - 1 ? ' / ' : ''}
                      </span>
                    ))}
                  </span>
                ))}
              </div>
            )}

            {isExpanded && (
              <div style={{ marginTop: 12 }}>
                {detailLoading[gid] ? (
                  <div className="loading">
                    <div className="spinner" />
                  </div>
                ) : detailOdds[gid] && detailOdds[gid].markets ? (
                  <table>
                    <thead>
                      <tr>
                        <th>Type</th>
                        <th>Question</th>
                        <th>Outcomes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detailOdds[gid].markets || []).map((m, i) => (
                        <tr key={i}>
                          <td>{m.type || '—'}</td>
                          <td>{m.question || '—'}</td>
                          <td>
                            {(m.outcomes || []).map((o, j) => (
                              <span key={j}>
                                {o}: {((m.prices?.[j] || 0) * 100).toFixed(1)}%
                                {j < (m.outcomes || []).length - 1 ? ' / ' : ''}
                              </span>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : live && live.markets ? (
                  <table>
                    <thead>
                      <tr>
                        <th>Type</th>
                        <th>Question</th>
                        <th>Outcomes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(live.markets || []).map((m, i) => (
                        <tr key={i}>
                          <td>{m.type || '—'}</td>
                          <td>{m.question || '—'}</td>
                          <td>
                            {(m.outcomes || []).map((o, j) => (
                              <span key={j}>
                                {o}: {((m.prices?.[j] || 0) * 100).toFixed(1)}%
                                {j < (m.outcomes || []).length - 1 ? ' / ' : ''}
                              </span>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="empty-state">No Polymarket data available for this game</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
