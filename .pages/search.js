// Site-wide search over the prebuilt index. Progressive enhancement: every
// page is complete without this file; it only adds filtering as you type.
(() => {
  const input = document.getElementById("search-input");
  const content = document.getElementById("content");
  const results = document.getElementById("search-results");
  if (!input || !content || !results) return;

  let records = [];
  let fuse = null;

  const escapeHtml = (value) =>
    String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const renderCard = (record) => `
    <article class="entry-card">
      <div class="entry-topline">
        <h3 class="entry-title">
          <a class="entry-link" href="${escapeHtml(record.href)}" target="_blank" rel="noreferrer">${escapeHtml(record.title)}</a>
        </h3>
      </div>
      <p class="entry-description">${escapeHtml(record.description)}</p>
      <p class="entry-meta"><a href="${escapeHtml(record.url)}">${escapeHtml(record.section)}${record.subsection ? " › " + escapeHtml(record.subsection) : ""}</a></p>
    </article>`;

  const search = (query) => {
    if (fuse) return fuse.search(query).map((hit) => hit.item);
    const needle = query.toLowerCase();
    return records.filter((record) =>
      [record.title, record.description, record.section, record.subsection].join(" ").toLowerCase().includes(needle)
    );
  };

  const apply = () => {
    const query = input.value.trim();
    if (!query) {
      results.hidden = true;
      content.hidden = false;
      return;
    }
    const hits = search(query).slice(0, 40);
    results.innerHTML = hits.length
      ? `<section class="section-shell"><div class="section-header"><div class="section-kicker">${hits.length}</div><div><h2 class="section-title">Matching projects</h2></div></div><div class="card-grid">${hits.map(renderCard).join("")}</div></section>`
      : `<div class="empty-state">No matching entries.</div>`;
    results.hidden = false;
    content.hidden = true;
  };

  const ready = fetch("/search-index.json")
    .then((response) => response.json())
    .then((data) => {
      records = data;
      fuse = window.Fuse
        ? new window.Fuse(records, {
            threshold: 0.34,
            ignoreLocation: true,
            keys: [
              { name: "title", weight: 0.45 },
              { name: "description", weight: 0.35 },
              { name: "section", weight: 0.12 },
              { name: "subsection", weight: 0.08 }
            ]
          })
        : null;
    })
    .catch(() => {});

  input.addEventListener("input", () => {
    ready.then(apply);
  });
})();
