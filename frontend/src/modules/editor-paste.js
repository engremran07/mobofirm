// Paste handling for the rich-text editor. A paste that carries images, or markup with
// inline styles, is taken over instead of letting the browser drop it into the editing
// area as-is (a data: image with its own width and inline style, which the strict CSP
// cannot apply and which the server later discards):
//   * image files and data: images are uploaded through the editor's upload endpoint
//     and inserted as <figure><img><figcaption></figcaption></figure>,
//   * images hosted elsewhere cannot be embedded (the site only serves its own images),
//     so the user gets a clear inline message instead of a silently vanishing image,
//   * the remaining markup is reduced to the editor's vocabulary before insertion.
// Everything is parsed in an inert DOMParser document; nothing is executed or fetched.
import { normalizeHtml } from "./editor-normalize.js";

const IMAGE_TYPE = /^image\/(?:png|jpe?g|gif|webp)$/i;
const DATA_IMAGE = /^data:(image\/(?:png|jpe?g|gif|webp));base64,([A-Za-z0-9+/=\s]+)$/i;
const EXTENSIONS = { "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/gif": "gif", "image/webp": "webp" };
const GENERIC_NAME = /^(?:image|img|photo|picture|pasted[ -]?image|screenshot|untitled|download|unnamed|blob)[ -]?\d*$/i;
export const TOO_LARGE = "too-large";

// Alt text from a file name; a generic name ("image.png") says nothing, so it stays
// empty (decorative) and the caption can describe the picture.
export function imageAlt(name) {
  const text = String(name || "")
    .replace(/\.[^.]+$/, "")
    .replace(/[-_]+/g, " ")
    .trim();
  return GENERIC_NAME.test(text) || /^\d+$/.test(text) ? "" : text.slice(0, 125);
}

// A File for a base64 data: image, TOO_LARGE when it would exceed maxBytes, or null.
export function fileFromDataUri(source, index, maxBytes) {
  const match = DATA_IMAGE.exec(String(source || ""));
  if (!match) return null;
  const encoded = match[2].replace(/\s+/g, "");
  if ((encoded.length * 3) / 4 > maxBytes) return TOO_LARGE;
  let binary;
  try {
    binary = atob(encoded);
  } catch (error) {
    return null;
  }
  const bytes = new Uint8Array(binary.length);
  for (let position = 0; position < binary.length; position += 1) bytes[position] = binary.charCodeAt(position);
  const type = match[1].toLowerCase();
  return new File([bytes], `pasted-image-${index + 1}.${EXTENSIONS[type]}`, { type });
}

function plural(count, one, many) {
  return count === 1 ? one : many;
}

// The strict CSP also applies to the inert parsing document, so a style="" attribute in
// pasted markup is reported as a violation (and could not be read anyway). The styles are
// dropped before parsing; colours, fonts and alignment come from the toolbar's classes.
const INLINE_STYLE = /\sstyle\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi;

function withoutInlineStyles(markup) {
  return markup.replace(INLINE_STYLE, "");
}

function pastedImages(clipboard, markup, config) {
  const files = Array.from(clipboard.files || []).filter((file) => IMAGE_TYPE.test(file.type));
  const doc = new DOMParser().parseFromString(`<body>${markup}</body>`, "text/html");
  const sources = Array.from(doc.body.querySelectorAll("img")).map((image) => image.getAttribute("src") || "");
  doc.body.querySelectorAll("img").forEach((image) => image.remove());
  const maxBytes = ((config.upload && config.upload.maxKb) || 0) * 1024;
  const embedded = [];
  let hosted = 0;
  // A copied picture usually arrives as a file and as <img src="https://…">: the file wins.
  if (!files.length) {
    sources.forEach((source, index) => {
      const file = fileFromDataUri(source, index, maxBytes);
      if (file) embedded.push(file);
      else hosted += 1;
    });
  }
  return { remaining: doc.body.innerHTML, files: files.length ? files : embedded, hosted };
}

// `hooks`: { config, say(text, isError), addImage(file) -> Promise, insertHtml(html), insertText(text) }
async function handlePaste(event, hooks) {
  const { config, say, addImage, insertHtml, insertText } = hooks;
  const clipboard = event.clipboardData;
  if (!clipboard) return;
  const original = clipboard.getData("text/html");
  const hasFiles = Array.from(clipboard.files || []).some((file) => IMAGE_TYPE.test(file.type));
  if (!hasFiles && !/<img[\s>]/i.test(original) && !/\sstyle\s*=/i.test(original)) return;

  event.preventDefault();
  event.stopImmediatePropagation();
  const markup = withoutInlineStyles(original);
  const { remaining, files, hosted } = pastedImages(clipboard, markup, config);
  const html = markup ? normalizeHtml(remaining, config) : "";
  const hasImages = files.length > 0 || hosted > 0;
  if (html) insertHtml(html);
  else if (!hasImages) {
    const text = clipboard.getData("text/plain");
    if (text) insertText(text);
  }
  if (!hasImages) return;

  const features = config.features || [];
  if (!features.includes("images")) {
    say("Images are not allowed in this field, so the pasted image was not added.", true);
    return;
  }
  if (!config.upload || !config.upload.enabled) {
    say("Image upload is not available here, so the pasted image was not added.", true);
    return;
  }
  let tooLarge = 0;
  for (const file of files) {
    if (file === TOO_LARGE) tooLarge += 1;
    else await addImage(file);
  }
  const problems = [];
  if (hosted) {
    problems.push(
      `${hosted} pasted ${plural(hosted, "image is", "images are")} hosted elsewhere and cannot be embedded. ` +
        "Save the image and add it with the image button.",
    );
  }
  if (tooLarge) problems.push(`${tooLarge} pasted ${plural(tooLarge, "image is", "images are")} too large.`);
  if (problems.length) say(problems.join(" "), true);
}

export function attachPasteHandler(editable, hooks) {
  // Capture phase on the editing area itself runs before Summernote's own paste handler.
  editable.addEventListener(
    "paste",
    (event) => {
      void handlePaste(event, hooks);
    },
    true,
  );
}
