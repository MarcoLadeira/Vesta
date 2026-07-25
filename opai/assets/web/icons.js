/* Tiny, dependency-free icon registry (#388). Icons inherit currentColor. */
(function (global) {
  const paths = {
    sparkles: '<path d="m12 2 1.4 5.1L18 8.5l-4.6 1.4L12 15l-1.4-5.1L6 8.5l4.6-1.4L12 2Z"/><path d="m19 14 .7 2.3L22 17l-2.3.7L19 20l-.7-2.3L16 17l2.3-.7L19 14Z"/>',
    folder: '<path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H10l2 2h6.5A2.5 2.5 0 0 1 21 8.5v8A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5v-10Z"/>',
    file: '<path d="M6 3h7l5 5v13H6z"/><path d="M13 3v5h5M9 13h6M9 16h6"/>',
    check: '<path d="m5 12 4.2 4L19 6"/>',
    warning: '<path d="M12 3 2.8 20h18.4L12 3Z"/><path d="M12 9v4M12 17h.01"/>',
    error: '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/>',
    pending: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2.5"/>',
    more: '<circle cx="6" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1" fill="currentColor" stroke="none"/>',
    running: '<path d="M12 3a9 9 0 1 0 9 9"/>',
    cancelled: '<circle cx="12" cy="12" r="9"/><path d="m8.5 8.5 7 7"/>',
    arrowLeft: '<path d="m14 6-6 6 6 6"/>',
    arrowRight: '<path d="m10 6 6 6-6 6"/>',
    chevronDown: '<path d="m7 10 5 5 5-5"/>',
    chevronRight: '<path d="m9 6 6 6-6 6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    close: '<path d="m7 7 10 10m0-10L7 17"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.1 2.1-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-3v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1-2.1-2.1.1-.1A1.7 1.7 0 0 0 7 15a1.7 1.7 0 0 0-1.5-1H5.3v-3h.2A1.7 1.7 0 0 0 7 10a1.7 1.7 0 0 0-.3-1.9l-.1-.1L8.7 6l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5v-.2h3v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1 2.1 2.1-.1.1A1.7 1.7 0 0 0 19.4 10a1.7 1.7 0 0 0 1.5 1h.2v3h-.2a1.7 1.7 0 0 0-1.5 1Z"/>',
    panel: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M12 5v14"/>',
    minimize: '<path d="M6 12h12"/>',
    maximize: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
    workspace: '<path d="M4 5h16v14H4z"/><path d="M4 10h16M8 5v5"/>',
    copy: '<rect x="9" y="9" width="10" height="10" rx="2"/><path d="M15 5H6a1 1 0 0 0-1 1v9"/>',
    copyDiff: '<path d="M8 4v10M8 4 5 7M8 4l3 3M16 20V10m0 10 3-3m-3 3-3-3"/>',
  };

  function icon(name, options) {
    const opts = options || {};
    const label = opts.label ? ` role="img" aria-label="${String(opts.label).replace(/&/g, "&amp;").replace(/\"/g, "&quot;")}"` : ' aria-hidden="true"';
    const path = paths[name] || paths.warning;
    return `<svg class="ui-icon${opts.className ? ` ${opts.className}` : ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" focusable="false"${label}>${path}</svg>`;
  }

  global.OPaiIcons = Object.freeze({ icon });
  document.querySelectorAll("[data-icon]").forEach((slot) => { slot.innerHTML = icon(slot.dataset.icon); });
})(window);
