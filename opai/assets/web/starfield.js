/**
 * Shooting-star background — one canvas, no DOM churn, no framework.
 *
 * Replaces six absolutely-positioned <i> elements that each ran their own CSS
 * animation. That version had no way to be occasional: a CSS animation either
 * repeats forever on a fixed schedule or does not run, so the streaks arrived
 * like a metronome and read as a screensaver rather than as something you
 * happen to catch.
 *
 * The motion is radial and outward from a vanishing point rather than a slide
 * across the screen, which is what makes it read as travelling forward instead
 * of sideways. Speed and trail length grow with distance from that point, the
 * way parallax actually behaves, so a streak accelerates as it reaches the
 * edge.
 *
 * Everything here is decorative, so the hard rule is that it must never cost
 * the application a frame:
 *
 *   - one canvas, `pointer-events: none`, never touched by layout;
 *   - the static field is drawn once to an offscreen buffer and blitted, so
 *     the per-frame work is a handful of gradient strokes and nothing else;
 *   - the pool is allocated up front and reused, so a long session allocates
 *     nothing and the collector never runs on our account;
 *   - motion is time-based off the rAF timestamp, so it is identical at 60,
 *     120 and 144Hz, and a clamp stops a backgrounded tab teleporting stars;
 *   - rendering moves to a worker via OffscreenCanvas where that is possible,
 *     and falls back cleanly where it is not -- which includes this app's own
 *     production case, since QtWebEngine loads the UI over file:// and workers
 *     are blocked at an opaque origin.
 */
(function (global) {
  "use strict";

  const CONFIG = {
    enabled: true,
    // Static field: count scales with area, clamped so a large monitor does
    // not turn a sparse sky into a texture.
    starfieldDensity: 1 / 14000, // stars per CSS px²
    starfieldMin: 40,
    starfieldMax: 120,
    starRadius: [0.3, 1.2],
    starOpacity: [0.05, 0.3],
    // A tiny subset breathes. Animating the whole field would mean redrawing
    // it every frame, which is exactly the cost this design exists to avoid.
    shimmerFraction: 0.12,
    // Shooting stars.
    maxActive: 3,
    spawnInterval: [2.5, 7.0], // seconds
    speed: [520, 1180], // CSS px/sec at the reference distance
    lifetime: [0.55, 1.1], // seconds
    trailLength: [150, 340],
    coreWidth: [1.0, 1.8],
    glowWidth: [3.5, 6],
    headRadius: [1.1, 2.2],
    maxOpacity: [0.55, 0.95],
    // Where the travel appears to come from, as a fraction of the canvas.
    // Slightly above centre reads as horizon rather than as a bullseye.
    vanishingPoint: [0.5, 0.42],
    // Stars are born a little way out, never exactly at the point: a streak
    // that starts at zero radius has no direction to inherit and flickers.
    birthRadius: [60, 190],
    maxDpr: 2,
    // A tab restored after minutes would otherwise advance every star by the
    // whole elapsed time in one step.
    maxFrameSeconds: 1 / 20,
  };

  function lerp(a, b, t) { return a + (b - a) * t; }
  function pick(range) { return lerp(range[0], range[1], Math.random()); }

  /**
   * The renderer.
   *
   * Deliberately written as one self-contained function of primitives so the
   * exact same source can run on the main thread or be stringified into a
   * worker. Two implementations of a render loop would drift, and the one that
   * drifted would be the one nobody could see running.
   */
  function createRenderer(ctx, config) {
    let width = 0;
    let height = 0;
    let dpr = 1;
    let colour = "255,255,255";
    let reduced = false;

    let field = null; // pre-rendered static starfield
    let shimmer = []; // the few stars that breathe
    const pool = [];
    let nextSpawn = 0;
    let elapsed = 0;

    for (let i = 0; i < config.maxActive; i += 1) pool.push({ alive: false });

    function buildField() {
      const area = width * height;
      const count = Math.max(
        config.starfieldMin,
        Math.min(config.starfieldMax, Math.round(area * config.starfieldDensity)),
      );
      // Drawn once into its own buffer and blitted each frame. Re-drawing a
      // hundred arcs per frame is most of the cost of a naive starfield.
      const buffer = typeof OffscreenCanvas !== "undefined"
        ? new OffscreenCanvas(Math.max(1, width * dpr), Math.max(1, height * dpr))
        : null;
      shimmer = [];
      if (!buffer) { field = null; return; }
      const bx = buffer.getContext("2d");
      bx.scale(dpr, dpr);
      for (let i = 0; i < count; i += 1) {
        const x = Math.random() * width;
        const y = Math.random() * height;
        const r = pick(config.starRadius);
        const a = pick(config.starOpacity);
        bx.beginPath();
        bx.arc(x, y, r, 0, Math.PI * 2);
        bx.fillStyle = "rgba(" + colour + "," + a.toFixed(3) + ")";
        bx.fill();
        if (Math.random() < config.shimmerFraction) {
          shimmer.push({ x, y, r, a, phase: Math.random() * Math.PI * 2, rate: 0.4 + Math.random() * 0.5 });
        }
      }
      field = buffer;
    }

    function spawn() {
      const star = pool.find((s) => !s.alive);
      if (!star) return;
      const angle = Math.random() * Math.PI * 2;
      const radius = pick(config.birthRadius);
      const vx = config.vanishingPoint[0] * width;
      const vy = config.vanishingPoint[1] * height;
      star.alive = true;
      star.age = 0;
      star.lifetime = pick(config.lifetime);
      star.x = vx + Math.cos(angle) * radius;
      star.y = vy + Math.sin(angle) * radius;
      star.dirX = Math.cos(angle);
      star.dirY = Math.sin(angle);
      star.speed = pick(config.speed);
      star.trail = pick(config.trailLength);
      star.core = pick(config.coreWidth);
      star.glow = pick(config.glowWidth);
      star.head = pick(config.headRadius);
      star.peak = pick(config.maxOpacity);
    }

    function step(dt) {
      elapsed += dt;
      if (elapsed >= nextSpawn) {
        spawn();
        nextSpawn = elapsed + pick(config.spawnInterval);
      }
      const diagonal = Math.hypot(width, height);
      for (let i = 0; i < pool.length; i += 1) {
        const s = pool[i];
        if (!s.alive) continue;
        s.age += dt;
        // Perspective: the further from the vanishing point, the faster it
        // travels and the longer it streaks. This is the whole reason it
        // reads as forward motion rather than as drift.
        const vx = config.vanishingPoint[0] * width;
        const vy = config.vanishingPoint[1] * height;
        const dist = Math.hypot(s.x - vx, s.y - vy);
        const scale = 0.35 + Math.min(1.9, dist / (diagonal * 0.32));
        s.x += s.dirX * s.speed * scale * dt;
        s.y += s.dirY * s.speed * scale * dt;
        s.currentTrail = s.trail * Math.min(1.6, scale);
        const t = s.age / s.lifetime;
        // In fast, out slow: a streak that fades in gradually looks like a
        // fault in the display rather than something arriving.
        s.alpha = t < 0.12
          ? s.peak * (t / 0.12)
          : s.peak * Math.max(0, 1 - (t - 0.12) / 0.88);
        if (s.age >= s.lifetime || dist > diagonal * 0.75) s.alive = false;
      }
    }

    function draw() {
      ctx.clearRect(0, 0, width, height);
      if (field) ctx.drawImage(field, 0, 0, width, height);
      for (let i = 0; i < shimmer.length; i += 1) {
        const st = shimmer[i];
        const a = st.a * (0.65 + 0.35 * Math.sin(elapsed * st.rate + st.phase));
        ctx.beginPath();
        ctx.arc(st.x, st.y, st.r, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(" + colour + "," + a.toFixed(3) + ")";
        ctx.fill();
      }
      if (reduced) return;

      // Additive only for the streaks: it is what makes a trail read as light
      // rather than as paint, and it is confined to a few thin strokes.
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < pool.length; i += 1) {
        const s = pool[i];
        if (!s.alive || s.alpha <= 0.002) continue;
        const tailX = s.x - s.dirX * s.currentTrail;
        const tailY = s.y - s.dirY * s.currentTrail;
        // A gradient per visible star per frame: at most three, which is far
        // cheaper than the per-frame shadowBlur it replaces.
        const g = ctx.createLinearGradient(tailX, tailY, s.x, s.y);
        g.addColorStop(0, "rgba(" + colour + ",0)");
        g.addColorStop(0.55, "rgba(" + colour + "," + (s.alpha * 0.18).toFixed(3) + ")");
        g.addColorStop(0.88, "rgba(" + colour + "," + (s.alpha * 0.7).toFixed(3) + ")");
        g.addColorStop(1, "rgba(" + colour + "," + s.alpha.toFixed(3) + ")");
        ctx.strokeStyle = g;
        ctx.lineCap = "round";
        // Two passes: a wide faint one for bloom, a thin bright core. Cheaper
        // and more controllable than a real blur.
        ctx.lineWidth = s.glow;
        ctx.globalAlpha = 0.35;
        ctx.beginPath();
        ctx.moveTo(tailX, tailY);
        ctx.lineTo(s.x, s.y);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.lineWidth = s.core;
        ctx.beginPath();
        ctx.moveTo(tailX, tailY);
        ctx.lineTo(s.x, s.y);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.head, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(" + colour + "," + s.alpha.toFixed(3) + ")";
        ctx.fill();
      }
      ctx.globalCompositeOperation = "source-over";
      ctx.globalAlpha = 1;
    }

    return {
      resize(w, h, ratio) {
        width = w; height = h; dpr = ratio;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.scale(dpr, dpr);
        buildField();
      },
      setColour(value) { colour = value; buildField(); },
      setReduced(value) { reduced = value; },
      frame(dt) { step(dt); draw(); },
    };
  }

  // The loop, shared by both hosts. Kept separate from the renderer so the
  // worker can own its own timing without duplicating any drawing code.
  function createLoop(renderer, config, requestFrame, cancelFrame) {
    let handle = 0;
    let last = 0;
    let running = false;
    function tick(now) {
      if (!running) return;
      const dt = last ? Math.min(config.maxFrameSeconds, (now - last) / 1000) : 0;
      last = now;
      renderer.frame(dt);
      handle = requestFrame(tick);
    }
    return {
      start() {
        if (running) return; // Strict-mode double-mounts must not double-loop.
        running = true; last = 0;
        handle = requestFrame(tick);
      },
      stop() {
        running = false;
        if (handle) cancelFrame(handle);
        handle = 0;
      },
      get running() { return running; },
    };
  }

  function workerSource() {
    return (
      "const CONFIG=" + JSON.stringify(CONFIG) + ";\n" +
      "const lerp=" + lerp.toString() + ";\n" +
      "const pick=" + pick.toString() + ";\n" +
      "const createRenderer=" + createRenderer.toString() + ";\n" +
      "const createLoop=" + createLoop.toString() + ";\n" +
      "let renderer=null,loop=null;\n" +
      "self.onmessage=(e)=>{const m=e.data;\n" +
      " if(m.type==='init'){const ctx=m.canvas.getContext('2d');renderer=createRenderer(ctx,CONFIG);" +
      "  renderer.setColour(m.colour);renderer.setReduced(m.reduced);renderer.resize(m.width,m.height,m.dpr);" +
      "  loop=createLoop(renderer,CONFIG,requestAnimationFrame,cancelAnimationFrame);loop.start();return;}\n" +
      " if(!renderer)return;\n" +
      " if(m.type==='resize')renderer.resize(m.width,m.height,m.dpr);\n" +
      " else if(m.type==='colour')renderer.setColour(m.colour);\n" +
      " else if(m.type==='reduced')renderer.setReduced(m.reduced);\n" +
      " else if(m.type==='play')loop.start();\n" +
      " else if(m.type==='pause')loop.stop();\n" +
      "};\n"
    );
  }

  /**
   * Whether a blob: worker is permitted by the page's own policy.
   *
   * Read from the CSP meta tag rather than discovered by throwing: the browser
   * reports a violation to the console even when the exception is caught, and
   * a decorative background has no business logging an error on every boot.
   */
  function workerAllowed() {
    if (typeof Worker !== "function" || typeof OffscreenCanvas === "undefined") return false;
    const meta = global.document.querySelector('meta[http-equiv="Content-Security-Policy"]');
    if (!meta) return true; // no policy in the document: let the try/catch decide
    const policy = String(meta.getAttribute("content") || "");
    const directive = /worker-src ([^;]*)/.exec(policy) || /script-src ([^;]*)/.exec(policy);
    return !directive || /blob:/.test(directive[1]);
  }

  function readColour(host) {
    // Derived from the theme rather than hard-coded, so the field belongs to
    // whatever palette the app is wearing. --space-star is already the colour
    // the dark stage uses for a point of light.
    const raw = getComputedStyle(host).getPropertyValue("--space-star").trim();
    const match = raw.match(/(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
    return match ? match[1] + "," + match[2] + "," + match[3] : "226,238,252";
  }

  function mount(canvas) {
    if (!CONFIG.enabled || !canvas || canvas.dataset.mounted) return null;
    canvas.dataset.mounted = "1";

    const motion = global.matchMedia("(prefers-reduced-motion: reduce)");
    const state = { worker: null, loop: null, renderer: null, observer: null, destroyed: false };
    const dprOf = () => Math.min(global.devicePixelRatio || 1, CONFIG.maxDpr);

    let width = canvas.clientWidth || 1;
    let height = canvas.clientHeight || 1;
    let dpr = dprOf();
    const colour = readColour(canvas);

    // Try the worker, but only when it can actually be built.
    //
    // Two things had to be learned the hard way here. The app ships a CSP of
    // `script-src 'self' file: qrc:`, which forbids a blob: worker -- and a
    // CSP violation is reported by the browser whether or not the throw is
    // caught, so a try/catch alone leaves an error in the console on every
    // boot. The policy is therefore read first and the attempt skipped
    // outright when blob: is not permitted.
    //
    // And the worker is constructed *before* control is transferred.
    // Transferring first meant that when the Worker constructor threw, the
    // canvas had already been handed away permanently: getContext('2d') then
    // returned null forever and the sky silently drew nothing at its default
    // 300x150. The irreversible step goes last.
    if (workerAllowed() && typeof canvas.transferControlToOffscreen === "function") {
      try {
        const url = URL.createObjectURL(new Blob([workerSource()], { type: "text/javascript" }));
        const worker = new Worker(url);
        URL.revokeObjectURL(url);
        const offscreen = canvas.transferControlToOffscreen();
        offscreen.width = Math.max(1, Math.round(width * dpr));
        offscreen.height = Math.max(1, Math.round(height * dpr));
        worker.postMessage(
          { type: "init", canvas: offscreen, width, height, dpr, colour, reduced: motion.matches },
          [offscreen],
        );
        state.worker = worker;
      } catch (_error) {
        state.worker = null;
      }
    }

    if (!state.worker) {
      const ctx = canvas.getContext("2d");
      if (!ctx) return null;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      state.renderer = createRenderer(ctx, CONFIG);
      state.renderer.setColour(colour);
      state.renderer.setReduced(motion.matches);
      state.renderer.resize(width, height, dpr);
      state.loop = createLoop(
        state.renderer, CONFIG,
        global.requestAnimationFrame.bind(global),
        global.cancelAnimationFrame.bind(global),
      );
      state.loop.start();
    }

    function send(message) {
      if (state.worker) state.worker.postMessage(message);
    }

    function applySize() {
      const w = canvas.clientWidth || 1;
      const h = canvas.clientHeight || 1;
      const ratio = dprOf();
      if (w === width && h === height && ratio === dpr) return;
      width = w; height = h; dpr = ratio;
      if (state.worker) { send({ type: "resize", width, height, dpr }); return; }
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      state.renderer.resize(width, height, dpr);
    }

    // Observed rather than measured in the loop: reading layout every frame is
    // the classic way a decorative canvas starts costing real money.
    let resizeHandle = 0;
    state.observer = new ResizeObserver(() => {
      if (resizeHandle) return;
      resizeHandle = global.requestAnimationFrame(() => { resizeHandle = 0; applySize(); });
    });
    state.observer.observe(canvas);

    const onVisibility = () => {
      const hidden = global.document.hidden;
      if (state.worker) { send({ type: hidden ? "pause" : "play" }); return; }
      if (hidden) state.loop.stop(); else state.loop.start();
    };
    global.document.addEventListener("visibilitychange", onVisibility);

    const onMotion = (event) => {
      if (state.worker) { send({ type: "reduced", reduced: event.matches }); return; }
      state.renderer.setReduced(event.matches);
    };
    if (motion.addEventListener) motion.addEventListener("change", onMotion);

    return {
      destroy() {
        if (state.destroyed) return;
        state.destroyed = true;
        if (state.observer) state.observer.disconnect();
        if (resizeHandle) global.cancelAnimationFrame(resizeHandle);
        global.document.removeEventListener("visibilitychange", onVisibility);
        if (motion.removeEventListener) motion.removeEventListener("change", onMotion);
        if (state.loop) state.loop.stop();
        if (state.worker) state.worker.terminate();
        delete canvas.dataset.mounted;
      },
      // The stage owns whether the sky is showing; this owns whether it is
      // costing anything. Pausing is what makes "hidden" free rather than
      // merely invisible.
      setActive(active) {
        if (state.destroyed) return;
        if (state.worker) { send({ type: active ? "play" : "pause" }); return; }
        if (active) state.loop.start(); else state.loop.stop();
      },
      get usingWorker() { return !!state.worker; },
    };
  }

  global.OPaiStarfield = { mount, CONFIG };
})(window);
