/**
 * Local WebSocket bridge client manager for ws://127.0.0.1:8080/ws (ADR-012).
 * Handles duplicate-connection guards, exponential backoff, sticky private refresh
 * debouncing, and clean unmount teardown.
 */

import { normalizeWsResolution } from './utils.js?v=2.2.0';

export function createLocalWsClient({
  onStatus,
  onMarketStats,
  onCandle,
  onPrivateRefresh,
  onOpen,
}) {
  let socket = null;
  let reconnectTimer = null;
  let reconnectDelay = 2000;
  let privateRefreshTimer = null;
  let pendingIncludeTrades = false;
  let stopped = false;

  function schedulePrivateRefresh(includeTrades = false) {
    pendingIncludeTrades = pendingIncludeTrades || Boolean(includeTrades);
    if (privateRefreshTimer) clearTimeout(privateRefreshTimer);
    privateRefreshTimer = setTimeout(() => {
      privateRefreshTimer = null;
      const shouldIncludeTrades = pendingIncludeTrades;
      pendingIncludeTrades = false;
      if (typeof onPrivateRefresh === 'function') {
        onPrivateRefresh(shouldIncludeTrades);
      }
    }, 600);
  }

  function send(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      try {
        socket.send(JSON.stringify(payload));
      } catch {}
    }
  }

  function connect() {
    stopped = false;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (
      socket &&
      (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    try {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${proto}//${window.location.host}/ws`;
      const ws = new WebSocket(wsUrl);
      socket = ws;

      ws.onopen = () => {
        if (socket !== ws) return;
        reconnectDelay = 2000;
        if (typeof onOpen === 'function') onOpen();
      };

      ws.onmessage = (evt) => {
        if (socket !== ws) return;
        try {
          const msg = JSON.parse(evt.data);
          if (!msg || typeof msg !== 'object') return;
          if (msg.type === 'status') {
            if (typeof onStatus === 'function') onStatus(Boolean(msg.upstream_connected));
          } else if (msg.type === 'market_stats' && msg.data) {
            if (typeof onStatus === 'function') onStatus(true);
            if (typeof onMarketStats === 'function') onMarketStats(msg.data);
          } else if (msg.type === 'candle' && msg.bar) {
            const sym = String(msg.symbol || '').toUpperCase();
            const res = normalizeWsResolution(msg.resolution);
            if (typeof onCandle === 'function') onCandle(sym, res, msg.bar);
          } else if (msg.type === 'private_order') {
            schedulePrivateRefresh(false);
          } else if (msg.type === 'private_trade') {
            schedulePrivateRefresh(true);
          }
        } catch {}
      };

      ws.onclose = () => {
        if (socket === ws) {
          socket = null;
        }
        if (typeof onStatus === 'function') onStatus(false);
        if (!stopped) {
          reconnectTimer = setTimeout(() => {
            reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
            connect();
          }, reconnectDelay);
        }
      };

      ws.onerror = () => {
        if (typeof onStatus === 'function') onStatus(false);
      };
    } catch {
      if (typeof onStatus === 'function') onStatus(false);
    }
  }

  function disconnect() {
    stopped = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (privateRefreshTimer) {
      clearTimeout(privateRefreshTimer);
      privateRefreshTimer = null;
    }
    pendingIncludeTrades = false;
    if (socket) {
      const s = socket;
      socket = null;
      try {
        s.close(1000, 'Client unmount');
      } catch {}
    }
  }

  return { connect, disconnect, send, schedulePrivateRefresh };
}
