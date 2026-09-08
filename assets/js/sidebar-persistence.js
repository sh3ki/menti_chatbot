function restoreMentiSidebarCache() {
  try {
    const uid = sessionStorage.getItem('menti_sidebar_uid');
    if (!uid) return;
    const archived = sessionStorage.getItem('menti_sidebar_show_archived') === '1';
    const key = `menti_sidebar_cache:${uid}:${archived ? 'archived' : 'active'}`;
    const cached = JSON.parse(sessionStorage.getItem(key) || 'null');
    const list = document.getElementById('convList');
    if (!list || !Array.isArray(cached && cached.data)) return;
    if (!cached.data.length) {
      list.innerHTML = '<div class="empty-state">No conversations yet</div>';
      return;
    }
    list.innerHTML = cached.data.map(function (chat) {
      const title = String(chat.title || 'Untitled');
      const safeTitle = title.replace(/[&<>"']/g, function (m) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m];
      });
      const id = chat.id ? encodeURIComponent(chat.id) : '';
      const href = id ? `/chat-page?conversation_id=${id}${archived ? '&is_archived=1' : ''}` : '/chat-page';
      return `<div class="conv-item" title="${safeTitle}" onclick="window.location.href='${href}'">${safeTitle}</div>`;
    }).join('');
  } catch (_) {}
}

window.restoreMentiSidebarCache = restoreMentiSidebarCache;
restoreMentiSidebarCache();

// Conversation search is deliberately click-to-search: typing does not
// query or re-render the sidebar until the user presses Search.
(function setupSidebarSearch() {
  const list = document.getElementById('convList') || document.getElementById('chatsContainer');
  if (!list || document.getElementById('sidebarSearch')) return;

  const style = document.createElement('style');
  style.textContent = `
    .sidebar-search { display:flex; gap:6px; margin:0 0 10px; }
    .sidebar-search input { min-width:0; flex:1; padding:8px 9px; border:1px solid #D9E2DA; border-radius:8px; background:#fff; color:#28282B; font:inherit; font-size:12px; }
    .sidebar-search button { padding:8px 10px; border:0; border-radius:8px; background:#2C5E31; color:#fff; cursor:pointer; font:inherit; font-size:12px; font-weight:600; }
    .sidebar-search button:hover { background:#234A27; }
    .sidebar-search mark { padding:0 1px; border-radius:2px; background:#FFE58A; color:inherit; }
    body.dark-mode .sidebar-search input { background:#2A2A2A; color:#F5F5F5; border-color:#505050; }
    body.dark-mode .sidebar-search input::placeholder { color:#A8A8A8; }
    body.dark-mode .sidebar-search input:focus { outline:none; border-color:#8FD596; box-shadow:0 0 0 2px rgba(143,213,150,.18); }
    body.dark-mode .sidebar-search mark { background:#806F32; color:#FFF4B0; }
  `;
  document.head.appendChild(style);

  const wrapper = document.createElement('div');
  wrapper.className = 'sidebar-search';
  wrapper.innerHTML = '<input id="sidebarSearch" type="search" placeholder="Search chats..." aria-label="Search chats"><button type="button" id="sidebarSearchBtn">Search</button>';
  const sidebarHeader = document.querySelector('.sidebar-header');
  if (sidebarHeader) sidebarHeader.insertBefore(wrapper, sidebarHeader.firstChild);
  else list.parentNode.insertBefore(wrapper, list);

  const input = wrapper.querySelector('input');
  const button = wrapper.querySelector('button');
  let activeQuery = '';
  let updating = false;

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (m) {
      return ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[m];
    });
  }

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function applySearch() {
    const items = Array.from(list.querySelectorAll('.conv-item, .chat-item'));
    let visible = 0;
    const pattern = activeQuery ? new RegExp(escapeRegExp(activeQuery), 'ig') : null;
    items.forEach(function (item) {
      const title = item.querySelector('.chat-item-text') || item;
      const text = title.textContent || '';
      const matched = !pattern || pattern.test(text);
      pattern && (pattern.lastIndex = 0);
      item.style.display = matched ? '' : 'none';
      if (matched) {
        visible += 1;
        title.innerHTML = pattern
          ? escapeHtml(text).replace(pattern, '<mark>$&</mark>')
          : escapeHtml(text);
      }
    });

    const oldEmpty = list.querySelector('.sidebar-search-empty');
    if (oldEmpty) oldEmpty.remove();
    if (activeQuery && items.length && visible === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-state sidebar-search-empty';
      empty.textContent = 'No chats found';
      list.appendChild(empty);
    }
  }

  button.addEventListener('click', function () {
    activeQuery = input.value.trim();
    applySearch();
  });
  input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') button.click();
  });

  // The chat page refreshes this list after creating, renaming, or archiving
  // chats. Reapply the last submitted query to newly-rendered results.
  const observer = new MutationObserver(function () {
    if (activeQuery && !updating) {
      updating = true;
      observer.disconnect();
      applySearch();
      observer.observe(list, { childList: true, subtree: true });
      updating = false;
    }
  });
  observer.observe(list, { childList: true, subtree: true });
})();

const archivedToggle = document.getElementById('toggleArchivedBtn');
if (archivedToggle && !archivedToggle.getAttribute('onclick')) {
  archivedToggle.addEventListener('click', function () {
    const next = sessionStorage.getItem('menti_sidebar_show_archived') !== '1';
    sessionStorage.setItem('menti_sidebar_show_archived', next ? '1' : '0');
    const text = document.getElementById('toggleArchivedText');
    const title = document.getElementById('chatsTitle');
    archivedToggle.classList.toggle('active', next);
    if (text) text.textContent = next ? 'Show Active' : 'Show Archived';
    if (title) title.textContent = next ? 'Archived Chats' : 'Active Chats';
    restoreMentiSidebarCache();
  });
}
