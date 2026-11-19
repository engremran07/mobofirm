// htmx 4 extensions used by the console. They register themselves on the global `htmx`
// (set by the core module, which htmx-setup.js imports first), and are only active when the
// script is present: no `hx-ext` attribute exists in htmx 4.
//   hx-head     merges <head> assets of a boosted full-page response (page-specific form media,
//               stylesheets) into the live document; the title is updated by htmx itself.
//   hx-preload  prefetches a boosted link on mousedown/touchstart and a link carrying
//               `hx-preload="mouseenter, focusin"` on hover/focus, so the click is instant.
import "htmx.org/dist/ext/hx-head.js";
import "htmx.org/dist/ext/hx-preload.js";
