// Summernote (lite, MIT) mounted lazily on `textarea[data-mf-editor]`.
// jQuery is bundled into this chunk only (never exposed globally). The editor is
// configured entirely from the server-rendered profile in `data-mf-editor-config`,
// keeps the textarea as the source of truth for form submission and falls back to the
// plain textarea if anything here fails.
import $ from "jquery";
import "summernote/dist/summernote-lite.js";
import "summernote/dist/summernote-lite.css";
import "../styles/editor-content.css";
import "../styles/editor.css";

import { normalizeHtml, visibleLength } from "./editor-normalize.js";
import { guardEmptyInlineOnEnter } from "./editor-guards.js";
import { attachPasteHandler, imageAlt } from "./editor-paste.js";
import { CUSTOM_BUTTONS, STYLE_TAGS, buildToolbar, popoverOptions } from "./editor-toolbar.js";

const READY = "mfEditorReady";
const CORE_MODULES = [
  "editor",
  "clipboard",
  "codeview",
  "statusbar",
  "fullscreen",
  "autoSync",
  "placeholder",
  "buttons",
  "toolbar",
];

// Only instantiate the modules the profile needs. Summernote's help dialog (and a few
// others) render inline `style` attributes at start-up, which the strict public CSP
// refuses; skipping unused modules also keeps each editor's DOM small.
function modulesFor(featureList, config) {
  const features = new Set(featureList);
  const keep = new Set(CORE_MODULES);
  if (features.has("links")) ["linkDialog", "linkPopover", "autoLink"].forEach((name) => keep.add(name));
  if (features.has("images")) {
    ["imageDialog", "imagePopover", "handle"].forEach((name) => keep.add(name));
    if (config.upload && config.upload.enabled) keep.add("dropzone");
  }
  if (features.has("tables")) keep.add("tablePopover");
  if (features.has("video")) keep.add("videoDialog");
  return Object.fromEntries(Object.entries($.summernote.options.modules).filter(([name]) => keep.has(name)));
}

function csrfToken(textarea) {
  const field = textarea.form && textarea.form.querySelector("input[name=csrfmiddlewaretoken]");
  if (field && field.value) return field.value;
  const match = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
  return match ? decodeURIComponent(match[1]) : "";
}

function labelElement(textarea) {
  return textarea.id ? document.querySelector(`label[for="${CSS.escape(textarea.id)}"]`) : null;
}

function labelFor(textarea) {
  const label = labelElement(textarea);
  return label ? label.textContent.trim() : textarea.getAttribute("aria-label") || "Rich text";
}

function createStatus(textarea) {
  const status = document.createElement("div");
  status.className = "mf-editor-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const message = document.createElement("span");
  message.className = "mf-editor-message";
  const counter = document.createElement("span");
  counter.className = "mf-editor-count";
  status.append(message, counter);
  return { status, message, counter };
}

async function uploadImage(file, config, textarea) {
  const body = new FormData();
  body.append("file", file);
  body.append("context", config.profile);
  const response = await fetch(config.upload.url, {
    method: "POST",
    body,
    credentials: "same-origin",
    headers: { "X-CSRFToken": csrfToken(textarea), Accept: "application/json" },
  });
  let data = {};
  try {
    data = await response.json();
  } catch (error) {
    data = {};
  }
  if (!response.ok) throw new Error(data.error || "The image could not be uploaded.");
  return data;
}

// Summernote treats <figure> as an inline node and may nest it in the caret's <p>, which
// is invalid HTML (parsers close the paragraph and strand the text after the figure).
// Lift the figure out so the markup is a figure between two paragraphs, and leave the
// caret in the paragraph after it.
function liftFigure(figure) {
  const paragraph = figure.parentElement;
  if (!paragraph || paragraph.tagName !== "P") return;
  const after = paragraph.cloneNode(false);
  while (figure.nextSibling) after.append(figure.nextSibling);
  paragraph.after(figure);
  figure.after(after);
  if (!after.textContent && !after.querySelector("img,br")) after.append(document.createElement("br"));
  if (!paragraph.textContent.trim() && !paragraph.querySelector("img,br")) paragraph.remove();
  const range = document.createRange();
  range.setStart(after, 0);
  range.collapse(true);
  const selection = document.getSelection();
  if (selection) {
    selection.removeAllRanges();
    selection.addRange(range);
  }
}

// The markup of an uploaded image: responsive by CSS (max-width/height:auto), with its
// intrinsic size so the layout does not jump, an alt text taken from a meaningful file
// name (empty for "image.png") and an empty caption the author may fill in.
function figureFor(data, file) {
  const figure = document.createElement("figure");
  const image = document.createElement("img");
  image.setAttribute("src", data.url);
  image.setAttribute("alt", imageAlt(file.name));
  if (Number.isInteger(data.width) && data.width > 0) image.setAttribute("width", String(data.width));
  if (Number.isInteger(data.height) && data.height > 0) image.setAttribute("height", String(data.height));
  figure.append(image, document.createElement("figcaption"));
  return figure;
}

function enhanceAccessibility(textarea, $editor, label) {
  const root = $editor[0];
  const toolbar = root.querySelector(".note-toolbar");
  if (toolbar) toolbar.setAttribute("aria-label", `${label} formatting`);
  const editable = root.querySelector(".note-editable");
  if (editable) {
    // The visible label now names the editing area (the hidden textarea keeps its id
    // but is no longer the label's target) and a click on it focuses the editor.
    const labelEl = labelElement(textarea);
    if (labelEl) {
      if (!labelEl.id) labelEl.id = `${textarea.id}-label`;
      editable.setAttribute("aria-labelledby", labelEl.id);
      labelEl.removeAttribute("for");
      labelEl.addEventListener("click", () => editable.focus());
    } else {
      editable.setAttribute("aria-label", label);
    }
    editable.setAttribute("dir", "auto");
    if (textarea.getAttribute("aria-required") === "true") editable.setAttribute("aria-required", "true");
  }
  root.querySelectorAll("button.note-btn").forEach((control) => {
    if (!control.getAttribute("aria-label")) {
      const title = control.getAttribute("title") || control.getAttribute("data-original-title");
      if (title) control.setAttribute("aria-label", title);
    }
    control.setAttribute("tabindex", "0");
  });
}

function mountOne(textarea) {
  if (textarea.dataset[READY]) return;
  textarea.dataset[READY] = "1";
  let config;
  try {
    config = JSON.parse(textarea.dataset.mfEditorConfig || "{}");
  } catch (error) {
    return;
  }
  const label = labelFor(textarea);
  const required = textarea.required;
  if (required) {
    textarea.required = false;
    textarea.setAttribute("aria-required", "true");
  }
  const { status, message, counter } = createStatus(textarea);
  const $note = $(textarea);
  const say = (text, isError) => {
    message.textContent = text || "";
    status.classList.toggle("is-error", Boolean(isError));
  };

  const options = {
    height: config.height || 280,
    minHeight: 120,
    placeholder: textarea.getAttribute("placeholder") || config.placeholder || "",
    spellCheck: config.spellcheck !== false,
    lang: "en-US",
    dialogsInBody: true,
    container: "body",
    dialogsFade: false,
    disableDragAndDrop: !(config.upload && config.upload.enabled),
    tabDisable: false,
    codeviewFilter: true,
    codeviewIframeFilter: true,
    linkTargetBlank: false,
    useProtocol: true,
    defaultProtocol: "https://",
    tableClassName: "mf-table",
    styleTags: STYLE_TAGS,
    toolbar: buildToolbar(config.features || [], config),
    modules: modulesFor(config.features || [], config),
    popover: popoverOptions(config.features || []),
    buttons: Object.fromEntries(Object.entries(CUSTOM_BUTTONS).map(([name, factory]) => [name, (context) => factory(context)])),
    callbacks: {
      onImageUpload(files) {
        // Toolbar upload and drag-and-drop: one at a time, in order.
        Array.from(files).reduce((queue, file) => queue.then(() => addImage(file)), Promise.resolve());
      },
      onChange() {
        sync();
      },
    },
  };

  // Upload one image through the editor endpoint and insert it as a figure. Never
  // throws: every failure is reported in the editor's own status line.
  async function addImage(file) {
    if (!config.upload || !config.upload.enabled) {
      say("Image upload is not available here.", true);
      return;
    }
    if (file.size > config.upload.maxKb * 1024) {
      say(`Image is larger than ${config.upload.maxKb} KiB.`, true);
      return;
    }
    say("Uploading image…", false);
    try {
      const data = await uploadImage(file, config, textarea);
      const figure = figureFor(data, file);
      $note.summernote("insertNode", figure);
      liftFigure(figure);
      sync();
      say("Image added. Add a caption for readers who cannot see it.", false);
    } catch (error) {
      say(error.message, true);
    }
  }

  const current = () => normalizeHtml($note.summernote("code"), config);
  const update = (html) => {
    const length = visibleLength(html);
    counter.textContent = config.maxLength ? `${length} / ${config.maxLength}` : `${length}`;
    status.classList.toggle("is-over", Boolean(config.maxLength) && length > config.maxLength);
    return length;
  };
  function sync() {
    const html = current();
    textarea.value = html;
    update(html);
  }

  $note.summernote(options);
  const $editor = $note.next(".note-editor");
  $editor.after(status);
  enhanceAccessibility(textarea, $editor, label);
  guardEmptyInlineOnEnter($editor[0]);
  const editable = $editor[0].querySelector(".note-editable");
  if (editable) {
    attachPasteHandler(editable, {
      config,
      say,
      addImage,
      insertHtml: (html) => {
        // Native insertion replaces a selection exactly like an ordinary paste.
        if (!document.execCommand("insertHTML", false, html)) $note.summernote("pasteHTML", html);
      },
      insertText: (text) => {
        if (!document.execCommand("insertText", false, text)) $note.summernote("insertText", text);
      },
    });
  }
  update(normalizeHtml(textarea.value, config));

  const form = textarea.form;
  if (form) {
    form.addEventListener(
      "submit",
      (event) => {
        const html = current();
        textarea.value = html;
        const length = update(html);
        const tooLong = config.maxLength && length > config.maxLength;
        const missing = required && !html;
        if (tooLong || missing) {
          event.preventDefault();
          event.stopImmediatePropagation();
          say(tooLong ? "The text is too long." : "This field is required.", true);
          $note.summernote("focus");
        }
      },
      true,
    );
    form.addEventListener("formdata", (event) => {
      if (textarea.name) event.formData.set(textarea.name, current());
    });
    form.addEventListener("reset", () => {
      window.setTimeout(() => $note.summernote("code", textarea.value), 0);
    });
  }
}

export function mountEditors(textareas) {
  textareas.forEach((textarea) => {
    try {
      mountOne(textarea);
    } catch (error) {
      textarea.dataset[READY] = "failed";
      console.error("Rich text editor failed to start", error);
    }
  });
}
