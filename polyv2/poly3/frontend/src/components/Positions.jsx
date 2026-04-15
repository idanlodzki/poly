import React, { useState, useEffect } from 'react';
import { fetchPositions, sellPosition } from '../api.js';
import { teamColor, formatDateTime, formatScore, scoreClass } from '../utils.js';

export default function Positions({ refreshKey }) {
  const [positions, setPositions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPositions()
      .then((d) => setPositions(d.positions || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [refreshKey]);

  const handleSell = async (posId) => {
    if (!window.confirm('Close this position?')) return;
    try {
      const result = await sellPosition(posId);
      if (result.clob_error) {
        alert(`Position closed locally. CLOB sell note: ${result.clob_error}`);
      }
      const d = await fetchPositions();
      setPositions(d.positions || []);
    } catch (e) {
      alert('Sell failed: ' + e.message);
    }
  };

  if (loading) return <div className="loading"><div className="spinner" /></div>;

  const open = positions.filter((p) => p.status === 'open');
  const closed = positions.filter((p) => p.status !== 'open');
  const calcFee = (pnl) => pnl > 0 ? pnl * 0.02 : 0;
  const totalPnl = positions.reduce((s, p) => s + ((p.pnl || 0) - calcFee(p.pnl || 0)), 0);
  const openPnl = open.reduce((s, p) => s + ((p.pnl || 0) - calcFee(p.pnl || 0)), 0);
  const totalFees = positions.reduce((s, p) => s + calcFee(p.pnl || 0), 0);

  return (
    <div>
      {/* Summary cards */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <div className="card" style={{ flex: 1, minWidth: 150, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: '#64748b', textTransform: 'uppercase' }}>Open Positions</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{open.length}</div>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 150, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: '#64748b', textTransform: 'uppercase' }}>Open P&L</div>
          <div className={scoreClass(openPnl)} style={{ fontSize: 24, fontWeight: 700 }}>${formatScore(openPnl)}</div>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 150, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: '#64748b', textTransform: 'uppercase' }}>Total P&L</div>
          <div className={scoreClass(totalPnl)} style={{ fontSize: 24, fontWeight: 700 }}>${formatScore(totalPnl)}</div>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 150, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: '#64748b', textTransform: 'uppercase' }}>Fees (2%)</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: totalFees > 0 ? '#dc2626' : '#94a3b8' }}>
            {totalFees > 0 ? `-$${totalFees.toFixed(2)}` : '$0'}
          </div>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 150, textAlign: 'center' }}>
          <div style={{ fontSize: 12, color: '#64748b', textTransform: 'uppercase' }}>Closed</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{closed.length}</div>
        </div>
      </div>

      {/* Positions table */}
      <div className="card">
        <p className="count-label"><strong>{positions.length}</strong> position(s)</p>
        {positions.length === 0 ? (
          <div className="empty-state">No positions yet. Enable auto trade to start placing bets.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Game</th>
                  <th>Type</th>
                  <th>Team</th>
                  <th>Outcome</th>
                  <th>Shares</th>
                  <th>Amount</th>
                  <th>Buy %</th>
                  <th>Current %</th>
                  <th>P&L</th>
                  <th>Fee (2%)</th>
                  <th>Status</th>
                  <th>Opened</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const shares = p.shares || 0;
                  const pnlVal = p.pnl || 0;
                  // Polymarket: 0% trading fee, 2% fee on net profit if market resolves in your favor
                  const fee = pnlVal > 0 ? pnlVal * 0.02 : 0;
                  const pnlAfterFee = pnlVal - fee;
                  const buyPct = p.buy_price ? (p.buy_price * 100).toFixed(1) + '%' : '\u2014';
                  const curPct = p.current_price ? (p.current_price * 100).toFixed(1) + '%' : '\u2014';
                  const statusStyle = p.status === 'open'
                    ? { background: '#ecfdf5', color: '#065f46' }
                    : p.status === 'sold'
                    ? { background: '#eff6ff', color: '#1e40af' }
                    : { background: '#f8fafc', color: '#64748b' };

                  return (
                    <tr key={p.id}>
                      <td>
                        <span style={{ color: teamColor(p.away_tricode), fontWeight: 600 }}>{p.away_tricode}</span>
                        <span style={{ color: '#94a3b8' }}> @ </span>
                        <span style={{ color: teamColor(p.home_tricode), fontWeight: 600 }}>{p.home_tricode}</span>
                      </td>
                      <td style={{ fontSize: 12 }}>{p.market_type || '\u2014'}</td>
                      <td style={{ color: teamColor(p.bet_team), fontWeight: 700 }}>{p.bet_team}</td>
                      <td style={{ fontSize: 13 }}>{p.outcome || '\u2014'}</td>
                      <td style={{ fontWeight: 600 }}>{shares.toFixed(1)}</td>
                      <td>${(p.amount_usd || 0).toFixed(2)}</td>
                      <td style={{ fontWeight: 600 }}>{buyPct}</td>
                      <td style={{ fontWeight: 600 }}>{curPct}</td>
                      <td className={scoreClass(pnlAfterFee)} style={{ fontWeight: 700 }}>
                        ${pnlAfterFee.toFixed(2)}
                      </td>
                      <td style={{ fontSize: 12, color: fee > 0 ? '#dc2626' : '#94a3b8' }}>
                        {fee > 0 ? `-$${fee.toFixed(2)}` : '$0'}
                      </td>
                      <td>
                        <span style={{ ...statusStyle, padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600 }}>
                          {p.status}
                        </span>
                      </td>
                      <td style={{ whiteSpace: 'nowrap', fontSize: 12 }}>{formatDateTime(p.created_at)}</td>
                      <td>
                        {(p.status === 'open' || p.status === 'logged') && (
                          <button className="btn btn-danger btn-sm" onClick={() => handleSell(p.id)}
                            style={{ padding: '3px 10px', fontSize: 11 }}>
                            Close
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
