// Toolbar construction and the custom buttons (fonts, colours, alignment, emoji).
// Summernote's own colour and font tools write inline `style` attributes, which the
// strict CSP cannot apply and the server removes, so these buttons apply curated
// `mf-*` classes instead. Everything is built from the profile's enabled features.
import $ from "jquery";

import { PALETTE } from "./editor-normalize.js";

const FONT_FAMILIES = [
  ["sans", "Sans"],
  ["serif", "Serif"],
  ["mono", "Monospace"],
];
const FONT_SIZES = [
  ["sm", "Small"],
  ["lg", "Large"],
];
const ALIGNMENTS = [
  ["start", "Align to start"],
  ["center", "Center"],
  ["end", "Align to end"],
  ["justify", "Justify"],
];
const EMOJI = "😀 😃 😄 😁 😆 😅 😂 🙂 😉 😊 😍 😘 😎 🤔 😐 😢 😭 😡 👍 👎 👏 🙏 💪 🔥 ✨ 🎉 ✅ ❌ ⚠️ 💡 📌 🔧 ⚙️ 📱 💻 🔋 📶 🔒 🔓".split(" ");
const BLOCK_SELECTOR = "p,div,h2,h3,h4,h5,li,blockquote,td,th,pre";
const SHORTCUTS = [
  ["Ctrl+B", "Bold"],
  ["Ctrl+I", "Italic"],
  ["Ctrl+U", "Underline"],
  ["Ctrl+Z", "Undo"],
  ["Ctrl+Y", "Redo"],
  ["Ctrl+K", "Insert link"],
  ["Ctrl+Shift+7", "Bulleted list"],
  ["Ctrl+Shift+8", "Numbered list"],
  ["Ctrl+Enter", "Horizontal rule"],
  ["Tab", "Indent"],
];

function button(options) {
  return $.summernote.ui.button(options);
}

function panel(className, markup) {
  return $.summernote.ui.dropdown({ className: `mf-ed-panel ${className}`, items: markup });
}

function dropdownButton({ context, label, text, panelClass, markup, onPick }) {
  const group = $.summernote.ui.buttonGroup([
    button({
      className: "dropdown-toggle mf-ed-toggle",
      contents: `<span class="mf-ed-glyph" aria-hidden="true">${text}</span>`,
      tooltip: label,
      container: context.options.container,
      data: { toggle: "dropdown" },
    }),
    panel(panelClass, markup),
  ]);
  const $group = group.render();
  $group.find(".mf-ed-panel").attr("role", "menu");
  $group.on("click", "[data-mf-value]", (event) => {
    event.preventDefault();
    onPick(event.currentTarget.getAttribute("data-mf-value"));
  });
  return $group;
}

function choice(group, value, label, extraClass = "") {
  return (
    `<button type="button" class="mf-ed-choice ${extraClass}" role="menuitem" ` +
    `data-mf-value="${group}:${value}" aria-label="${label}"><span>${label}</span></button>`
  );
}

function swatch(prefix, name, label) {
  return (
    `<button type="button" class="mf-ed-swatch mf-ed-sw-${name}" role="menuitem" ` +
    `data-mf-value="${prefix}:${name}" aria-label="${label}" title="${label}"></button>`
  );
}

function selectionRange(context) {
  context.invoke("editor.restoreRange");
  const selection = window.getSelection();
  return selection && selection.rangeCount ? selection.getRangeAt(0) : null;
}

function unwrapFamily(root, family) {
  root.querySelectorAll(`span[class*="${family}"]`).forEach((span) => {
    Array.from(span.classList)
      .filter((cls) => cls.startsWith(family))
      .forEach((cls) => span.classList.remove(cls));
    if (!span.classList.length) span.replaceWith(...span.childNodes);
  });
}

function applyInline(context, family, className) {
  context.invoke("editor.beforeCommand");
  const range = selectionRange(context);
  if (!range || range.collapsed) {
    context.invoke("editor.afterCommand");
    return;
  }
  const fragment = range.extractContents();
  unwrapFamily(fragment, family);
  let last = fragment;
  if (className) {
    const span = document.createElement("span");
    span.className = className;
    span.appendChild(fragment);
    last = span;
  }
  range.insertNode(last);
  const selection = window.getSelection();
  selection.removeAllRanges();
  const restored = document.createRange();
  restored.selectNodeContents(last.nodeType === 11 ? range.commonAncestorContainer : last);
  selection.addRange(restored);
  context.invoke("editor.afterCommand");
}

function alignBlocks(context, name) {
  context.invoke("editor.beforeCommand");
  const range = selectionRange(context);
  const editable = context.layoutInfo.editable[0];
  if (range) {
    const blocks = new Set();
    const start = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
    const closest = start && start.closest(BLOCK_SELECTOR);
    if (closest && editable.contains(closest)) blocks.add(closest);
    editable.querySelectorAll(BLOCK_SELECTOR).forEach((block) => {
      if (range.intersectsNode(block)) blocks.add(block);
    });
    blocks.forEach((block) => {
      Array.from(block.classList)
        .filter((cls) => cls.startsWith("mf-align-"))
        .forEach((cls) => block.classList.remove(cls));
      if (name !== "start") block.classList.add(`mf-align-${name}`);
    });
  }
  context.invoke("editor.afterCommand");
}

function fontButton(context) {
  const markup =
    FONT_FAMILIES.map(([key, label]) => choice("font", key, label, `mf-font-${key}`)).join("") +
    '<div class="mf-ed-sep" role="separator"></div>' +
    FONT_SIZES.map(([key, label]) => choice("size", key, label)).join("") +
    '<div class="mf-ed-sep" role="separator"></div>' +
    choice("reset", "font", "Default font and size");
  return dropdownButton({
    context,
    label: "Font and size",
    text: "Aa",
    panelClass: "mf-ed-fonts",
    markup,
    onPick(value) {
      const [kind, key] = value.split(":");
      if (kind === "font") applyInline(context, "mf-font-", `mf-font-${key}`);
      else if (kind === "size") applyInline(context, "mf-size-", `mf-size-${key}`);
      else {
        applyInline(context, "mf-font-", "");
        applyInline(context, "mf-size-", "");
      }
    },
  });
}

function colorButton(context) {
  const names = Object.keys(PALETTE);
  const markup =
    '<div class="mf-ed-label">Text colour</div><div class="mf-ed-swatches">' +
    names.map((name) => swatch("fg", name, `Text ${name}`)).join("") +
    "</div>" +
    '<div class="mf-ed-label">Highlight</div><div class="mf-ed-swatches">' +
    names.map((name) => swatch("bg", name, `Highlight ${name}`)).join("") +
    "</div>" +
    choice("reset", "color", "Remove colours");
  return dropdownButton({
    context,
    label: "Colours",
    text: '<span class="mf-ed-colorA">A</span>',
    panelClass: "mf-ed-colors",
    markup,
    onPick(value) {
      const [kind, name] = value.split(":");
      if (kind === "fg") applyInline(context, "mf-fg-", `mf-fg-${name}`);
      else if (kind === "bg") applyInline(context, "mf-bg-", `mf-bg-${name}`);
      else {
        applyInline(context, "mf-fg-", "");
        applyInline(context, "mf-bg-", "");
      }
    },
  });
}

function alignButton(context) {
  return dropdownButton({
    context,
    label: "Alignment",
    text: "≡",
    panelClass: "mf-ed-align",
    markup: ALIGNMENTS.map(([key, label]) => choice("align", key, label)).join(""),
    onPick(value) {
      alignBlocks(context, value.split(":")[1]);
    },
  });
}

function emojiButton(context) {
  return dropdownButton({
    context,
    label: "Emoji",
    text: "☺",
    panelClass: "mf-ed-emoji",
    markup:
      '<div class="mf-ed-emoji-grid">' +
      EMOJI.map(
        (glyph) =>
          `<button type="button" class="mf-ed-emoji-item" role="menuitem" data-mf-value="emoji:${glyph}" aria-label="${glyph}">${glyph}</button>`,
      ).join("") +
      "</div>",
    onPick(value) {
      context.invoke("editor.restoreRange");
      context.invoke("editor.insertText", value.slice(value.indexOf(":") + 1));
    },
  });
}

function helpButton(context) {
  const rows = SHORTCUTS.map(
    ([keys, label]) =>
      `<div class="mf-ed-shortcut"><span class="mf-ed-keys">${keys
        .split("+")
        .map((key) => `<kbd>${key}</kbd>`)
        .join("+")}</span><span>${label}</span></div>`,
  ).join("");
  return dropdownButton({
    context,
    label: "Keyboard shortcuts",
    text: "?",
    panelClass: "mf-ed-help",
    markup: `<div class="mf-ed-label">Keyboard shortcuts</div>${rows}`,
    onPick() {},
  });
}

export const CUSTOM_BUTTONS = {
  mfFont: fontButton,
  mfColor: colorButton,
  mfAlign: alignButton,
  mfEmoji: emojiButton,
  mfHelp: helpButton,
};

export function buildToolbar(featureList, config) {
  const features = new Set(featureList);
  const groups = [];
  const add = (name, items) => {
    const present = items.filter(Boolean);
    if (present.length) groups.push([name, present]);
  };
  add("history", features.has("undo_redo") ? ["undo", "redo"] : []);
  add("style", features.has("style") ? ["style"] : []);
  add("font", [
    features.has("bold") && "bold",
    features.has("italic") && "italic",
    features.has("underline") && "underline",
    features.has("strike") && "strikethrough",
    features.has("script") && "superscript",
    features.has("script") && "subscript",
    features.has("clear_format") && "clear",
  ]);
  add("look", [features.has("fonts") && "mfFont", features.has("colors") && "mfColor"]);
  add("para", [features.has("lists") && "ul", features.has("lists") && "ol", features.has("align") && "mfAlign"]);
  add("insert", [
    features.has("links") && "link",
    features.has("images") && "picture",
    features.has("video") && config.videoProviders.length > 0 && "video",
    features.has("tables") && "table",
    features.has("hr") && "hr",
    features.has("emoji") && "mfEmoji",
  ]);
  add("view", [
    features.has("fullscreen") && "fullscreen",
    features.has("code_view") && "codeview",
    features.has("help") && "mfHelp",
  ]);
  return groups;
}

export function popoverOptions(featureList) {
  const features = new Set(featureList);
  return {
    image: features.has("images") ? [["remove", ["removeMedia"]]] : [],
    link: features.has("links") ? [["link", ["linkDialogShow", "unlink"]]] : [],
    table: features.has("tables")
      ? [
          ["add", ["addRowDown", "addRowUp", "addColLeft", "addColRight"]],
          ["delete", ["deleteRow", "deleteCol", "deleteTable"]],
        ]
      : [],
    air: [],
  };
}

export const STYLE_TAGS = ["p", "blockquote", "pre", "h2", "h3", "h4", "h5"];
