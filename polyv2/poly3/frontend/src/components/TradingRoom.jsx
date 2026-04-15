import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchBettingConfig,
  updateBettingConfig,
  fetchBetLog,
  clearBetLog,
  fetchPlayersDb,
  fetchNewsLog,
  fetchNotifications,
  simulateInjury,
} from '../api.js';
import { teamColor, formatDateTime, scoreClass, formatScore } from '../utils.js';

export default function TradingRoom({ refreshKey }) {
  const [config, setConfig] = useState({ auto_trade_enabled: false, threshold: 0, bet_amount: 10 });
  const [betLog, setBetLog] = useState([]);
  const [playersDb, setPlayersDb] = useState([]);
  const [loading, setLoading] = useState(true);

  // Simulate
  const [simOpen, setSimOpen] = useState(false);
  const [simPlayer, setSimPlayer] = useState('');
  const [simStatus, setSimStatus] = useState('Out');
  const [simResult, setSimResult] = useState('');
  const [simLoading, setSimLoading] = useState(false);

  const thresholdTimer = useRef(null);
  const betAmountTimer = useRef(null);

  const load = useCallback(() => {
    Promise.all([fetchBettingConfig(), fetchBetLog(), fetchPlayersDb()])
      .then(([cData, bData, pData]) => {
        setConfig(cData || { auto_trade_enabled: false, threshold: 0, bet_amount: 10 });
        setBetLog(bData.rows || []);
        setPlayersDb(pData.players || []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [refreshKey, load]);
  useEffect(() => () => { if (thresholdTimer.current) clearTimeout(thresholdTimer.current); if (betAmountTimer.current) clearTimeout(betAmountTimer.current); }, []);

  const handleToggle = useCallback(() => {
    const next = !config.auto_trade_enabled;
    setConfig((p) => ({ ...p, auto_trade_enabled: next }));
    updateBettingConfig({ auto_trade_enabled: next, threshold: config.threshold, bet_amount: config.bet_amount });
  }, [config]);

  const handleThreshold = useCallback((value) => {
    const num = Number(value);
    setConfig((p) => ({ ...p, threshold: num }));
    if (thresholdTimer.current) clearTimeout(thresholdTimer.current);
    thresholdTimer.current = setTimeout(() => {
      updateBettingConfig({ auto_trade_enabled: config.auto_trade_enabled, threshold: num, bet_amount: config.bet_amount });
    }, 600);
  }, [config.auto_trade_enabled, config.bet_amount]);

  const handleBetAmount = useCallback((value) => {
    const num = Number(value);
    setConfig((p) => ({ ...p, bet_amount: num }));
    if (betAmountTimer.current) clearTimeout(betAmountTimer.current);
    betAmountTimer.current = setTimeout(() => {
      updateBettingConfig({ auto_trade_enabled: config.auto_trade_enabled, threshold: config.threshold, bet_amount: num });
    }, 600);
  }, [config.auto_trade_enabled, config.threshold]);

  const handleSimulate = async () => {
    if (!simPlayer) { setSimResult('Select a player'); return; }
    setSimLoading(true); setSimResult('');
    try {
      const { ok, data } = await simulateInjury(simPlayer, simStatus);
      if (ok) {
        setSimResult('Injected');
        const bData = await fetchBetLog();
        setBetLog(bData.rows || []);
      } else {
        setSimResult(data.detail || 'Error');
      }
    } catch (e) { setSimResult(e.message); }
    finally { setSimLoading(false); }
  };

  const sortedPlayers = [...playersDb].sort((a, b) => (a.player_name || '').localeCompare(b.player_name || ''));
  const lastBet = betLog[0] || null;

  const formatOdds = (bet) => {
    const o = Array.isArray(bet.market_outcomes) ? bet.market_outcomes : [];
    const p = Array.isArray(bet.market_prices) ? bet.market_prices : [];
    if (!o.length) return '\u2014';
    return o.map((x, i) => `${x}: ${((p[i] || 0) * 100).toFixed(1)}%`).join(' / ');
  };

  if (loading) return <div className="loading"><div className="spinner" /></div>;

  return (
    <div>
      {/* Status Banner */}
      <div className={`status-banner ${config.auto_trade_enabled ? 'active' : 'inactive'}`}>
        <span className="pulse-dot" />
        {config.auto_trade_enabled
          ? `Auto trading is live \u2014 edge threshold \u00B1${config.threshold}`
          : 'Auto trade is off'}
      </div>

      {/* Config + Simulate Row */}
      <div className="card" style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <div className="toggle-wrap" onClick={handleToggle} style={{ cursor: 'pointer' }}>
            <div className={`toggle-track ${config.auto_trade_enabled ? 'on' : ''}`}>
              <div className="toggle-thumb" />
            </div>
            <span className="toggle-label">{config.auto_trade_enabled ? 'Enabled' : 'Disabled'}</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <label style={{ fontSize: 13, color: '#64748b' }}>Threshold:</label>
            <input className="form-input" type="number" value={config.threshold}
              onChange={(e) => handleThreshold(e.target.value)} style={{ width: 80 }} />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <label style={{ fontSize: 13, color: '#64748b' }}>Bet $:</label>
            <input className="form-input" type="number" value={config.bet_amount}
              onChange={(e) => handleBetAmount(e.target.value)} style={{ width: 80 }} />
          </div>

          <button className="btn btn-danger btn-sm" onClick={() => { clearBetLog().then(() => setBetLog([])); }}>
            Clear History
          </button>

          <div style={{ marginLeft: 'auto' }}>
            <button
              className={`btn btn-sm ${simOpen ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setSimOpen(!simOpen)}
              style={{ fontSize: 12 }}
            >
              {simOpen ? 'Hide Simulate' : 'Simulate Injury'}
            </button>
          </div>
        </div>

        {/* Collapsible simulate section */}
        {simOpen && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
            <select className="form-select" value={simPlayer} onChange={(e) => setSimPlayer(e.target.value)}
              style={{ minWidth: 180 }}>
              <option value="">Select player...</option>
              {sortedPlayers.map((p) => <option key={p.player_name} value={p.player_name}>{p.player_name}</option>)}
            </select>
            <select className="form-select" value={simStatus} onChange={(e) => setSimStatus(e.target.value)}
              style={{ minWidth: 140 }}>
              <option value="Out">Out</option>
              <option value="Doubtful">Doubtful</option>
              <option value="Questionable">Questionable</option>
              <option value="Probable">Probable</option>
              <option value="Available">Available</option>
              <option value="Remove from report">Remove from report</option>
            </select>
            <button className="btn btn-primary btn-sm" onClick={handleSimulate} disabled={simLoading}>
              {simLoading ? 'Running...' : 'Run'}
            </button>
            {simResult && (
              <span style={{ fontSize: 12, fontWeight: 600, color: simResult.includes('Error') || simResult.includes('Select') ? '#dc2626' : '#16a34a' }}>
                {simResult}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Last Trade Card */}
      {lastBet && (
        <div className="last-trade-card" style={{ marginTop: 12 }}>
          <div className="card-label">Latest Trade Signal</div>
          <div className="trade-grid">
            <div>
              <div className="trade-item-label">Matchup</div>
              <div className="trade-item-value">
                <span style={{ color: teamColor(lastBet.away_tricode), fontWeight: 700 }}>{lastBet.away_tricode}</span>
                {' @ '}
                <span style={{ color: teamColor(lastBet.home_tricode), fontWeight: 700 }}>{lastBet.home_tricode}</span>
              </div>
            </div>
            <div>
              <div className="trade-item-label">Edge</div>
              <div className={`trade-item-value ${scoreClass(lastBet.edge_score)}`}>{formatScore(lastBet.edge_score)}</div>
            </div>
            <div>
              <div className="trade-item-label">Team</div>
              <div className="trade-item-value" style={{ color: teamColor(lastBet.profitable_team) }}>{lastBet.profitable_team || '\u2014'}</div>
            </div>
            <div>
              <div className="trade-item-label">Market</div>
              <div className="trade-item-value">{lastBet.market_type || '\u2014'}</div>
            </div>
            <div>
              <div className="trade-item-label">Odds</div>
              <div className="trade-item-value" style={{ fontSize: 13 }}>{formatOdds(lastBet)}</div>
            </div>
            <div>
              <div className="trade-item-label">Logged</div>
              <div className="trade-item-value">{formatDateTime(lastBet.timestamp)}</div>
            </div>
          </div>
        </div>
      )}

      {/* Trade Log */}
      <div className="card" style={{ marginTop: 12 }}>
        <p className="count-label"><strong>{betLog.length}</strong> trade signal(s) logged</p>
        {betLog.length === 0 ? (
          <div className="empty-state">No trades logged yet. Enable auto trade and set a threshold.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Matchup</th>
                  <th>Edge</th>
                  <th>Team</th>
                  <th>Market</th>
                  <th>Odds</th>
                  <th>Closeness</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {betLog.map((b, i) => (
                  <tr key={b.id || i}>
                    <td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(b.timestamp)}</td>
                    <td>
                      <span style={{ color: teamColor(b.away_tricode), fontWeight: 600 }}>{b.away_tricode}</span>
                      {' @ '}
                      <span style={{ color: teamColor(b.home_tricode), fontWeight: 600 }}>{b.home_tricode}</span>
                    </td>
                    <td className={scoreClass(b.edge_score)}>{formatScore(b.edge_score)}</td>
                    <td style={{ color: teamColor(b.profitable_team), fontWeight: 600 }}>{b.profitable_team || '\u2014'}</td>
                    <td>{b.market_type || '\u2014'}</td>
                    <td style={{ fontSize: 12 }}>{formatOdds(b)}</td>
                    <td>{b.market_closeness != null ? Number(b.market_closeness).toFixed(4) : '\u2014'}</td>
                    <td>
                      {b.status || '\u2014'}
                      {b.simulated && <span className="sim-badge">SIM</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
