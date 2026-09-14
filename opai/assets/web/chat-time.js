(function (global) {
  'use strict';
  function stamp(value, now = new Date()) {
    if (!value) return null;
    const date = new Date(value);
    if (!Number.isFinite(date.valueOf())) return null;
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    const day = date.toDateString() === now.toDateString() ? 'Today' : date.toDateString() === yesterday.toDateString() ? 'Yesterday' : date.toLocaleDateString([], { month: 'short', day: 'numeric', ...(date.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) });
    return { iso: date.toISOString(), label: day + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }), title: date.toLocaleString(), minute: Math.floor(date.valueOf() / 60000) };
  }
  global.OPaiChatTime = { stamp };
})(typeof window !== 'undefined' ? window : globalThis);
