const normalize = (value) =>
  value.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

// Filters a <select> by replacing its options rather than toggling `hidden`,
// which iOS Safari ignores. The chosen value is tracked so clearing the search
// or submitting the form never loses it.
function bindCountrySearch(input) {
  if (input.dataset.countrySearchReady) return;
  const select = document.getElementById(input.dataset.countrySearchFor);
  if (!(select instanceof HTMLSelectElement)) return;

  input.dataset.countrySearchReady = "true";
  const status = document.getElementById(`${input.id}-status`);
  const options = Array.from(select.options);
  let chosen = select.value;

  select.addEventListener("change", () => {
    chosen = select.value;
  });

  const apply = (matching) => {
    select.replaceChildren(...matching);
    const keeps = matching.some((option) => option.value === chosen);
    select.value = keeps ? chosen : "";
  };

  const update = () => {
    const query = normalize(input.value.trim());
    const matching = options.filter(
      (option) =>
        option.value === "" ||
        !query ||
        normalize(`${option.textContent} ${option.value}`).includes(query),
    );
    apply(matching);
    if (status) {
      const count = matching.filter((option) => option.value !== "").length;
      status.textContent = query ? `${count} matching options` : `${count} options available`;
    }
  };

  input.addEventListener("input", update);
  select.form?.addEventListener("submit", () => apply(options));
  update();
}

export function initializeCountrySearch(root = document) {
  root.querySelectorAll("[data-country-search-for]").forEach(bindCountrySearch);
}
