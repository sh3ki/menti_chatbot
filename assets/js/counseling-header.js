(function () {
  const style = document.createElement('style');
  style.textContent = `
    .header { height: auto; min-height: 70px; padding: 16px 24px; box-shadow: 0 2px 8px rgba(15,15,15,.04); position: relative; z-index: 100; }
    .header-left { display:flex; align-items:center; gap:12px; }
    .header-logo { width:36px; height:36px; object-fit:contain; }
    body.dark-mode .header-logo { content:url('/static/menti%20logo%20dark.png'); }
    .header-brand-link { display:flex; align-items:center; gap:12px; color:inherit; text-decoration:none; }
    .header h1 { margin:0; color:#2c5e31; font-size:22px; font-weight:700; }
    .header-right { display:flex; align-items:center; gap:10px; }
    .dark-mode-toggle { display:flex; align-items:center; justify-content:center; padding:8px 14px; background:#f0f0f0; color:#2c5e31; border:1.5px solid #e0e0e0; border-radius:10px; cursor:pointer; }
    .dark-mode-toggle svg { width:16px; height:16px; }
    .dark-mode-toggle .moon-icon { display:none; }
    .profile-menu-wrap { position:relative; }
    .profile-menu-trigger { display:flex; align-items:center; gap:8px; height:40px; padding:5px 10px 5px 5px; background:none; border:1.5px solid #e0e0e0; border-radius:22px; cursor:pointer; font:inherit; transition:border-color .2s, background .2s; }
    .profile-menu-trigger:hover, .profile-menu-wrap.open .profile-menu-trigger { border-color:#4fa858; background:#f6fbf6; }
    .header-avatar { width:30px !important; height:30px !important; min-width:30px; min-height:30px; border-radius:50%; background:#e8f5e9; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:700; color:#2c5e31; overflow:hidden; flex-shrink:0; }
    .header-avatar img { width:100%; height:100%; object-fit:contain; border-radius:50%; }
    .header-name { font-size:13px; font-weight:600; color:#28282b; max-width:130px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .header-chevron { width:14px; height:14px; color:#28282b; opacity:.5; transition:transform .2s; }
    .profile-menu-wrap.open .header-chevron { transform:rotate(180deg); }
    .profile-dropdown { display:none; position:absolute; right:0; top:calc(100% + 8px); background:#fff; border:1px solid #e8e8e8; border-radius:12px; box-shadow:0 8px 28px rgba(0,0,0,.12); min-width:196px; z-index:300; overflow:hidden; padding:4px 0; }
    .profile-menu-wrap.open .profile-dropdown { display:block; }
    .profile-dropdown-item { display:flex; align-items:center; gap:10px; padding:11px 16px; font-size:13px; font-weight:500; color:#28282b; text-decoration:none; transition:background .15s; width:100%; text-align:left; background:none; border:0; cursor:pointer; font-family:inherit; }
    .profile-dropdown-item:hover { background:#f8f8f8; color:#2c5e31; }
    .profile-dropdown-item.active-page { color:#2c5e31; background:#f0f7f1; font-weight:600; }
    .profile-dropdown-item svg { width:15px; height:15px; flex-shrink:0; opacity:.7; }
    .profile-dropdown-divider { height:1px; background:#f0f0f0; margin:4px 0; }
    .profile-dropdown-logout { color:#c62828; }
    .profile-dropdown-logout:hover { background:#fff5f5!important; color:#c62828!important; }
    body.dark-mode .dark-mode-toggle { background:#2c5e31; color:#fff; border-color:#3a7a41; }
    body.dark-mode .dark-mode-toggle .sun-icon { display:none; }
    body.dark-mode .dark-mode-toggle .moon-icon { display:block; }
    body.dark-mode .header { background:#242424; border-bottom-color:#3a3a3a; }
    body.dark-mode .header h1 { color:#4fa858; }
    body.dark-mode .profile-menu-trigger { border-color:#3a3a3a; background:none; }
    body.dark-mode .profile-menu-trigger:hover, body.dark-mode .profile-menu-wrap.open .profile-menu-trigger { border-color:#4fa858; background:#1f3a21; }
    body.dark-mode .header-avatar { background:#1f3a21; color:#a0d8a0; }
    body.dark-mode .header-name { color:#e0e0e0; }
    body.dark-mode .header-chevron { color:#b0b0b0; }
    body.dark-mode .profile-dropdown { background:#2c2c2c; border-color:#3a3a3a; }
    body.dark-mode .profile-dropdown-item { color:#fff; }
    body.dark-mode .profile-dropdown-item.active-page { color:#4fa858; background:#1f3a21; }
  `;
  document.head.appendChild(style);

  const sharedHeaderOverrides = document.createElement('style');
  sharedHeaderOverrides.textContent = `
    .incognito-topbar-btn { display:flex; align-items:center; gap:6px; padding:8px 14px; background:#f3f0ff; color:#5b21b6; border:1.5px solid #ddd6fe; border-radius:10px; cursor:pointer; font:600 13px inherit; }
    .incognito-topbar-btn svg { width:16px; height:16px; flex-shrink:0; }
    .incognito-topbar-btn:hover { background:#ede9fe; border-color:#c4b5fd; }
    body.dark-mode .incognito-topbar-btn { background:#1f3a21; color:#a0d8a0; border-color:#3a6a3e; }
  `;
  document.head.appendChild(sharedHeaderOverrides);

  const header = document.querySelector('.header');
  if (!header) return;
  header.innerHTML = `<div class="header-left"><a href="/chat-page" class="header-brand-link" aria-label="Menti chat home"><img src="/static/menti logo.png" alt="Menti" class="header-logo"><h1>Menti</h1></a></div><div class="header-right"><button class="dark-mode-toggle" id="darkModeToggle" title="Toggle Dark Mode"><svg class="sun-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M12 18C8.68 18 6 15.32 6 12s2.68-6 6-6 6 2.68 6 6-2.68 6-6 6zm0-10c-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4-1.79-4-4-4z"/></svg><svg class="moon-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M9.37 5.51C9.9 9.62 13.3 12.86 17.47 12.86c.4 0 .82-.02 1.23.12C17.85 17.75 14.64 21 10.5 21 5.04 21 1 16.96 1 11.5 1 7.36 4.25 4.15 8.26 3.78c-.1.52-.14 1.05-.14 1.6l1.25-.11z"/></svg></button><button class="incognito-topbar-btn" id="incognitoTopBtn">◉ Private</button><div class="profile-menu-wrap" id="profileMenuWrap"><button class="profile-menu-trigger" id="profileMenuTrigger"><div class="header-avatar" id="headerAvatar">?</div><span class="header-name" id="headerName">Loading...</span><svg class="header-chevron" viewBox="0 0 24 24" fill="currentColor"><path d="M7 10l5 5 5-5z"/></svg></button><div class="profile-dropdown" id="profileDropdown"><a class="profile-dropdown-item" id="progressNavLink" href="#">▣ My Progress</a><a class="profile-dropdown-item" id="planNavLink" href="#">▤ Personalized Plan</a><a class="profile-dropdown-item" id="journalNavLink" href="#">▤ My Journal</a><a class="profile-dropdown-item" id="offlineResourcesNavLink" href="/offline-resources">▣ Offline Resources</a><a class="profile-dropdown-item" href="/profile">♟ Profile Settings</a><a class="profile-dropdown-item active-page" href="/counseling-appointments">▣ Counseling Sessions</a><div class="profile-dropdown-divider"></div><button class="profile-dropdown-item profile-dropdown-logout" id="counselingLogoutBtn">⇥ Logout</button></div></div></div>`;
  const privateButton = document.getElementById('incognitoTopBtn');
  privateButton.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg>Private';
  document.getElementById('darkModeToggle').innerHTML = '<svg class="sun-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zM17.24 4.84l1.79-1.8-1.41-1.41-1.79 1.79 1.41 1.42zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8zm12.9 0l1.79 1.79 1.41-1.41-1.8-1.79-1.4 1.41z"/></svg><svg class="moon-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"/></svg>';
  const dropdown = document.getElementById('profileDropdown');
  dropdown.innerHTML = `<a class="profile-dropdown-item" id="progressNavLink" href="#"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/></svg>My Progress</a><a class="profile-dropdown-item" id="planNavLink" href="#"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm1 16H8v-2h7v2zm1-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>Personalized Plan</a><a class="profile-dropdown-item" id="journalNavLink" href="#"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zm-2-9H7V8h10v2zm0 4H7v-2h10v2z"/></svg>My Journal</a><a class="profile-dropdown-item" id="offlineResourcesNavLink" href="/offline-resources"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 18H4V6h16v12zm0-14H4c-1.1 0-2 .9-2 2v14h20V6c0-1.1-.9-2-2-2zm-7 13h-2v2h2v-2z"/></svg>Offline Resources</a><a class="profile-dropdown-item" href="/profile"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>Profile Settings</a><a class="profile-dropdown-item active-page" href="/counseling-appointments"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V9h14v11zM7 11h5v5H7v-5z"/></svg>Counseling Sessions</a><div class="profile-dropdown-divider"></div><button class="profile-dropdown-item profile-dropdown-logout" id="logoutBtn"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.58L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4V5z"/></svg>Logout</button>`;
  const wrap = document.getElementById('profileMenuWrap');
  document.getElementById('profileMenuTrigger').onclick = e => { e.stopPropagation(); wrap.classList.toggle('open'); };
  document.addEventListener('click', e => { if (!wrap.contains(e.target)) wrap.classList.remove('open'); });
  document.getElementById('incognitoTopBtn').onclick = () => { location.href = '/incognito'; };
  document.getElementById('logoutBtn').onclick = () => { location.href = '/'; };
  const toggle = document.getElementById('darkModeToggle');
  if (localStorage.getItem('menti_dark_mode') === 'true') document.body.classList.add('dark-mode');
  toggle.onclick = () => { document.body.classList.toggle('dark-mode'); localStorage.setItem('menti_dark_mode', document.body.classList.contains('dark-mode')); };
  import('https://www.gstatic.com/firebasejs/10.8.0/firebase-app.js').then(({ getApp, getApps, initializeApp }) => import('https://www.gstatic.com/firebasejs/10.8.0/firebase-auth.js').then(({ getAuth, onAuthStateChanged }) => {
    const firebaseConfig = {
      apiKey: 'AIzaSyBCYdU_-ZJjws8JTdmLiCgwkYD6O4ze9z0',
      authDomain: 'menti-7b8a1.firebaseapp.com',
      projectId: 'menti-7b8a1',
      storageBucket: 'menti-7b8a1.firebasestorage.app',
      messagingSenderId: '164612023817',
      appId: '1:164612023817:web:c0437146e86951377f3ad8'
    };
    const firebaseApp = getApps().length ? getApp() : initializeApp(firebaseConfig);
    return onAuthStateChanged(getAuth(firebaseApp), async user => {
    if (!user || user.isAnonymous) return;
    wrap.style.display = 'block'; document.getElementById('incognitoTopBtn').style.display = 'flex';
    let profile = {};
    try { profile = await fetch(`/user/profile/${user.uid}`).then(response => response.ok ? response.json() : ({})); } catch (_) {}
    const name = profile.displayName || user.displayName || user.email || 'User'; document.getElementById('headerName').textContent = name;
    const avatar = document.getElementById('headerAvatar');
    const photoURL = user.photoURL || profile.photoURL || '';
    if (photoURL) avatar.innerHTML = `<img src="${photoURL}" alt="Avatar" onerror="this.parentNode.innerHTML='<span>${name.slice(0, 1).toUpperCase()}</span>'">`;
    else avatar.textContent = (name.match(/\b\w/g) || ['U']).slice(0, 2).join('').toUpperCase();
    document.getElementById('progressNavLink').href = `/progress/${user.uid}`; document.getElementById('planNavLink').href = `/personalized-plan/${user.uid}`; document.getElementById('journalNavLink').href = `/journal/${user.uid}`;
    });
  }));
})();
