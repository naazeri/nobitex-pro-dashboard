/**
 * Shared HTTP fetch & async race-condition utilities for the Nobitex Dashboard.
 * 100% offline-compatible native ES module (ADR-012).
 */

export async function fetchJson(url, opts = {}) {
  const resp = await fetch(url, opts);
  let data = null;
  const contentType = resp.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
  } else {
    try {
      const text = await resp.text();
      if (text) data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!resp.ok) {
    const detail = (data && (data.detail || data.message)) || `HTTP ${resp.status}`;
    const err = new Error(detail);
    err.status = resp.status;
    err.payload = data;
    throw err;
  }
  return data;
}

/**
 * Prevents stale out-of-order async responses from overwriting newer UI state.
 * Aborts any previous in-flight fetch via AbortController and tracks monotonic request IDs.
 */
export function createLatestRequestGuard() {
  let latestId = 0;
  let controller = null;

  return {
    begin() {
      if (controller) {
        try {
          controller.abort();
        } catch {}
      }
      controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      latestId += 1;
      return {
        id: latestId,
        signal: controller ? controller.signal : undefined,
      };
    },
    isLatest(id) {
      return id === latestId;
    },
    abort() {
      latestId += 1;
      if (controller) {
        try {
          controller.abort();
        } catch {}
        controller = null;
      }
    },
  };
}
