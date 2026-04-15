import React, { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import NotificationPanel from './components/NotificationPanel';
import InjuryReport from './components/InjuryReport';
import UpcomingGames from './components/UpcomingGames';
import TeamGameNews from './components/TeamGameNews';
import CaughtNews from './components/CaughtNews';
import PlayerDatabase from './components/PlayerDatabase';
import TransitionScores from './components/TransitionScores';
import Positions from './components/Positions';
import TradingRoom from './components/TradingRoom';
import { fetchReport, fetchNotifications, clearNotifications, fetchPlayersDb, fetchTransitionConfig } from './api';
import { formatDateTime } from './utils';

const PAGE_TITLES = {
  injuries: 'Injury Report',
  games: 'Upcoming Games',
  teamNews: 'Team Game News',
  news: 'Caught News',
  players: 'Player Database',
  transitions: 'Transition Scores',
  positions: 'Positions',
  trading: 'Trading Room',
};

export default function App() {
  const [activeTab, setActiveTab] = useState('injuries');
  const [notifications, setNotifications] = useState([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [serverStatus, setServerStatus] = useState(null);
  const [lastReportAt, setLastReportAt] = useState('');
  const [playersDb, setPlayersDb] = useState([]);
  const [transitionConfigs, setTransitionConfigs] = useState([]);

  const loadShared = useCallback(() => {
    fetchNotifications().then((d) => setNotifications(d?.notifications || [])).catch(() => {});
    fetchReport().then((d) => { setServerStatus(d?.status || null); setLastReportAt(d?.last_report_at || ''); }).catch(() => setServerStatus(null));
    fetchPlayersDb().then((d) => setPlayersDb(d?.players || [])).catch(() => {});
    fetchTransitionConfig().then((d) => setTransitionConfigs(d?.rows || [])).catch(() => {});
  }, []);

  useEffect(() => {
    loadShared();
    const interval = setInterval(() => {
      setRefreshKey((k) => k + 1);
      loadShared();
    }, 10000);
    return () => clearInterval(interval);
  }, [loadShared]);

  const handleClearNotifs = useCallback(() => {
    clearNotifications().then(() => setNotifications([])).catch(() => {});
  }, []);

  const pollMode = serverStatus?.poll_mode || 'normal';
  const isAggressive = pollMode === 'aggressive';
  const pollLabel = isAggressive ? 'Aggressive polling (1s)' : 'Normal polling (2m)';
  const connected = !!serverStatus;

  return (
    <div className="app-layout">
      <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />
      <div className="main-content">
        <div className="page-header">
          <div className="page-header-row">
            <h1 className="page-title">{PAGE_TITLES[activeTab] || 'NBA Poly'}</h1>
            <span className={`status-pill ${connected ? (isAggressive ? 'aggressive' : 'normal') : 'disconnected'}`}>
              {connected ? pollLabel : 'Connecting...'}
            </span>
            {lastReportAt && (
              <span className="last-report-label">
                Last report: <strong>{formatDateTime(lastReportAt)}</strong>
              </span>
            )}
            <button className="notif-header-btn" onClick={() => setNotifOpen(true)}>
              {'\u{1F514}'}
              {notifications.length > 0 && <span className="notif-header-badge">{notifications.length}</span>}
            </button>
          </div>
          <p className="page-subtitle">
            Live injury feed &middot; Auto-refresh every 10s
          </p>
        </div>
        {activeTab === 'injuries' && <InjuryReport refreshKey={refreshKey} />}
        {activeTab === 'news' && <CaughtNews refreshKey={refreshKey} />}
        {activeTab === 'games' && <UpcomingGames refreshKey={refreshKey} />}
        {activeTab === 'teamNews' && <TeamGameNews refreshKey={refreshKey} />}
        {activeTab === 'positions' && <Positions refreshKey={refreshKey} />}
        {activeTab === 'trading' && <TradingRoom refreshKey={refreshKey} />}
        {activeTab === 'players' && <PlayerDatabase refreshKey={refreshKey} />}
        {activeTab === 'transitions' && <TransitionScores refreshKey={refreshKey} />}
      </div>
      <NotificationPanel
        notifications={notifications}
        playersDb={playersDb}
        transitionConfigs={transitionConfigs}
        isOpen={notifOpen}
        onClose={() => setNotifOpen(false)}
        onClear={handleClearNotifs}
      />
    </div>
  );
}
