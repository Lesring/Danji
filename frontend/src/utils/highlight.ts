export function escapeHtml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

export function highlightText(raw: string, excerpt: string) {
  if (!excerpt) return escapeHtml(raw);
  const index = raw.indexOf(excerpt);
  if (index < 0) return escapeHtml(raw);
  return (
    escapeHtml(raw.slice(0, index)) +
    "<mark>" +
    escapeHtml(excerpt) +
    "</mark>" +
    escapeHtml(raw.slice(index + excerpt.length))
  );
}
