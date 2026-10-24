/* Browser helpers for the Flask session authentication endpoints. */
let authState = null;
let statePromise = null;
const listeners = new Set();

function asClientUser(user) {
  if (!user) return null;
  return {
    ...user,
    uid: user.uid || user.id,
    isAnonymous: Boolean(user.isAnonymous),
    getIdToken: async () => '',
  };
}

function notify(user) {
  authState = asClientUser(user);
  for (const callback of listeners) callback(authState);
}

function loadSession() {
  if (!statePromise) {
    statePromise = fetch('/auth/session', { credentials: 'same-origin' })
      .then(async response => response.ok ? (await response.json()).user : null)
      .catch(() => null)
      .then(user => { notify(user); return authState; });
  }
  return statePromise;
}

export function initializeApp(config = {}) { return { config }; }
export function getApp() { return initializeApp(); }
export function getApps() { return [getApp()]; }
export function getAuth() {
  const auth = { get currentUser() { return authState; }, onAuthStateChanged: (_auth, callback) => onAuthStateChanged(auth, callback), signOut: () => signOut(auth) };
  loadSession();
  return auth;
}
export function onAuthStateChanged(_auth, callback) {
  listeners.add(callback);
  loadSession().then(() => callback(authState));
  return () => listeners.delete(callback);
}
export class GoogleAuthProvider {}

export async function signInWithEmailAndPassword(_auth, email, password) {
  const response = await fetch('/auth/login', { method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email, password}) });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error || 'Login failed'), { code: 'auth/invalid-credential' });
  statePromise = null;
  const session = await fetch('/auth/session', {credentials: 'same-origin'}).then(r => r.json());
  notify(session.user);
  return { user: authState };
}

export async function createUserWithEmailAndPassword(_auth, email, password) {
  const body = {email, password};
  for (const key of ['firstName', 'middleName', 'lastName']) {
    const field = document.getElementById(key);
    if (field) body[key] = field.value.trim();
  }
  const response = await fetch('/auth/signup', { method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error || 'Account creation failed'), { code: 'auth/email-already-in-use' });
  const login = await signInWithEmailAndPassword(_auth, email, password);
  return login;
}

export function signInWithPopup() {
  const next = location.pathname.startsWith('/providers/') ? '/providers/status' : '/chat-page';
  const portal = location.pathname.startsWith('/providers/') ? '&portal=provider' : '';
  if (location.pathname === '/providers/signup') {
    const providerFields = {};
    for (const key of ['firstName', 'middleName', 'lastName', 'role', 'licenseNumber']) {
      const field = document.getElementById(key);
      if (field) providerFields[key] = field.value;
    }
    sessionStorage.setItem('menti_provider_application', JSON.stringify(providerFields));
    window.location.assign('/auth/google?next=/providers/signup?oauth=complete&portal=provider');
    return new Promise(() => {});
  }
  window.location.assign(`/auth/google?next=${encodeURIComponent(next)}${portal}`);
  return new Promise(() => {});
}

export async function signInAnonymously() {
  const response = await fetch('/auth/guest', { method: 'POST', credentials: 'same-origin' });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Guest session could not start');
  statePromise = null;
  notify(result.user);
  return { user: authState };
}

export async function signOut() {
  await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
  statePromise = null;
  notify(null);
}

export async function updateProfile(_user, profile) {
  await fetch('/auth/profile', { method: 'PUT', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(profile) });
  if (authState) Object.assign(authState, {displayName: profile.displayName || authState.displayName, photoURL: profile.photoURL || authState.photoURL});
}
