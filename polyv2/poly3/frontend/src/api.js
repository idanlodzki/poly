const json = (res) => res.json();

export function fetchReport() {
  return fetch('/api/report').then(json);
}

export function fetchNotifications() {
  return fetch('/api/notifications').then(json);
}

export function clearNotifications() {
  return fetch('/api/clear-notifications').then(json);
}

export function fetchUpcomingGames() {
  return fetch('/api/upcoming-games').then(json);
}

export function fetchPolymarketGame(slug) {
  return fetch(`/api/polymarket-game?slug=${encodeURIComponent(slug)}`).then(json);
}

export function fetchPolymarketLive() {
  return fetch('/api/polymarket-live').then(json);
}

export function fetchNewsLog() {
  return fetch('/api/news-log').then(json);
}

export function clearNewsLog() {
  return fetch('/api/clear-news-log').then(json);
}

export function fetchPlayersDb() {
  return fetch('/api/players-db').then(json);
}

export function upsertPlayer({ player_name, nba_team, importance }) {
  return fetch('/api/players-db/upsert', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ player_name, nba_team, importance }),
  }).then(json);
}

export function deletePlayer(playerName) {
  return fetch('/api/players-db/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ player_name: playerName }),
  }).then(json);
}

export function fetchTransitionConfig() {
  return fetch('/api/transition-config').then(json);
}

export function upsertTransitionConfig({ transition_type, from_state, to_state, score }) {
  return fetch('/api/transition-config/upsert', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transition_type, from_state, to_state, score }),
  }).then(json);
}

export function fetchBettingConfig() {
  return fetch('/api/betting-config').then(json);
}

export function updateBettingConfig({ auto_trade_enabled, threshold, bet_amount, block_hour_start, block_hour_end }) {
  return fetch('/api/betting-config/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto_trade_enabled, threshold, bet_amount, block_hour_start, block_hour_end }),
  }).then(json);
}

export function fetchBetLog() {
  return fetch('/api/bet-log').then(json);
}

export function clearBetLog() {
  return fetch('/api/clear-bet-log').then(json);
}

export function fetchLatestBatch() {
  return fetch('/api/latest-batch').then(json);
}

export async function simulateInjury(playerName, targetStatus) {
  const res = await fetch('/api/simulate-injury', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ player_name: playerName, target_status: targetStatus }),
  });
  return { ok: res.ok, data: await res.json() };
}

export function fetchPositions() {
  return fetch('/api/positions').then(json);
}

export function sellPosition(positionId) {
  return fetch('/api/positions/sell', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ position_id: positionId }),
  }).then(json);
}

export function fetchTweets() {
  return fetch('/api/tweets').then(json);
}

export function clearTweets() {
  return fetch('/api/clear-tweets').then(json);
}

export function fetchTwitterLog() {
  return fetch('/api/twitter-log').then(json);
}

export function fetchTwitterStatus() {
  return fetch('/api/twitter-status').then(json);
}

export function toggleTwitter() {
  return fetch('/api/twitter-toggle').then(json);
}
