import { getAuth, onAuthStateChanged } from '/static/js/auth-client.js';

if (new URLSearchParams(location.search).get('oauth') === 'complete') {
  const auth = getAuth();
  onAuthStateChanged(auth, async user => {
    if (!user) return;
    const fields = JSON.parse(sessionStorage.getItem('menti_provider_application') || '{}');
    for (const key of ['firstName', 'middleName', 'lastName', 'role', 'licenseNumber']) {
      const input = document.getElementById(key);
      if (input && fields[key]) input.value = fields[key];
    }
    try {
      const response = await fetch('/providers/register', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(fields),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Provider application failed');
      sessionStorage.removeItem('menti_provider_application');
      location.replace(result.redirect || '/providers/status');
    } catch (error) {
      const message = document.getElementById('msg');
      message.textContent = error.message;
      message.style.display = 'block';
    }
  });
}
