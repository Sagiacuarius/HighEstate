const overlay = document.getElementById('commandOverlay');
const dashboardGrid = document.querySelector('.dashboard-grid');
const viewLinks = document.querySelectorAll('[data-view]');
const viewSections = document.querySelectorAll('[data-section]');

function showView(viewName) {
  if (viewName === 'overview') {
    dashboardGrid.classList.remove('is-detail-view');
    viewSections.forEach((section) => section.classList.remove('is-visible'));
    return;
  }
  dashboardGrid.classList.add('is-detail-view');
  viewSections.forEach((section) => section.classList.toggle('is-visible', section.dataset.section === viewName));
}

viewLinks.forEach((link) => {
  link.addEventListener('click', (event) => {
    event.preventDefault();
    const viewName = link.dataset.view;
    viewLinks.forEach((item) => item.classList.toggle('active', item === link));
    showView(viewName);
    history.replaceState(null, '', `#${viewName}`);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
});

showView('overview');


const input = document.getElementById('commandInput');
const result = document.getElementById('commandResult');

function openCommand() {
  overlay.classList.add('open');
  setTimeout(() => input.focus(), 80);
}

function closeCommand() {
  overlay.classList.remove('open');
  result.innerHTML = '';
}

function fillCommand(text) {
  input.value = text;
  input.focus();
}

function runCommand() {
  const text = input.value.trim();
  if (!text) return;
  result.innerHTML = `<div class="result-box"><b>Atelier está preparando un borrador.</b><br>Interpreté: “${escapeHtml(text)}”. En la versión operativa se validarán permisos, herramientas y alcance antes de activar cualquier acción.</div>`;
}

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[character]));
}

overlay.addEventListener('click', (event) => {
  if (event.target === overlay) closeCommand();
});

input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') runCommand();
  if (event.key === 'Escape') closeCommand();
});
