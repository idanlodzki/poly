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

export default function Betting({ refreshKey }) {
  const [config, setConfig] = useState({ auto_trade_enabled: false, threshold: 0 });
  const [betLog, setBetLog] = useState([]);
  const [playersDb, setPlayersDb] = useState([]);
  const [loading, setLoading] = useState(true);

  // Simulate state
  const [simPlayer, setSimPlayer] = useState('');
  const [simStatus, setSimStatus] = useState('Out');
  const [simResult, setSimResult] = useState('');
  const [simLoading, setSimLoading] = useState(false);

  const thresholdTimer = useRef(null);

  const load = useCallback(() => {
    Promise.all([fetchBettingConfig(), fetchBetLog(), fetchPlayersDb()])
      .then(([cData, bData, pData]) => {
        setConfig(cData || { auto_trade_enabled: false, threshold: 0 });
        setBetLog(bData.rows || bData.bets || []);
        setPlayersDb(pData.players || []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [refreshKey, load]);

  // Cleanup threshold timer
  useEffect(() => {
    return () => {
      if (thresholdTimer.current) clearTimeout(thresholdTimer.current);
    };
  }, []);

  const handleToggle = useCallback(() => {
    const next = !config.auto_trade_enabled;
    setConfig((prev) => ({ ...prev, auto_trade_enabled: next }));
    updateBettingConfig({ auto_trade_enabled: next, threshold: config.threshold });
  }, [config]);

  const handleThresholdChange = useCallback(
    (value) => {
      const num = Number(value);
      setConfig((prev) => ({ ...prev, threshold: num }));
      if (thresholdTimer.current) clearTimeout(thresholdTimer.current);
      thresholdTimer.current = setTimeout(() => {
        updateBettingConfig({ auto_trade_enabled: config.auto_trade_enabled, threshold: num });
      }, 600);
    },
    [config.auto_trade_enabled],
  );

  const handleClearHistory = () => {
    clearBetLog().then(() => setBetLog([]));
  };

  const handleSimulate = async () => {
    if (!simPlayer) {
      setSimResult('Select a player');
      return;
    }
    setSimLoading(true);
    setSimResult('');
    try {
      const { ok, data } = await simulateInjury(simPlayer, simStatus);
      if (ok) {
        setSimResult('Simulation successful');
        // Re-fetch data
        const [bData, nData] = await Promise.all([fetchBetLog(), fetchNewsLog(), fetchNotifications()]);
        setBetLog(bData.rows || bData.bets || []);
      } else {
        setSimResult(`Error: ${data.detail || data.error || 'Unknown error'}`);
      }
    } catch (err) {
      setSimResult(`Error: ${err.message}`);
    } finally {
      setSimLoading(false);
    }
  };

  const sortedPlayers = [...playersDb].sort((a, b) =>
    (a.player_name || '').localeCompare(b.player_name || ''),
  );

  const lastBet = betLog[0] || null;

  const formatOdds = (bet) => {
    if (!bet.market_outcomes || !bet.market_prices) return '\u2014';
    const outcomes = Array.isArray(bet.market_outcomes) ? bet.market_outcomes : [];
    const prices = Array.isArray(bet.market_prices) ? bet.market_prices : [];
    return outcomes
      .map((o, i) => `${o}: ${((prices[i] || 0) * 100).toFixed(1)}%`)
      .join(' / ');
  };

  if (loading) {
    return (
      <div className="loading">
        <div className="spinner" />
      </div>
    );
  }

  return (
    <div>
      {/* Status Banner */}
      <div className={`status-banner ${config.auto_trade_enabled ? 'active' : 'inactive'}`}>
        {config.auto_trade_enabled ? (
          <span>
            <span className="pulse-dot" /> Auto trading is live — edge threshold +/-{config.threshold}
          </span>
        ) : (
          <span>Auto trade is off</span>
        )}
      </div>

      {/* Config Card */}
      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <div className="toggle-wrap" onClick={handleToggle} style={{ cursor: 'pointer' }}>
            <div className={`toggle-track ${config.auto_trade_enabled ? 'on' : ''}`}>
              <div className="toggle-thumb" />
            </div>
            <span className="toggle-label">
              {config.auto_trade_enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>

          <div>
            <label style={{ fontSize: '0.8em', marginRight: 6 }}>Threshold:</label>
            <input
              className="form-input"
              type="number"
              value={config.threshold}
              onChange={(e) => handleThresholdChange(e.target.value)}
              style={{ width: 80 }}
            />
          </div>

          <button className="btn btn-danger btn-sm" onClick={handleClearHistory}>
            Clear History
          </button>
        </div>
      </div>

      {/* Simulate Injury Card */}
      <div className="sim-card" style={{ marginTop: 16 }}>
        <h3 style={{ margin: '0 0 12px' }}>Simulate Injury</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select
            className="form-select"
            value={simPlayer}
            onChange={(e) => setSimPlayer(e.target.value)}
          >
            <option value="">Select player</option>
            {sortedPlayers.map((p) => (
              <option key={p.player_name} value={p.player_name}>
                {p.player_name}
              </option>
            ))}
          </select>

          <select
            className="form-select"
            value={simStatus}
            onChange={(e) => setSimStatus(e.target.value)}
          >
            <option value="Out">Out</option>
            <option value="Doubtful">Doubtful</option>
            <option value="Questionable">Questionable</option>
            <option value="Probable">Probable</option>
            <option value="Available">Available</option>
            <option value="Remove from report">Remove from report</option>
          </select>

          <button
            className="btn btn-primary btn-sm"
            onClick={handleSimulate}
            disabled={simLoading}
          >
            {simLoading ? 'Simulating...' : 'Simulate'}
          </button>

          {simResult && (
            <span
              style={{
                fontSize: '0.85em',
                color: simResult.startsWith('Error') ? '#dc2626' : '#16a34a',
                fontWeight: 600,
              }}
            >
              {simResult}
            </span>
          )}
        </div>
      </div>

      {/* Last Trade Card */}
      {lastBet && (
        <div className="last-trade-card" style={{ marginTop: 16 }}>
          <div className="card-label">Last Trade</div>
          <div className="trade-grid">
            <div>
              <div className="trade-item-label">Matchup</div>
              <div className="trade-item-value">
                <span style={{ color: teamColor(lastBet.away_tricode), fontWeight: 700 }}>
                  {lastBet.away_tricode}
                </span>
                {' @ '}
                <span style={{ color: teamColor(lastBet.home_tricode), fontWeight: 700 }}>
                  {lastBet.home_tricode}
                </span>
              </div>
            </div>
            <div>
              <div className="trade-item-label">Edge Score</div>
              <div className={`trade-item-value ${scoreClass(lastBet.edge_score)}`}>
                {formatScore(lastBet.edge_score)}
              </div>
            </div>
            <div>
              <div className="trade-item-label">Profitable Team</div>
              <div className="trade-item-value" style={{ color: teamColor(lastBet.profitable_team), fontWeight: 700 }}>
                {lastBet.profitable_team || '\u2014'}
              </div>
            </div>
            <div>
              <div className="trade-item-label">Market Type</div>
              <div className="trade-item-value">{lastBet.market_type || '\u2014'}</div>
            </div>
            <div>
              <div className="trade-item-label">Odds</div>
              <div className="trade-item-value">{formatOdds(lastBet)}</div>
            </div>
            <div>
              <div className="trade-item-label">Closeness</div>
              <div className="trade-item-value">{lastBet.market_closeness ?? '\u2014'}</div>
            </div>
            <div>
              <div className="trade-item-label">Logged At</div>
              <div className="trade-item-value">{formatDateTime(lastBet.timestamp)}</div>
            </div>
          </div>
        </div>
      )}

      {/* Bet Log Table */}
      <div className="card" style={{ marginTop: 16 }}>
        {betLog.length === 0 ? (
          <div className="empty-state">No bets logged yet</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Matchup</th>
                <th>Edge Score</th>
                <th>Profitable Team</th>
                <th>Market Type</th>
                <th>Market Odds</th>
                <th>Closeness</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {betLog.map((bet, i) => (
                <tr key={bet.id || i}>
                  <td>{formatDateTime(bet.timestamp)}</td>
                  <td>
                    <span style={{ color: teamColor(bet.away_tricode), fontWeight: 600 }}>
                      {bet.away_tricode}
                    </span>
                    {' @ '}
                    <span style={{ color: teamColor(bet.home_tricode), fontWeight: 600 }}>
                      {bet.home_tricode}
                    </span>
                  </td>
                  <td className={scoreClass(bet.edge_score)}>
                    {formatScore(bet.edge_score)}
                  </td>
                  <td style={{ color: teamColor(bet.profitable_team), fontWeight: 600 }}>
                    {bet.profitable_team || '\u2014'}
                  </td>
                  <td>{bet.market_type || '\u2014'}</td>
                  <td>{formatOdds(bet)}</td>
                  <td>{bet.market_closeness ?? '\u2014'}</td>
                  <td>
                    {bet.status || '\u2014'}
                    {bet.simulated && <span className="sim-badge">SIM</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
