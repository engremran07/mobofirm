/* Deterministic motion runtime. A pure function of time: window.__seek(ms) draws the frame at
   `ms` and nothing else (no timers, no CSS animations, no network, no randomness except a seeded
   generator). The renderer calls __seek for every frame and screenshots the stage.
   All text is applied with textContent; the plan is data, never markup. */
(() => {
  "use strict";
  const plan = JSON.parse(document.getElementById("plan").textContent);
  const { w: W, h: H, u: U, safe, brand } = plan;
  const stage = document.getElementById("stage");
  const NS = "http://www.w3.org/2000/svg";
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const ease = {
    outCubic: (x) => 1 - Math.pow(1 - x, 3),
    outExpo: (x) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x)),
    inCubic: (x) => x * x * x,
    inOut: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
    outBack: (x) => 1 + 2.70158 * Math.pow(x - 1, 3) + 1.70158 * Math.pow(x - 1, 2),
    spring: (x) => (x >= 1 ? 1 : 1 - Math.exp(-7 * x) * Math.cos(11 * x)),
  };
  const seg = (t, start, dur) => clamp((t - start) / dur);
  let seed = plan.seed >>> 0;
  const rand = () => {
    seed = (seed + 0x6d2b79f5) >>> 0;
    let r = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
  const el = (tag, cls, text, parent) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  };
  const sv = (tag, attrs, parent) => {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    if (parent) parent.appendChild(node);
    return node;
  };
  const px = (n) => `${(n * U).toFixed(2)}px`;
  const root = document.documentElement.style;
  root.setProperty("--u", `${U}px`);
  for (const [k, v] of Object.entries(brand.colors)) root.setProperty(`--${k}`, v);
  stage.style.width = `${W}px`;
  stage.style.height = `${H}px`;

  /* ---------------------------------------------------------------- background */
  const bg = el("div", "bg", null, stage);
  const grid = el("div", "grid", null, stage);
  const orbs = [0, 1, 2].map(() => el("div", "orb", null, stage));
  const dots = Array.from({ length: 22 }, () => {
    const node = el("div", "dot", null, stage);
    return { node, x: rand(), y: rand(), s: 0.4 + rand() * 1.2, v: 0.2 + rand() * 0.6 };
  });
  const vignette = el("div", "vignette", null, stage);

  function drawBackground(t) {
    const a = 160 + 25 * Math.sin(t / 3800);
    bg.style.background = `linear-gradient(${a}deg, var(--bg1), var(--bg2))`;
    grid.style.backgroundPosition = `${(-t * 0.012 * U).toFixed(1)}px ${(t * 0.02 * U).toFixed(1)}px`;
    orbs.forEach((orb, i) => {
      const size = (i === 0 ? 900 : 640) * U;
      const x = W * (0.2 + 0.6 * ((i * 0.37 + 0.5 + 0.35 * Math.sin(t / (4200 + i * 900) + i)) % 1)) - size / 2;
      const y = H * (0.18 + 0.62 * ((i * 0.31 + 0.5 + 0.3 * Math.cos(t / (5200 + i * 700) + i * 2)) % 1)) - size / 2;
      orb.style.width = orb.style.height = `${size}px`;
      orb.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      orb.style.background = `radial-gradient(circle, ${i === 1 ? "var(--accent2)" : "var(--accent)"} 0%, transparent 65%)`;
      orb.style.opacity = i === 0 ? 0.32 : 0.2;
    });
    dots.forEach((d) => {
      const y = (((d.y - (t / 1000) * d.v * 0.05) % 1) + 1) % 1;
      d.node.style.transform = `translate(${(d.x * W).toFixed(1)}px, ${(y * H).toFixed(1)}px) scale(${d.s})`;
      d.node.style.opacity = (0.15 + 0.35 * Math.abs(Math.sin(t / 1300 + d.x * 9))).toFixed(3);
    });
  }

  /* ----------------------------------------------------------------- chrome */
  const CAPTION_ZONE = 290 * U;
  const content = el("div", "content", null, stage);
  content.style.cssText = `left:${safe.l}px;top:${safe.t}px;right:${safe.r}px;bottom:${safe.b + CAPTION_ZONE}px;`;
  const progress = el("div", "progress", null, stage);
  progress.style.cssText = `left:${safe.l}px;right:${safe.r}px;top:${Math.max(8, safe.t - 36 * U)}px;`;
  const progressFill = el("div", "progress-fill", null, progress);
  const mark = el("div", "mark", brand.name, stage);
  mark.style.cssText = `left:${safe.l}px;top:${Math.max(8, safe.t - 36 * U) + 22 * U}px;`;
  mark.dataset.safe = "1";
  const capBox = el("div", "captions", null, stage);
  capBox.style.cssText = `left:${safe.l}px;right:${safe.r}px;bottom:${safe.b}px;`;
  capBox.dataset.safe = "1";
  const capLine = el("div", "caption", null, capBox);
  const capWords = plan.beats.map((beat) => {
    const words = beat.text.split(/\s+/).filter(Boolean);
    const total = words.reduce((n, word) => n + word.length + 1, 0) || 1;
    let at = 0;
    return words.map((text) => {
      const start = beat.start + ((beat.end - beat.start) * 0.9 * at) / total;
      at += text.length + 1;
      return { text, start };
    });
  });
  let capIndex = -1;
  const capNodes = [];
  function drawCaptions(t) {
    const i = plan.beats.findIndex((beat) => t >= beat.start && t < beat.end);
    if (i !== capIndex) {
      capIndex = i;
      capLine.textContent = "";
      capNodes.length = 0;
      if (i >= 0) capWords[i].forEach((word) => capNodes.push({ node: el("span", "cw", word.text, capLine), start: word.start }));
    }
    if (i < 0) {
      capBox.style.opacity = 0;
      return;
    }
    const beat = plan.beats[i];
    const pop = ease.outBack(seg(t, beat.start, 220));
    const fade = 1 - ease.inCubic(seg(t, beat.end - 140, 140));
    capBox.style.opacity = clamp(pop) * fade;
    capBox.style.transform = `translateY(${((1 - clamp(pop)) * 24 * U).toFixed(1)}px) scale(${(0.96 + 0.04 * clamp(pop)).toFixed(3)})`;
    capNodes.forEach(({ node, start }, k) => {
      const next = capNodes[k + 1] ? capNodes[k + 1].start : beat.end;
      const on = t >= start && t < next;
      node.classList.toggle("on", on);
      node.classList.toggle("done", t >= next);
    });
  }

  /* ----------------------------------------------------------------- helpers */
  function kinetic(text, cls, parent, delay0 = 0) {
    const line = el("div", `kinetic ${cls || ""}`, null, parent);
    const words = text.split(/\s+/).filter(Boolean).map((word, i) => ({
      node: el("span", "kw", word, line),
      delay: delay0 + i * 90,
    }));
    line.appendChild(document.createTextNode(""));
    return {
      node: line,
      update(local) {
        words.forEach(({ node, delay }) => {
          const p = ease.outBack(seg(local, delay, 520));
          const o = ease.outCubic(seg(local, delay, 320));
          node.style.opacity = o;
          node.style.transform = `translateY(${((1 - p) * 70 * U).toFixed(1)}px) rotate(${((1 - p) * 4).toFixed(2)}deg) scale(${(0.8 + 0.2 * p).toFixed(3)})`;
        });
      },
    };
  }
  function icon(kind, parent) {
    const s = sv("svg", { viewBox: "0 0 48 48", class: "icon" }, parent);
    const stroke = { fill: "none", stroke: "currentColor", "stroke-width": 3.2, "stroke-linecap": "round", "stroke-linejoin": "round" };
    const shapes = {
      usb: [["path", { d: "M8 24h28M36 24l-6-5M36 24l-6 5M20 24v-9h6M14 24v9", ...stroke }], ["circle", { cx: 8, cy: 24, r: 3, ...stroke }]],
      phone: [["rect", { x: 14, y: 5, width: 20, height: 38, rx: 4, ...stroke }], ["path", { d: "M22 38h4", ...stroke }]],
      bolt: [["path", { d: "M27 5 12 27h11l-3 16 16-23H25z", ...stroke }]],
      download: [["path", { d: "M24 6v26M13 22l11 11 11-11M10 41h28", ...stroke }]],
      check: [["path", { d: "M10 25l9 9 19-21", ...stroke }]],
      warn: [["path", { d: "M24 6 4 41h40zM24 19v10M24 35v.5", ...stroke }]],
      chip: [["rect", { x: 13, y: 13, width: 22, height: 22, rx: 3, ...stroke }], ["path", { d: "M19 5v8M29 5v8M19 35v8M29 35v8M5 19h8M5 29h8M35 19h8M35 29h8", ...stroke }]],
      power: [["path", { d: "M24 6v16M14 13a15 15 0 1 0 20 0", ...stroke }]],
      clock: [["circle", { cx: 24, cy: 24, r: 17, ...stroke }], ["path", { d: "M24 14v10l7 5", ...stroke }]],
    };
    (shapes[kind] || shapes.bolt).forEach(([tag, attrs]) => sv(tag, attrs, s));
    return s;
  }
  function badge(text, parent, cls) {
    return el("span", `badge ${cls || ""}`, text, parent);
  }
  const at = (d, i, fallback) => (d.at && d.at[i] !== undefined ? d.at[i] : fallback);
  function count(target, decimals, p) {
    const v = target * ease.outExpo(p);
    return decimals ? v.toFixed(decimals) : String(Math.round(v));
  }

  /* ------------------------------------------------------------------ scenes */
  const builders = {
    hook(d, box) {
      el("div", "kicker", d.kicker, box);
      const head = kinetic(d.headline, "headline", box, 120);
      const sub = d.sub ? el("div", "sub", d.sub, box) : null;
      const chips = el("div", "chips", null, box);
      (d.chips || []).forEach((c) => badge(c, chips));
      return (t) => {
        head.update(t);
        if (sub) {
          sub.style.opacity = ease.outCubic(seg(t, 700, 500));
          sub.style.transform = `translateY(${((1 - ease.outCubic(seg(t, 700, 500))) * 30 * U).toFixed(1)}px)`;
        }
        [...chips.children].forEach((chip, i) => {
          const p = ease.outBack(seg(t, 900 + i * 120, 450));
          chip.style.opacity = clamp(p);
          chip.style.transform = `scale(${clamp(p, 0, 1.2).toFixed(3)})`;
        });
      };
    },
    device(d, box) {
      const stageBox = el("div", "device-stage", null, box);
      const svg = sv("svg", { viewBox: "0 0 360 640", class: "device" }, stageBox);
      const defs = sv("defs", {}, svg);
      const grad = sv("linearGradient", { id: "scr", x1: 0, y1: 0, x2: 1, y2: 1 }, defs);
      sv("stop", { offset: "0%", "stop-color": brand.colors.accent }, grad);
      sv("stop", { offset: "100%", "stop-color": brand.colors.accent2 }, grad);
      const clip = sv("clipPath", { id: "scrclip" }, defs);
      sv("rect", { x: 26, y: 40, width: 308, height: 560, rx: 30 }, clip);
      const body = sv("g", {}, svg);
      sv("rect", { x: 8, y: 8, width: 344, height: 624, rx: 52, fill: "#0b1020", stroke: "rgba(255,255,255,.28)", "stroke-width": 3 }, body);
      const screen = sv("g", { "clip-path": "url(#scrclip)" }, body);
      sv("rect", { x: 26, y: 40, width: 308, height: 560, fill: "url(#scr)" }, screen);
      let art = null;
      if (d.image) art = sv("image", { href: d.image, x: 26, y: 40, width: 308, height: 560, preserveAspectRatio: "xMidYMid slice" }, screen);
      else {
        const letter = sv("text", { x: 180, y: 300, "text-anchor": "middle", "font-size": 190, "font-weight": 800, fill: "rgba(255,255,255,.92)" }, screen);
        letter.textContent = (d.initial || "?").slice(0, 2);
        const model = sv("text", { x: 180, y: 380, "text-anchor": "middle", "font-size": 30, "font-weight": 700, fill: "rgba(255,255,255,.85)" }, screen);
        model.textContent = (d.model || "").slice(0, 22);
        sv("path", { d: "M26 470 C 120 430, 220 520, 334 470 L334 600 L26 600Z", fill: "rgba(255,255,255,.14)" }, screen);
      }
      sv("rect", { x: 140, y: 18, width: 80, height: 18, rx: 9, fill: "#05070f" }, body);
      const chips = el("div", "orbit", null, stageBox);
      const chipNodes = (d.chips || []).map((c) => badge(c, chips, "float"));
      const name = el("div", "device-name", d.name, box);
      const lower = el("div", "lower-third", null, box);
      el("span", "lt-bar", null, lower);
      el("span", "lt-text", d.lower || "", lower);
      if (d.verified) badge("VERIFIED", lower, "ok");
      return (t, local, dur) => {
        const kb = ease.inOut(clamp(local / dur));
        const enter = ease.outExpo(seg(t, 0, 800));
        stageBox.style.transform = `translateY(${((1 - enter) * 120 * U).toFixed(1)}px) scale(${(0.9 + 0.1 * enter - 0.03 * kb).toFixed(4)}) rotate(${(Math.sin(t / 1700) * 1.2).toFixed(2)}deg)`;
        if (art) art.setAttribute("transform", `translate(${(-14 * kb).toFixed(2)} ${(-10 * kb).toFixed(2)}) scale(${(1.04 + 0.1 * kb).toFixed(4)})`);
        chipNodes.forEach((chip, i) => {
          const p = ease.outBack(seg(t, 500 + i * 150, 500));
          const side = i % 2 ? 1 : -1;
          const bob = Math.sin(t / 700 + i * 1.7) * 14;
          chip.style.opacity = clamp(p);
          chip.style.top = `${22 + i * 26}%`;
          chip.style.transform = `translate(${(side * 235 * U).toFixed(1)}px, ${(bob * U).toFixed(1)}px) scale(${clamp(p, 0, 1.15).toFixed(3)})`;
        });
        name.style.opacity = ease.outCubic(seg(t, 350, 450));
        name.style.transform = `translateY(${((1 - ease.outCubic(seg(t, 350, 450))) * 30 * U).toFixed(1)}px)`;
        const slide = ease.outExpo(seg(t, 700, 700));
        lower.style.opacity = clamp(slide);
        lower.style.transform = `translateX(${((1 - slide) * -300 * U).toFixed(1)}px)`;
      };
    },
    facts(d, box) {
      el("div", "title", d.title, box);
      const gridBox = el("div", "stats", null, box);
      const cards = (d.stats || []).map((s) => {
        const card = el("div", "stat", null, gridBox);
        el("div", "stat-label", s.label, card);
        const row = el("div", "stat-row", null, card);
        const value = el("span", "stat-value", "", row);
        if (s.unit) el("span", "stat-unit", s.unit, row);
        return { card, value, s };
      });
      const stamp = d.badge ? badge(d.badge, box, "ok big") : null;
      return (t) => {
        cards.forEach(({ card, value, s }, i) => {
          const p = ease.outBack(seg(t, 200 + i * 140, 520));
          card.style.opacity = clamp(p);
          card.style.transform = `translateY(${((1 - p) * 60 * U).toFixed(1)}px) scale(${(0.9 + 0.1 * clamp(p, 0, 1.1)).toFixed(3)})`;
          const c = seg(t, 300 + i * 140, 900);
          const shown = s.num !== undefined && s.num !== null ? count(s.num, s.decimals || 0, c) : s.value;
          value.textContent = shown;
          value.classList.toggle("small", String(shown).length > 9);
        });
        if (stamp) {
          const p = ease.spring(seg(t, 900, 700));
          stamp.style.opacity = clamp(p * 2);
          stamp.style.transform = `scale(${(0.4 + 0.6 * p).toFixed(3)}) rotate(${((1 - p) * -12).toFixed(1)}deg)`;
        }
      };
    },
    points(d, box) {
      el("div", "title", d.title, box);
      const list = el("div", "points", null, box);
      const rows = (d.items || []).map((text) => {
        const row = el("div", "point", null, list);
        const mark = el("span", "tick", null, row);
        icon("check", mark);
        el("span", "point-text", text, row);
        return row;
      });
      return (t) =>
        rows.forEach((row, i) => {
          const p = ease.outExpo(seg(t, at(d, i, 250 + i * 260), 600));
          row.style.opacity = p;
          row.style.transform = `translateX(${((1 - p) * 140 * U).toFixed(1)}px)`;
        });
    },
    steps(d, box) {
      el("div", "title", d.title, box);
      const meta = el("div", "chips", null, box);
      [d.tool, d.chipset].filter(Boolean).forEach((m) => badge(m, meta));
      const list = el("div", "steps", null, box);
      const rows = (d.steps || []).map((s, i) => {
        const row = el("div", "step", null, list);
        el("span", "step-n", String(i + 1), row);
        const ic = el("span", "step-icon", null, row);
        icon(s.icon, ic);
        el("span", "step-text", s.text, row);
        return { row, ic };
      });
      return (t) =>
        rows.forEach(({ row, ic }, i) => {
          const p = ease.outBack(seg(t, at(d, i, 250 + i * 380), 560));
          row.style.opacity = clamp(p);
          row.style.transform = `translateY(${((1 - p) * 70 * U).toFixed(1)}px)`;
          ic.style.transform = `scale(${(1 + 0.12 * Math.sin(t / 380 + i)).toFixed(3)})`;
        });
    },
    warning(d, box) {
      const card = el("div", "warn", null, box);
      const ic = el("div", "warn-icon", null, card);
      icon("warn", ic);
      el("div", "warn-title", d.title, card);
      el("div", "warn-text", d.text, card);
      return (t) => {
        const p = ease.spring(seg(t, 150, 800));
        card.style.opacity = clamp(p * 2);
        card.style.transform = `scale(${(0.7 + 0.3 * p).toFixed(3)})`;
        ic.style.transform = `scale(${(1 + 0.1 * Math.sin(t / 220)).toFixed(3)})`;
      };
    },
    chart(d, box) {
      el("div", "title", d.title, box);
      const chart = el("div", "chart", null, box);
      const max = Math.max(1, ...(d.bars || []).map((b) => b.value));
      const bars = (d.bars || []).map((b) => {
        const row = el("div", "bar-row", null, chart);
        el("span", "bar-label", b.label, row);
        const track = el("span", "bar-track", null, row);
        const fill = el("span", "bar-fill", null, track);
        const num = el("span", "bar-num", "0", row);
        return { b, fill, num };
      });
      return (t) =>
        bars.forEach(({ b, fill, num }, i) => {
          const p = ease.outExpo(seg(t, 250 + i * 160, 900));
          fill.style.width = `${((b.value / max) * 100 * p).toFixed(2)}%`;
          num.textContent = String(Math.round(b.value * p));
        });
    },
    list(d, box) {
      el("div", "title", d.title, box);
      const list = el("div", "points", null, box);
      const rows = (d.items || []).map((it, i) => {
        const row = el("div", "point", null, list);
        el("span", "step-n", String(i + 1), row);
        const text = el("span", "point-text", null, row);
        el("strong", null, it.title, text);
        if (it.sub) el("small", null, it.sub, text);
        return row;
      });
      return (t) =>
        rows.forEach((row, i) => {
          const p = ease.outExpo(seg(t, at(d, i, 200 + i * 220), 600));
          row.style.opacity = p;
          row.style.transform = `translateX(${((1 - p) * 120 * U).toFixed(1)}px)`;
        });
    },
    cta(d, box) {
      const head = kinetic(d.headline, "headline", box, 80);
      const qrBox = el("div", "qr", null, box);
      if (d.qr) {
        const q = sv("svg", { viewBox: `-2 -2 ${d.qr.size + 4} ${d.qr.size + 4}`, class: "qr-svg", "shape-rendering": "crispEdges" }, qrBox);
        sv("rect", { x: -2, y: -2, width: d.qr.size + 4, height: d.qr.size + 4, fill: "#fff", rx: 1.2 }, q);
        sv("path", { d: d.qr.d, fill: "#0b1020" }, q);
      }
      const btn = el("div", "button", d.button, box);
      el("div", "site", d.site, box);
      return (t) => {
        head.update(t);
        const p = ease.spring(seg(t, 500, 800));
        qrBox.style.opacity = clamp(p * 2);
        qrBox.style.transform = `scale(${(0.6 + 0.4 * p).toFixed(3)})`;
        const pulse = 1 + 0.045 * Math.sin(t / 260);
        btn.style.transform = `scale(${pulse.toFixed(4)})`;
        btn.style.opacity = ease.outCubic(seg(t, 700, 400));
      };
    },
  };

  const scenes = plan.scenes.map((scene) => {
    const box = el("div", `scene scene-${scene.kind}`, null, content);
    box.dataset.safe = "1";
    const update = builders[scene.kind](scene.data, box);
    // Scale a scene down when its natural layout is bigger than the safe area (square and
    // landscape frames, long titles) so nothing ever crosses the platform UI zones.
    box.style.visibility = "hidden";
    const rects = [...box.children].map((child) => child.getBoundingClientRect());
    const height = Math.max(...rects.map((r) => r.bottom)) - Math.min(...rects.map((r) => r.top));
    const width = Math.max(...rects.map((r) => r.right)) - Math.min(...rects.map((r) => r.left));
    const fit = rects.length ? Math.min(1, (box.clientHeight - 8) / height, (box.clientWidth - 8) / width) : 1;
    box.style.visibility = "";
    box.style.display = "none";
    return { scene, box, update, fit };
  });

  const ENTER = 520;
  const EXIT = 340;
  function drawScenes(t) {
    for (const { scene, box, update, fit } of scenes) {
      const local = t - scene.start;
      if (local < -10 || t > scene.end + 10) {
        box.style.display = "none";
        continue;
      }
      box.style.display = "";
      const dur = scene.end - scene.start;
      const inP = ease.outExpo(seg(local, 0, ENTER));
      const outP = ease.inCubic(seg(local, dur - EXIT, EXIT));
      box.style.opacity = (clamp(inP * 1.4) * (1 - outP)).toFixed(3);
      box.style.transform = `translate(${(-outP * 90 * U).toFixed(1)}px, ${((1 - inP) * 70 * U).toFixed(1)}px) scale(${((0.96 + 0.04 * inP - 0.03 * outP) * fit).toFixed(4)})`;
      update(local, local, dur);
    }
  }

  window.__seek = (ms) => {
    const t = Math.max(0, Math.min(ms, plan.total));
    drawBackground(t);
    drawScenes(t);
    drawCaptions(t);
    progressFill.style.width = `${((t / plan.total) * 100).toFixed(3)}%`;
    mark.style.opacity = 0.75;
    vignette.style.opacity = 1;
    return t;
  };
  window.__plan = { total: plan.total, fps: plan.fps };
  window.__ready = true;
  window.__seek(0);
})();
