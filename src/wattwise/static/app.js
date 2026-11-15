// HTMX owns network requests and server-rendered updates.
// Browser-only helpers handle dialogs, usage labels, and copying share links.
function updateUsage() {
  const basis = document.getElementById('basis');
  const usage = document.getElementById('usage');
  if (!basis || !usage) return;
  document.getElementById('usage-label').textContent = basis.value === 'hour' ? 'Hours per day' : basis.value === 'year' ? 'Usage included in annual figure' : 'Cycles per week';
  usage.readOnly = basis.value === 'year';
  usage.max = basis.value === 'hour' ? 24 : 10000;
}
function notify(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.style.display = 'block';
  setTimeout(() => { toast.style.display = 'none'; }, 5000);
}
document.addEventListener('htmx:afterSwap', event => {
  if (event.detail.target.id === 'editor-content') {
    updateUsage();
    const dialog = document.getElementById('editor');
    if (!dialog.open) dialog.showModal();
  }
  if (event.detail.target.id === 'library') event.detail.target.scrollIntoView({behavior: 'smooth', block: 'start'});
  document.querySelectorAll('[data-shared-link]').forEach(a => { a.textContent = a.href; });
});
document.addEventListener('closeEditor', () => document.getElementById('editor').close());
document.addEventListener('change', event => {
  if (event.target.id === 'category') {
    const basis = document.getElementById('basis');
    basis.value = ['Heating / cooling', 'Heat pump'].includes(event.target.value) ? 'hour' : event.target.value === 'Refrigerator' ? 'year' : '100cycles';
    document.getElementById('usage').value = basis.value === 'hour' ? 8 : 4;
    updateUsage();
  }
  if (event.target.id === 'basis') updateUsage();
});
document.addEventListener('click', async event => {
  if (event.target.closest('[data-close]')) document.getElementById('editor').close();
  if (event.target.closest('[data-close-library]')) document.getElementById('library').replaceChildren();
  const copy = event.target.closest('[data-copy]');
  if (copy) {
    try { await navigator.clipboard.writeText(new URL(copy.dataset.copy, location.origin).href); notify('Share link copied.'); }
    catch { notify('Select the share link to copy it.'); }
  }
});
document.addEventListener('htmx:responseError', event => {
  notify(event.detail.xhr.status === 403 ? 'Session expired. Refresh the page before trying again.' : 'The update failed. Refresh the page and try again.');
});
document.addEventListener('htmx:sendError', () => notify('Could not reach the server. Check your connection and try again.'));
