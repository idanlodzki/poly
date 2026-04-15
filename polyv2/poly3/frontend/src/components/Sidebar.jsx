import React from 'react';

const MAIN_NAV = [
  { key: 'injuries', icon: '\u2695', label: 'Injury Report' },
  { key: 'news', icon: '\u25A4', label: 'Caught News' },
  { key: 'games', icon: '\u25C9', label: 'Upcoming Games' },
  { key: 'teamNews', icon: '\u229E', label: 'Team News' },
  { key: 'positions', icon: '\u25B6', label: 'Positions' },
  { key: 'trading', icon: '$', label: 'Trading Room' },
];

const CONFIG_NAV = [
  { key: 'players', icon: '\u25CE', label: 'Player DB' },
  { key: 'transitions', icon: '\u21C4', label: 'Transitions' },
];

export default function Sidebar({ activeTab, onTabChange }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">NBA Poly</div>
      <nav className="sidebar-nav">
        {MAIN_NAV.map((item) => (
          <div
            key={item.key}
            className={'sidebar-item' + (activeTab === item.key ? ' active' : '')}
            onClick={() => onTabChange(item.key)}
          >
            <span className="sidebar-item-icon">{item.icon}</span>
            {item.label}
          </div>
        ))}
      </nav>
      <div className="sidebar-divider" />
      <div className="sidebar-section-label">Configuration</div>
      <nav className="sidebar-nav">
        {CONFIG_NAV.map((item) => (
          <div
            key={item.key}
            className={'sidebar-item' + (activeTab === item.key ? ' active' : '')}
            onClick={() => onTabChange(item.key)}
          >
            <span className="sidebar-item-icon">{item.icon}</span>
            {item.label}
          </div>
        ))}
      </nav>
    </aside>
  );
}
