// "Not listed? Request it" links under catalogue pickers. The link target is the
// request form; as the user types in the picker (or picks a parent such as the brand)
// the link is rewritten so the request form opens prefilled with exactly that.
//
//   <a href="/firmwares/requests/model/" data-request-link
//      data-source="upload-model"            picker input whose typed text becomes ?name=
//      data-param="name"                      query parameter for that text
//      data-context="brand=upload-brand">     param=picker input id, value read from <id>-value

function readLink(link) {
  const base = new URL(link.dataset.base ?? link.getAttribute("href"), window.location.origin);
  link.dataset.base = `${base.pathname}${base.search}`;
  return base;
}

function updateLink(link) {
  const url = readLink(link);
  const source = document.getElementById(link.dataset.source ?? "");
  const param = link.dataset.param ?? "name";
  const typed = source instanceof HTMLInputElement ? source.value.trim() : "";
  if (typed) url.searchParams.set(param, typed);
  else url.searchParams.delete(param);

  for (const pair of (link.dataset.context ?? "").split(",")) {
    const [name, pickerId] = pair.split("=");
    if (!name || !pickerId) continue;
    const hidden = document.getElementById(`${pickerId}-value`);
    if (hidden instanceof HTMLInputElement && hidden.value) url.searchParams.set(name, hidden.value);
    else url.searchParams.delete(name);
  }
  link.setAttribute("href", `${url.pathname}${url.search}`);
}

export function initializeRequestLinks() {
  const links = () => document.querySelectorAll("a[data-request-link]");
  const refresh = () => links().forEach(updateLink);
  document.addEventListener("input", refresh);
  document.addEventListener("combobox:select", refresh);
  document.addEventListener("combobox:clear", refresh);
  document.addEventListener("htmx:afterSwap", refresh);
  refresh();
}
