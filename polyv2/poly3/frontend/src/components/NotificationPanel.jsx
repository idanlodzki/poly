import React, { useMemo } from 'react';
import { ICON_MAP, formatDateTime, formatScore, scoreClass, statusBadgeClass } from '../utils';

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

function transitionStatesForRow(row) {
  const rtype = row.type || '';
  if (rtype === 'added') return { type: 'added', from: row.from_status || 'Not On Report', to: row.to_status || row.status || '' };
  if (rtype === 'removed') return { type: 'removed', from: row.from_status || row.status || '', to: row.to_status || 'Removed' };
  if (rtype === 'status_change') return { type: 'status_change', from: row.from_status || '', to: row.to_status || row.status || '' };
  return { type: rtype, from: row.from_status || '', to: row.to_status || '' };
}

function NotifItem({ item, getImportance, getScore }) {
  const type = item.type || 'status_change';
  const iconChar = ICON_MAP[type] || '~';
  const score = getScore(item);
  const status = item.status || item.to_status || '';

  return (
    <div className="notif-item">
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span className={'notif-icon ' + type}>{iconChar}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>{item.player || '\u2014'}</span>
            <span style={{ fontSize: 12, color: '#94a3b8' }}>({item.team || ''})</span>
            {status && <span className={statusBadgeClass(status)} style={{ fontSize: 11, padding: '2px 8px' }}>{status}</span>}
          </div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 3 }}>
            {item.detail || ''}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4, fontSize: 12 }}>
            <span className={scoreClass(score)} style={{ fontSize: 12 }}>
              Score: {formatScore(score)}
            </span>
            <span style={{ color: '#94a3b8' }}>
              {formatDateTime(item.timestamp_at)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function NotificationPanel({ notifications, playersDb, transitionConfigs, isOpen, onClose, onClear }) {
  const importanceMap = useMemo(() => {
    const map = {};
    (playersDb || []).forEach((p) => {
      for (const key of nameLookupKeys(p.player_name)) {
        if (!map[key] || p.importance > map[key]) map[key] = p.importance;
      }
    });
    return map;
  }, [playersDb]);

  const transitionMap = useMemo(() => {
    const map = {};
    (transitionConfigs || []).forEach((t) => {
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

  const getScore = (row) => {
    const imp = getImportance(row.player);
    if (!imp || row.type === 'injury_change') return 0;
    const t = transitionStatesForRow(row);
    const tScore = transitionMap[`${t.type}|${t.from}|${t.to}`] || 0;
    return Math.round((imp * imp * tScore) / 100 * 100) / 100;
  };

  return (
    <>
      <div className={'notif-backdrop' + (isOpen ? ' open' : '')} onClick={onClose} />
      <div className={'notif-overlay' + (isOpen ? ' open' : '')}>
        <div className="notif-header">
          <h3>Notifications ({notifications.length})</h3>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-secondary btn-sm" onClick={onClear}>Clear</button>
            <button className="btn btn-secondary btn-sm" onClick={onClose}>{'\u2715'}</button>
          </div>
        </div>
        <div className="notif-body">
          {notifications.slice(0, 100).map((n, i) => (
            <NotifItem key={i} item={n} getImportance={getImportance} getScore={getScore} />
          ))}
          {!notifications.length && (
            <div className="empty-state">Watching for changes...</div>
          )}
        </div>
      </div>
    </>
  );
}
