// Editor content normalisation: turn whatever contenteditable produces into the
// markup the server accepts. The server (nh3) stays authoritative; this only keeps
// the editor's output inside the same vocabulary so nothing legitimate is lost:
//   - colours / alignment / fonts become the curated `mf-*` classes (no inline style,
//     which the strict CSP could not apply anyway),
//   - iframes are rewritten to the privacy-friendly players of allowed providers,
//   - foreign classes, inline styles and non-same-origin images are dropped.
// Parsing happens in an inert DOMParser document, so nothing here can execute.

export const PALETTE = {
  red: [214, 69, 69],
  orange: [224, 122, 31],
  amber: [201, 154, 6],
  green: [46, 158, 91],
  teal: [19, 138, 140],
  blue: [47, 111, 214],
  indigo: [91, 91, 214],
  purple: [142, 68, 173],
  pink: [214, 69, 143],
  gray: [107, 119, 133],
};

const ALIGN_MAP = {
  left: "start",
  start: "start",
  right: "end",
  end: "end",
  center: "center",
  justify: "justify",
};

const BLOCKS = "p,div,h1,h2,h3,h4,h5,h6,li,blockquote,td,th,pre";
const YOUTUBE_ID = /^[A-Za-z0-9_-]{6,20}$/;
const VIMEO_ID = /^\d{4,12}$/;
const KEEP_CLASS = /^mf-(?:fg|bg)-[a-z]+$|^mf-(?:font|size)-[a-z]+$|^mf-align-[a-z]+$|^mf-table$|^mf-video$/;

function parseColor(value) {
  const text = String(value || "").trim().toLowerCase();
  let match = /^#([0-9a-f]{6})$/.exec(text);
  if (match) {
    const n = parseInt(match[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  match = /^#([0-9a-f]{3})$/.exec(text);
  if (match) {
    return match[1].split("").map((c) => parseInt(c + c, 16));
  }
  match = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/.exec(text);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

export function nearestColor(value) {
  const rgb = parseColor(value);
  if (!rgb) return null;
  let best = null;
  let bestDistance = Infinity;
  for (const [name, ref] of Object.entries(PALETTE)) {
    const d = (rgb[0] - ref[0]) ** 2 + (rgb[1] - ref[1]) ** 2 + (rgb[2] - ref[2]) ** 2;
    if (d < bestDistance) {
      best = name;
      bestDistance = d;
    }
  }
  return best;
}

export function videoEmbedUrl(raw, providers) {
  let url;
  try {
    url = new URL(String(raw).trim().replace(/^\/\//, "https://"));
  } catch (error) {
    return null;
  }
  if (!/^https?:$/.test(url.protocol) || url.username || url.port) return null;
  const host = url.hostname.toLowerCase();
  const parts = url.pathname.split("/").filter(Boolean);
  if (providers.includes("youtube")) {
    const youtubeHosts = [
      "youtube.com",
      "www.youtube.com",
      "m.youtube.com",
      "youtu.be",
      "youtube-nocookie.com",
      "www.youtube-nocookie.com",
    ];
    if (youtubeHosts.includes(host)) {
      let id = "";
      if (host === "youtu.be") id = parts[0] || "";
      else if (["embed", "shorts", "live", "v"].includes(parts[0])) id = parts[1] || "";
      else if (parts[0] === "watch") id = url.searchParams.get("v") || "";
      return YOUTUBE_ID.test(id) ? `https://www.youtube-nocookie.com/embed/${id}` : null;
    }
  }
  if (providers.includes("vimeo") && ["vimeo.com", "www.vimeo.com", "player.vimeo.com"].includes(host)) {
    const id = parts[0] === "video" ? parts[1] : parts.find((part) => /^\d+$/.test(part));
    return VIMEO_ID.test(id || "") ? `https://player.vimeo.com/video/${id}?dnt=1` : null;
  }
  return null;
}

function addClass(element, name) {
  element.classList.add(name);
}

function replaceClassFamily(element, family, name) {
  Array.from(element.classList)
    .filter((cls) => cls.startsWith(family))
    .forEach((cls) => element.classList.remove(cls));
  if (name) element.classList.add(name);
}

function retag(doc, element, name) {
  const replacement = doc.createElement(name);
  Array.from(element.attributes).forEach((attribute) => replacement.setAttribute(attribute.name, attribute.value));
  while (element.firstChild) replacement.appendChild(element.firstChild);
  element.replaceWith(replacement);
}

function convertLegacy(doc, features) {
  doc.body.querySelectorAll("font").forEach((font) => {
    const span = doc.createElement("span");
    if (features.has("colors") && font.getAttribute("color")) {
      const name = nearestColor(font.getAttribute("color"));
      if (name) addClass(span, `mf-fg-${name}`);
    }
    while (font.firstChild) span.appendChild(font.firstChild);
    font.replaceWith(span);
  });
  doc.body.querySelectorAll("[align]").forEach((element) => {
    const mapped = ALIGN_MAP[String(element.getAttribute("align")).toLowerCase()];
    if (mapped && features.has("align")) addClass(element, `mf-align-${mapped}`);
    element.removeAttribute("align");
  });
}

function convertStyles(element, features) {
  const style = element.style;
  if (features.has("colors")) {
    const fg = nearestColor(style.color);
    if (fg) replaceClassFamily(element, "mf-fg-", `mf-fg-${fg}`);
    const bg = nearestColor(style.backgroundColor);
    if (bg) replaceClassFamily(element, "mf-bg-", `mf-bg-${bg}`);
  }
  if (features.has("align") && element.matches(BLOCKS)) {
    const mapped = ALIGN_MAP[String(style.textAlign).toLowerCase()];
    if (mapped) replaceClassFamily(element, "mf-align-", `mf-align-${mapped}`);
  }
}

function rewriteIframes(doc, features, providers) {
  doc.body.querySelectorAll("iframe").forEach((frame) => {
    const url =
      features.has("video") && providers.length ? videoEmbedUrl(frame.getAttribute("src"), providers) : null;
    if (!url) {
      frame.remove();
      return;
    }
    Array.from(frame.attributes).forEach((attribute) => frame.removeAttribute(attribute.name));
    frame.setAttribute("src", url);
    frame.setAttribute("class", "mf-video");
    frame.setAttribute("title", "Embedded video");
  });
}

const IMAGE_ATTRIBUTES = new Set(["src", "alt", "width", "height"]);
const MAX_DIMENSION = 9999;

function dimension(value) {
  const number = Number.parseInt(value, 10);
  return /^\d{1,4}$/.test(String(value || "").trim()) && number > 0 && number <= MAX_DIMENSION ? String(number) : "";
}

// An image keeps only what the server accepts (site-served src, alt and its intrinsic
// size, which reserves the layout space); a paragraph that holds nothing but an image
// becomes a <figure>, and an empty <figcaption> is kept clean for the caption field.
function tidyImages(doc, features) {
  doc.body.querySelectorAll("img").forEach((image) => {
    const src = image.getAttribute("src") || "";
    if (!features.has("images") || !src.startsWith("/") || src.startsWith("//")) {
      image.remove();
      return;
    }
    Array.from(image.attributes)
      .filter((attribute) => !IMAGE_ATTRIBUTES.has(attribute.name))
      .forEach((attribute) => image.removeAttribute(attribute.name));
    for (const name of ["width", "height"]) {
      const value = dimension(image.getAttribute(name));
      if (value) image.setAttribute(name, value);
      else image.removeAttribute(name);
    }
    if (!image.hasAttribute("alt")) image.setAttribute("alt", "");
  });
  doc.body.querySelectorAll("figcaption").forEach((caption) => {
    if (!caption.textContent.trim()) caption.replaceChildren();
  });
  doc.body.querySelectorAll("p").forEach((paragraph) => {
    const images = paragraph.querySelectorAll("img");
    const others = Array.from(paragraph.childNodes).filter(
      (node) => node.nodeName !== "IMG" && node.nodeName !== "BR" && node.textContent.trim() !== "",
    );
    if (images.length !== 1 || images[0].parentNode !== paragraph || others.length) return;
    const figure = doc.createElement("figure");
    figure.append(images[0], doc.createElement("figcaption"));
    paragraph.replaceWith(figure);
  });
  doc.body.querySelectorAll("figure").forEach((figure) => {
    if (!figure.querySelector("img")) figure.remove();
  });
}

function tidyAttributes(doc, features) {
  doc.body.querySelectorAll("*").forEach((element) => {
    const kept = Array.from(element.classList).filter((cls) => KEEP_CLASS.test(cls));
    if (kept.length) element.setAttribute("class", kept.join(" "));
    else element.removeAttribute("class");
    element.removeAttribute("style");
    Array.from(element.attributes)
      .filter((attribute) => /^(data-|on|contenteditable|spellcheck|dir$|lang$)/i.test(attribute.name))
      .forEach((attribute) => element.removeAttribute(attribute.name));
  });
  tidyImages(doc, features);
  // A paragraph with no children at all is what a parser leaves behind around a block
  // (e.g. a figure that was nested in a paragraph); it has no visible effect.
  doc.body.querySelectorAll("p").forEach((paragraph) => {
    if (!paragraph.hasChildNodes()) paragraph.remove();
  });
  doc.body.querySelectorAll("b,i,u,s,strong,em,span,sub,sup").forEach((element) => {
    if (!element.textContent && !element.querySelector("img,iframe,br,hr,table")) element.remove();
  });
  doc.body.querySelectorAll("table").forEach((table) => {
    table.setAttribute("class", "mf-table");
    table.removeAttribute("width");
    table.removeAttribute("border");
  });
}

export function normalizeHtml(html, config) {
  const features = new Set(config.features || []);
  const providers = config.videoProviders || [];
  const doc = new DOMParser().parseFromString(`<body>${html || ""}</body>`, "text/html");
  doc.body.querySelectorAll("script,style,link,meta,object,embed,form,svg,math").forEach((node) => node.remove());
  convertLegacy(doc, features);
  doc.body.querySelectorAll("h1").forEach((heading) => retag(doc, heading, "h2"));
  doc.body.querySelectorAll("h6").forEach((heading) => retag(doc, heading, "h5"));
  doc.body.querySelectorAll("[style]").forEach((element) => convertStyles(element, features));
  rewriteIframes(doc, features, providers);
  tidyAttributes(doc, features);
  const result = doc.body.innerHTML.trim();
  const text = doc.body.textContent.replace(/\u00a0/g, " ").trim();
  const hasMedia = doc.body.querySelector("img,iframe,hr,table");
  return text || hasMedia ? result : "";
}

export function visibleLength(html) {
  const doc = new DOMParser().parseFromString(`<body>${html || ""}</body>`, "text/html");
  doc.body.querySelectorAll("script,style").forEach((node) => node.remove());
  return doc.body.textContent.replace(/\u00a0/g, " ").trim().length;
}
