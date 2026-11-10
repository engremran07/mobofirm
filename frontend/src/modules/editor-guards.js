// Summernote's insertParagraph throws when Enter is pressed while the caret sits in an
// inline element that holds no content (an empty <b>, <span class="mf-fg-red"> or a
// wrapper that only contains a zero-width space), which some command sequences leave
// behind. Removing such wrappers just before Summernote handles the key keeps Enter
// working and loses nothing: an empty inline element has no visible effect.

const INLINE = new Set([
  "A",
  "B",
  "BIG",
  "CODE",
  "DEL",
  "EM",
  "FONT",
  "I",
  "INS",
  "MARK",
  "S",
  "SMALL",
  "SPAN",
  "STRIKE",
  "STRONG",
  "SUB",
  "SUP",
  "U",
]);
const INVISIBLE = /[\u200B\uFEFF]/g;
const CONTENT = "img,iframe,video,audio,table,hr,input";

function isEmptyInline(node) {
  return (
    node.nodeType === Node.ELEMENT_NODE &&
    INLINE.has(node.tagName) &&
    node.textContent.replace(INVISIBLE, "") === "" &&
    !node.querySelector(CONTENT)
  );
}

function outermostEmptyInline(editable, start) {
  let node = start && start.nodeType === Node.TEXT_NODE ? start.parentNode : start;
  let found = null;
  while (node && node !== editable && editable.contains(node)) {
    if (isEmptyInline(node)) found = node;
    node = node.parentNode;
  }
  return found;
}

// Returns true when a wrapper was removed (the caret is then placed where it was).
export function removeEmptyInlineAtCaret(editable) {
  const doc = editable.ownerDocument;
  const selection = doc.getSelection();
  if (!selection || !selection.rangeCount || !selection.isCollapsed) return false;
  const target = outermostEmptyInline(editable, selection.anchorNode);
  if (!target) return false;
  const parent = target.parentNode;
  const index = Array.prototype.indexOf.call(parent.childNodes, target);
  parent.removeChild(target);
  if (parent !== editable && parent.childNodes.length === 0) {
    parent.appendChild(doc.createElement("br"));
  }
  const range = doc.createRange();
  range.setStart(parent, Math.min(index, parent.childNodes.length));
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}

export function guardEmptyInlineOnEnter(editor) {
  editor.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Enter" || event.isComposing) return;
      const editable = event.target instanceof Element ? event.target.closest(".note-editable") : null;
      if (editable && editor.contains(editable)) removeEmptyInlineAtCaret(editable);
    },
    true,
  );
}
