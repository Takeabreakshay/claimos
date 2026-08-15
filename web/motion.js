/* ===================== ClaimOS motion engine =====================
   Smooth momentum scroll + scroll-driven choreography.

   Techniques (the things that actually create the "feel"):
     - Lenis          momentum scrolling, everything eases instead of snapping
     - SplitText      headings reveal character by character
     - ScrollTrigger  sections animate as they enter the viewport
     - ScrambleText   numbers "compute" then lock in — which is literally what
                      our decision engine does, so it earns its place here
     - CustomEase     the site's signature curve

   FAIL-SAFE: every helper no-ops if its library is missing, and nothing here
   ever leaves an element hidden. Motion is an enhancement, never a dependency.
================================================================== */
"use strict";

window.MOTION = (function () {
  const has = (n) => typeof window[n] !== "undefined";
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- signature easing ---- */
  let EASE = "power3.out";
  let ENTER = "expo.out";                 // anime.js-style fluid entrance
  if (has("gsap")) {
    if (has("CustomEase")) {
      gsap.registerPlugin(CustomEase);
      try {
        CustomEase.create("sui", "0.645, 0.045, 0.355, 1");
        CustomEase.create("anime", "0.16, 1, 0.3, 1");  // smooth ease-out, soft settle
        EASE = "sui";
        ENTER = "anime";
      } catch (e) { /* keep the fallback */ }
    }
    if (has("ScrollTrigger")) gsap.registerPlugin(ScrollTrigger);
    if (has("SplitText")) gsap.registerPlugin(SplitText);
    if (has("ScrambleTextPlugin")) gsap.registerPlugin(ScrambleTextPlugin);
  }

  /* ---- Lenis smooth scroll ----
     Lenis is driven by requestAnimationFrame. rAF is throttled or paused in
     background/unfocused tabs and some embedded browsers, and a dead loop means
     the page simply will not scroll — the worst possible failure. So we watchdog
     the loop and, if it isn't ticking, tear Lenis down and hand scrolling back to
     the browser with native CSS smoothing. Degraded feel beats a frozen page. */
  let lenis = null;
  let _ticks = 0;

  function nativeFallback(reason) {
    try { if (lenis && lenis.destroy) lenis.destroy(); } catch (e) { /* noop */ }
    lenis = null;
    const html = document.documentElement;
    html.classList.remove("lenis", "lenis-smooth", "lenis-stopped");
    html.style.scrollBehavior = "smooth";
    if (window.console && console.info) console.info("[motion] native scroll:", reason);
  }

  function initScroll() {
    if (reduced()) { document.documentElement.style.scrollBehavior = "auto"; return; }
    if (!has("Lenis")) { nativeFallback("Lenis not loaded"); return; }
    try {
      lenis = new Lenis({
        duration: 1.05,
        easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
        smoothWheel: true,
        wheelMultiplier: 0.9,
      });
      const raf = (time) => {
        if (!lenis) return;
        _ticks++;
        lenis.raf(time);
        requestAnimationFrame(raf);
      };
      requestAnimationFrame(raf);

      if (has("ScrollTrigger")) {
        lenis.on("scroll", ScrollTrigger.update);
      }
      // let it settle, then re-measure (content arrives async) and verify the loop
      setTimeout(() => {
        refresh();
        if (_ticks < 3) nativeFallback("rAF loop not ticking");
      }, 700);
    } catch (e) {
      nativeFallback("init failed: " + e);
    }
  }

  /* ---- headings reveal per character ---- */
  function revealHeading(el) {
    if (!el || reduced() || !has("gsap") || !has("SplitText")) return;
    if (el.dataset.split) return;
    el.dataset.split = "1";
    try {
      const s = new SplitText(el, { type: "chars" });
      gsap.from(s.chars, {
        yPercent: 110, opacity: 0, duration: 0.62, ease: EASE,
        stagger: 0.016,
        onComplete: () => { try { s.revert(); } catch (e) { } },
      });
    } catch (e) { /* heading simply doesn't animate */ }
  }

  /* ---- numbers compute, then lock ----
     Scrambles through glyphs and resolves to the real value. Only for values
     the engine actually produced — never used to fake a computation. */
  function scramble(el, finalText, opts) {
    if (!el) return;
    const text = String(finalText);
    if (reduced() || !has("gsap") || !has("ScrambleTextPlugin")) {
      el.textContent = text;
      return;
    }
    try {
      gsap.to(el, {
        duration: (opts && opts.duration) || 0.85,
        ease: "none",
        scrambleText: {
          text, chars: "0123456789", speed: 0.5, revealDelay: 0.12,
        },
      });
    } catch (e) { el.textContent = text; }
  }

  /* ---- section entrance ---- */
  function reveal(nodes, opts) {
    const list = [...nodes].filter(Boolean);
    if (!list.length) return;
    if (reduced() || !has("gsap")) {
      list.forEach((n) => { n.style.opacity = ""; n.style.transform = ""; });
      return;
    }
    const o = opts || {};
    gsap.fromTo(list,
      { y: o.y != null ? o.y : 30, scale: 0.98, opacity: 0 },
      {
        y: 0, scale: 1, opacity: 1, duration: 0.9, ease: o.ease || ENTER,
        stagger: o.stagger != null ? o.stagger : 0.08,
        overwrite: "auto",
        // hard guarantee: whatever happens, end visible
        onComplete: () => list.forEach((n) => {
          n.style.opacity = ""; n.style.transform = "";
        }),
      }
    );
  }

  /* ---- scroll-linked reveal for below-the-fold blocks ----
     Anything parked in a hidden from-state is tracked, so the safety sweep can
     rescue it if its trigger never fires (mis-measured layout, resize, a view
     swap mid-flight). Content visibility is never left to chance. */
  const _parked = new Set();

  function show(n) {
    _parked.delete(n);
    if (has("gsap")) gsap.set(n, { clearProps: "opacity,transform,y" });
    n.style.opacity = ""; n.style.transform = "";
  }

  function onScrollReveal(nodes) {
    const list = [...nodes].filter(Boolean);
    if (!list.length || reduced() || !has("gsap") || !has("ScrollTrigger")) {
      list.forEach(show);
      return;
    }
    list.forEach((n, i) => {
      _parked.add(n);
      gsap.fromTo(n, { y: 22, opacity: 0 }, {
        y: 0, opacity: 1, duration: 0.7, ease: EASE, delay: (i % 4) * 0.05,
        scrollTrigger: { trigger: n, start: "top 96%", once: true },
        onComplete: () => show(n),
      });
    });
    sweep();
  }

  /* Safety sweep: anything parked that is actually on screen gets revealed.
     Runs for a few seconds after each render and on every scroll. */
  let _sweepTimer = null;
  function sweepOnce() {
    if (!_parked.size) return;
    [..._parked].forEach((n) => {
      if (!n.isConnected) { _parked.delete(n); return; }
      const r = n.getBoundingClientRect();
      if (r.top < innerHeight * 1.05 && r.bottom > -50) show(n);
    });
  }
  function sweep() {
    clearInterval(_sweepTimer);
    let ticks = 0;
    // let layout settle first — this is what the original bug tripped over
    requestAnimationFrame(() => requestAnimationFrame(sweepOnce));
    _sweepTimer = setInterval(() => {
      sweepOnce();
      if (++ticks > 12 || !_parked.size) clearInterval(_sweepTimer);
    }, 400);
  }
  addEventListener("scroll", sweepOnce, { passive: true });
  addEventListener("resize", sweepOnce, { passive: true });

  /* Re-measure. Critical: this app renders its content asynchronously, so at
     Lenis init the page is just a skeleton and it concludes there is nothing to
     scroll. Every render must tell it the page grew, or smooth scroll silently
     does nothing. */
  function refresh() {
    try { if (lenis && lenis.resize) lenis.resize(); } catch (e) { /* noop */ }
    if (has("ScrollTrigger")) ScrollTrigger.refresh();
  }

  /* Belt and braces: watch the content box and re-measure whenever it changes
     size, so late-arriving data (images, API responses) can't strand us. */
  let _rzTimer = null;
  function watchSize() {
    if (!("ResizeObserver" in window)) return;
    const target = document.getElementById("view") || document.body;
    try {
      new ResizeObserver(() => {
        clearTimeout(_rzTimer);
        _rzTimer = setTimeout(refresh, 120);
      }).observe(target);
    } catch (e) { /* noop */ }
  }
  if (document.readyState === "loading") {
    addEventListener("DOMContentLoaded", watchSize);
  } else {
    watchSize();
  }

  /* ---- magnetic hover on primary actions ---- */
  function magnetic(el) {
    if (!el || reduced() || !has("gsap")) return;
    if (el.dataset.mag) return;
    el.dataset.mag = "1";
    const strength = 0.28;
    el.addEventListener("mousemove", (e) => {
      const r = el.getBoundingClientRect();
      gsap.to(el, {
        x: (e.clientX - (r.left + r.width / 2)) * strength,
        y: (e.clientY - (r.top + r.height / 2)) * strength,
        duration: 0.45, ease: EASE,
      });
    });
    el.addEventListener("mouseleave", () => {
      gsap.to(el, { x: 0, y: 0, duration: 0.55, ease: "elastic.out(1,0.5)" });
    });
  }

  initScroll();
  return { reveal, onScrollReveal, revealHeading, scramble, magnetic, refresh,
           show, sweep: sweepOnce, EASE,
           get lenis() { return lenis; } };
})();
