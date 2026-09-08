/**
 * Global Call Manager - Handles incoming counseling calls across all pages
 * Features:
 * - Polls for incoming calls every 2 seconds
 * - Professional modal UI for incoming calls
 * - Ringtone notification
 * - Auto-decline after 30 seconds
 * - Works on any page
 * 
 * IMPORTANT: Pages must call window.mentiCallManager.setUserToken(token) after Firebase auth
 */

class MentiCallManager {
  constructor() {
    this.pollingInterval = null;
    this.pollingMs = 2000;
    this.activeCall = null;
    this.callTimeout = null;
    this.isListening = false;
    this.userToken = null;
    this.audioContext = null;
    this.ringtoneAudio = null;
    this.userEmail = null;
    this.isRinging = false;
    // Tracks appointment IDs whose current offer has already been accepted/declined,
    // so we don't re-ring while waiting for the answer/end signal to propagate.
    this.handledOffers = new Map();
    this.realtimeReady = false;
    this.incomingStream = null;
    this.appointmentUnsubscribe = null;
    this.signalUnsubscribers = new Map();
    this.approvedAppointments = new Map();

    this.firebaseConfig = {
      apiKey: 'AIzaSyBCYdU_-ZJjws8JTdmLiCgwkYD6O4ze9z0',
      authDomain: 'menti-7b8a1.firebaseapp.com',
      projectId: 'menti-7b8a1',
      storageBucket: 'menti-7b8a1.firebasestorage.app',
      messagingSenderId: '164612023817',
      appId: '1:164612023817:web:c0437146e86951377f3ad8'
    };
    
    console.log('[MentiCallManager] Initialized, waiting for user token...');
    
    // Wait for page to initialize Firebase and provide token
    this.waitForAuth();
  }

  waitForAuth() {
    // Check every 500ms if a token is available
    const checkToken = setInterval(() => {
      if (!this.userToken && window.__mentiUserToken) {
        this.userToken = window.__mentiUserToken;
      } else if (!this.userToken) {
        this.userToken = localStorage.getItem('menti_user_token');
      }
      if (this.userToken) {
        clearInterval(checkToken);
        console.log('[MentiCallManager] Token received, starting polling');
        this.startPolling();
      }
    }, 500);
    
    // Give up after 30 seconds and try anyway (fallback for pages without auth)
    setTimeout(() => {
      clearInterval(checkToken);
      if (!this.userToken && !this.isListening) {
        console.log('[MentiCallManager] No token after 30s, starting polling without auth');
        this.startPolling();
      }
    }, 30000);
  }

  setUserToken(token) {
    this.userToken = token;
    window.__mentiUserToken = token;
    try {
      localStorage.setItem('menti_user_token', token);
    } catch (_) {}
    if (!this.isListening) {
      this.startPolling();
    }
  }

  buildAuthHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (this.userToken) {
      headers['Authorization'] = `Bearer ${this.userToken}`;
    }
    return headers;
  }

  startPolling() {
    if (this.isListening) return;
    this.isListening = true;
    
    console.log('[MentiCallManager] Starting polling with token:', !!this.userToken);
    
    // Immediate first check
    this.checkForIncomingCalls();

    // Try server-side SSE realtime first; keep polling as fallback safety net.
    this.startIncomingStatusStream().catch((err) => {
      console.log('[MentiCallManager] Realtime stream setup failed, using polling fallback:', err.message);
      this.realtimeReady = false;
      this.setPollingInterval(2000);
    });
    
    // Polling remains as fallback; when realtime is ready this interval is reduced.
    this.setPollingInterval(this.pollingMs);
  }

  setPollingInterval(ms) {
    this.pollingMs = ms;
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = null;
    }
    this.pollingInterval = setInterval(() => this.checkForIncomingCalls(), this.pollingMs);
  }

  stopRealtimeIncomingCalls() {
    if (this.incomingStream) {
      try {
        this.incomingStream.close();
      } catch (_) {}
      this.incomingStream = null;
    }
    if (this.appointmentUnsubscribe) {
      try {
        this.appointmentUnsubscribe();
      } catch (_) {}
      this.appointmentUnsubscribe = null;
    }
    for (const unsubscribe of this.signalUnsubscribers.values()) {
      try {
        unsubscribe();
      } catch (_) {}
    }
    this.signalUnsubscribers.clear();
    this.approvedAppointments.clear();
    this.realtimeReady = false;
  }

  async startIncomingStatusStream() {
    return new Promise((resolve, reject) => {
      const token = this.userToken || localStorage.getItem('menti_user_token') || '';
      const streamUrl = token
        ? `/api/video/incoming-stream?token=${encodeURIComponent(token)}`
        : '/api/video/incoming-stream';

      let settled = false;
      const stream = new EventSource(streamUrl);
      const startupTimer = setTimeout(() => {
        if (settled) return;
        settled = true;
        try { stream.close(); } catch (_) {}
        reject(new Error('Incoming stream connection timeout'));
      }, 8000);

      const markReady = () => {
        if (settled) return;
        settled = true;
        clearTimeout(startupTimer);
        this.incomingStream = stream;
        this.realtimeReady = true;
        // Keep a low-frequency fallback poll for resilience.
        this.setPollingInterval(15000);
        console.log('[MentiCallManager] Realtime call notifications enabled (SSE)');
        resolve(true);
      };

      stream.addEventListener('connected', () => {
        markReady();
      });

      stream.addEventListener('appointment_update', (evt) => {
        markReady();
        try {
          const payload = JSON.parse(evt.data || '{}');
          const appointment = payload.appointment || null;
          if (!appointment || !appointment.id) return;
          this.approvedAppointments.set(appointment.id, appointment);
        } catch (_) {}
      });

      stream.addEventListener('signal_update', (evt) => {
        markReady();
        try {
          const payload = JSON.parse(evt.data || '{}');
          const appointmentId = payload.appointmentId;
          const signal = payload.signal || {};
          const incomingAppointment = payload.appointment || null;
          if (incomingAppointment && incomingAppointment.id) {
            this.approvedAppointments.set(incomingAppointment.id, incomingAppointment);
          }
          const appointment = this.approvedAppointments.get(appointmentId) || incomingAppointment;
          if (!appointment || !appointment.id) return;
          this.processSignalUpdate(appointment, signal);
        } catch (err) {
          console.error('[MentiCallManager] Stream signal parsing error:', err);
        }
      });

      stream.addEventListener('appointment_removed', (evt) => {
        markReady();
        try {
          const payload = JSON.parse(evt.data || '{}');
          const appointmentId = payload.appointmentId;
          if (!appointmentId) return;
          this.approvedAppointments.delete(appointmentId);
          this.handledOffers.delete(appointmentId);
        } catch (_) {}
      });

      stream.onerror = () => {
        if (!settled) {
          settled = true;
          clearTimeout(startupTimer);
          try { stream.close(); } catch (_) {}
          reject(new Error('Incoming stream unavailable'));
          return;
        }
        console.warn('[MentiCallManager] Incoming stream disconnected, reverting to polling');
        this.stopRealtimeIncomingCalls();
        this.setPollingInterval(2000);
      };
    });
  }

  async waitForFirebaseUser(auth, timeoutMs = 6000, onAuthStateChangedFn = null) {
    if (auth.currentUser) return auth.currentUser;
    return new Promise((resolve) => {
      let resolved = false;
      const stop = setTimeout(() => {
        if (resolved) return;
        resolved = true;
        try { unsub(); } catch (_) {}
        resolve(auth.currentUser || null);
      }, timeoutMs);
      const subscribe = onAuthStateChangedFn || ((authObj, cb) => authObj.onAuthStateChanged(cb));
      const unsub = subscribe(auth, (user) => {
        if (resolved) return;
        resolved = true;
        clearTimeout(stop);
        try { unsub(); } catch (_) {}
        resolve(user || null);
      });
    });
  }

  async startRealtimeIncomingCalls() {
    const [{ initializeApp, getApps, getApp }, { getAuth, onAuthStateChanged }, { getFirestore, collection, query, where, doc, onSnapshot }] = await Promise.all([
      import('https://www.gstatic.com/firebasejs/10.8.0/firebase-app.js'),
      import('https://www.gstatic.com/firebasejs/10.8.0/firebase-auth.js'),
      import('https://www.gstatic.com/firebasejs/10.8.0/firebase-firestore.js')
    ]);

    const app = getApps().length ? getApp() : initializeApp(this.firebaseConfig);
    const auth = getAuth(app);
    const user = await this.waitForFirebaseUser(auth, 6000, onAuthStateChanged);
    if (!user) {
      throw new Error('No Firebase user session available for realtime listener');
    }

    const fs = getFirestore(app);
    const appointmentsQuery = query(
      collection(fs, 'appointments'),
      where('userId', '==', user.uid),
      where('status', '==', 'approved')
    );

    this.appointmentUnsubscribe = onSnapshot(appointmentsQuery, (snapshot) => {
      const liveIds = new Set();

      snapshot.forEach((snap) => {
        const appointment = { id: snap.id, ...(snap.data() || {}) };
        liveIds.add(appointment.id);
        this.approvedAppointments.set(appointment.id, appointment);
        if (!this.signalUnsubscribers.has(appointment.id)) {
          const signalRef = doc(fs, 'video_signals', appointment.id);
          const signalUnsub = onSnapshot(signalRef, (signalSnap) => {
            const signal = signalSnap.exists() ? (signalSnap.data() || {}) : {};
            this.processSignalUpdate(appointment, signal);
          });
          this.signalUnsubscribers.set(appointment.id, signalUnsub);
        }
      });

      for (const [appointmentId, unsub] of this.signalUnsubscribers.entries()) {
        if (liveIds.has(appointmentId)) continue;
        try { unsub(); } catch (_) {}
        this.signalUnsubscribers.delete(appointmentId);
        this.approvedAppointments.delete(appointmentId);
        this.handledOffers.delete(appointmentId);
      }
    }, (err) => {
      console.log('[MentiCallManager] Realtime appointments listener error:', err.message);
      this.stopRealtimeIncomingCalls();
      this.setPollingInterval(2000);
    });

    this.realtimeReady = true;
    // Keep a low-frequency fallback poll for resilience.
    this.setPollingInterval(15000);
    console.log('[MentiCallManager] Realtime call notifications enabled');
  }

  processSignalUpdate(appointment, signal) {
    if (!appointment || !appointment.id) return;
    if (!signal || Object.keys(signal).length === 0) return;

    if (this.activeCall && this.activeCall.id === appointment.id) {
      if (signal.answer || signal.userDeclined || signal.callEnded || signal.active === false) {
        this.endCall();
      }
    }

    const offerSdp = signal.offer && (signal.offer.sdp || JSON.stringify(signal.offer));

    if (signal.callEnded || signal.userDeclined || signal.active === false) {
      this.handledOffers.delete(appointment.id);
      return;
    }

    const handledSdp = this.handledOffers.get(appointment.id);
    if (handledSdp && handledSdp === offerSdp) {
      return;
    }

    if (signal.offer && !signal.answer && !this.activeCall) {
      this.onIncomingCall(appointment, signal);
    }
  }

  stopPolling() {
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = null;
    }
    this.stopRealtimeIncomingCalls();
    this.isListening = false;
  }

  async checkForIncomingCalls() {
    if (this.realtimeReady) {
      return;
    }
    try {
      // Build headers with token if available
      const headers = this.buildAuthHeaders();

      // If a call is currently ringing but already answered/ended/declined, stop it.
      if (this.activeCall) {
        const stateRes = await fetch(`/api/video/${this.activeCall.id}/signal`, { headers });
        if (stateRes.ok) {
          const state = await stateRes.json();
          if (state.answer || state.userDeclined || state.callEnded || state.active === false) {
            this.endCall();
          }
        }
      }

      // Fetch approved appointments for this user
      const response = await fetch('/api/counseling/appointments', { headers });
      if (!response.ok) {
        if (response.status === 401) {
          console.log('[MentiCallManager] 401 - Auth token may have expired');
        }
        return;
      }

      const appointments = await response.json();
      const approvedAppointments = appointments.filter(a => a.status === 'approved');

      // Check each approved appointment for incoming signal
      for (const appointment of approvedAppointments) {
        if (this.activeCall) break; // Only handle one call at a time

        const signalResponse = await fetch(`/api/video/${appointment.id}/signal`, { headers });
        if (!signalResponse.ok) continue;

        const signal = await signalResponse.json();
        const offerSdp = signal.offer && (signal.offer.sdp || JSON.stringify(signal.offer));

        // Once a call truly ends, forget it so a future new call can ring again.
        if (signal.callEnded || signal.userDeclined || signal.active === false) {
          this.handledOffers.delete(appointment.id);
          continue;
        }

        // Skip offers we've already accepted/are waiting on, until the answer/end arrives.
        const handledSdp = this.handledOffers.get(appointment.id);
        if (handledSdp && handledSdp === offerSdp) {
          continue;
        }

        // If we have an offer and no active call, we have an incoming call
        if (signal.offer && !signal.answer && !this.activeCall) {
          this.onIncomingCall(appointment, signal);
          break;
        }
      }
    } catch (err) {
      console.error('[MentiCallManager] Polling error:', err);
    }
  }

  async onIncomingCall(appointment, signal) {
    if (this.activeCall) return; // Already handling a call
    
    this.activeCall = appointment;
    this.activeCallSignal = signal;
    this.isRinging = true;

    // Show modal and play ringtone
    this.showIncomingCallModal(appointment);
    this.playRingtone();

    // Auto-decline after 30 seconds
    this.callTimeout = setTimeout(() => {
      if (this.activeCall && this.activeCall.id === appointment.id) {
        this.declineCall();
      }
    }, 30000);
  }

  showIncomingCallModal(appointment) {
    // Remove any existing modal
    const existing = document.getElementById('menti-incoming-call-modal');
    if (existing) existing.remove();

    // Create professional modal
    const modal = document.createElement('div');
    modal.id = 'menti-incoming-call-modal';
    modal.className = 'menti-call-modal menti-call-incoming';
    
    const rawProviderName = String(appointment.providerName || 'Your Counselor').trim();
    const providerName = rawProviderName
      .split(/\s+/)
      .map(part => part ? part.charAt(0).toUpperCase() + part.slice(1).toLowerCase() : '')
      .join(' ');
    const providerInitial = providerName.charAt(0).toUpperCase() || 'P';
    const providerRole = appointment.providerRole
      ? appointment.providerRole.charAt(0).toUpperCase() + appointment.providerRole.slice(1)
      : 'Provider';
    
    modal.innerHTML = `
      <div class="menti-call-backdrop"></div>
      <div class="menti-call-container">
        <div class="menti-call-inner">
          <!-- Avatar -->
          <div class="menti-call-avatar">${providerInitial}</div>
          
          <!-- Caller Info -->
          <div class="menti-call-info">
            <h2 class="menti-call-name">${providerName}</h2>
            <p class="menti-call-role">${providerRole}</p>
            <p class="menti-call-status">Incoming counseling video call</p>
          </div>
          
          <!-- Session Details -->
          <div class="menti-call-details">
            <span class="menti-detail-item">Session ready to connect</span>
          </div>
          
          <!-- Action Buttons -->
          <div class="menti-call-actions">
            <div class="menti-call-action-wrap">
              <button class="menti-call-btn menti-call-decline" id="menti-call-decline" title="Decline call">
                <span class="menti-call-icon"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg></span>
              </button>
              <span class="menti-call-action-label">Decline</span>
            </div>
            <div class="menti-call-action-wrap">
              <button class="menti-call-btn menti-call-accept" id="menti-call-accept" title="Accept call">
                <span class="menti-call-icon"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3" y="6" width="12" height="12" rx="2"/><path d="m15 10 6-3v10l-6-3z"/></svg></span>
              </button>
              <span class="menti-call-action-label">Accept</span>
            </div>
          </div>
          
          <!-- Timer -->
          <div class="menti-call-timer">
            Auto-decline in <span id="menti-call-countdown">30</span>s
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    // Add event listeners
    document.getElementById('menti-call-decline').addEventListener('click', () => this.declineCall());
    document.getElementById('menti-call-accept').addEventListener('click', () => this.acceptCall());

    // Start countdown timer display
    this.updateCountdownDisplay();
  }

  updateCountdownDisplay() {
    const countdownEl = document.getElementById('menti-call-countdown');
    if (!countdownEl) return;

    let remaining = 30;
    const interval = setInterval(() => {
      remaining--;
      if (countdownEl && countdownEl.parentElement) {
        countdownEl.textContent = remaining;
      }
      if (remaining <= 0) {
        clearInterval(interval);
      }
    }, 1000);
  }

  playRingtone() {
    // Use a familiar telephone cadence: two bright dual-tone bursts, then a pause.
    try {
      const audioContext = this.audioContext || (this.audioContext = new (window.AudioContext || window.webkitAudioContext)());
      if (audioContext.state === 'suspended') audioContext.resume();

      const playBurst = (startTime, duration = 0.42) => {
        const gain = audioContext.createGain();
        gain.connect(audioContext.destination);
        gain.gain.setValueAtTime(0.0001, startTime);
        gain.gain.exponentialRampToValueAtTime(0.22, startTime + 0.025);
        gain.gain.setValueAtTime(0.22, startTime + duration - 0.06);
        gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);

        [440, 480].forEach(freq => {
          const osc = audioContext.createOscillator();
          osc.type = 'sine';
          osc.frequency.value = freq;
          osc.connect(gain);
          osc.start(startTime);
          osc.stop(startTime + duration + 0.02);
        });
      };

      const playPattern = () => {
        if (!this.isRinging) return;
        const now = audioContext.currentTime + 0.02;
        playBurst(now);
        playBurst(now + 0.62);
      };

      if (!this.ringtoneInterval) {
        playPattern();
        this.ringtoneInterval = setInterval(() => {
          playPattern();
        }, 3000);
      }
    } catch (err) {
      console.log('[MentiCallManager] Ringtone error (Web Audio not available):', err.message);
    }
  }

  stopRingtone() {
    if (this.ringtoneInterval) {
      clearInterval(this.ringtoneInterval);
      this.ringtoneInterval = null;
    }
    this.isRinging = false;
  }

  openCallTab(appointment) {
    if (!appointment || !appointment.id) return;
    if (this.userToken) {
      try {
        localStorage.setItem('menti_user_token', this.userToken);
      } catch (_) {}
      window.__mentiUserToken = this.userToken;
    }
    const token = this.userToken || '';
    const tokenParam = token ? `&token=${encodeURIComponent(token)}` : '';
    const videoCallUrl = `/video/${appointment.id}?role=user${tokenParam}`;
    window.open(videoCallUrl, `menti-call-${appointment.id}`, 'width=1280,height=720');
  }

  async acceptCall() {
    if (!this.activeCall) return;

    const acceptedCall = this.activeCall;

    this.stopRingtone();
    const modal = document.getElementById('menti-incoming-call-modal');
    if (modal) modal.remove();

    // Clear timeout
    if (this.callTimeout) {
      clearTimeout(this.callTimeout);
      this.callTimeout = null;
    }

    // Emit custom event for page to handle
    window.dispatchEvent(new CustomEvent('mentiCallAccepted', { 
      detail: { appointment: acceptedCall } 
    }));

    let handledByPage = false;

    // If page defines onMentiCallAccepted, call it
    if (typeof window.onMentiCallAccepted === 'function') {
      try {
        window.onMentiCallAccepted(acceptedCall);
        handledByPage = true;
      } catch (err) {
        console.log('[MentiCallManager] onMentiCallAccepted handler failed, using fallback:', err.message);
      }
    }

    // Global fallback so accepting from any page always opens the call room.
    if (!handledByPage) {
      this.openCallTab(acceptedCall);
    }

    // Log call accepted
    try {
      const headers = this.buildAuthHeaders();

      await fetch('/api/call-history', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          appointmentId: acceptedCall.id,
          action: 'accepted',
          timestamp: new Date().toISOString()
        })
      });
    } catch (err) {
      console.log('[MentiCallManager] Error logging accepted call:', err.message);
    }

    // Remember this offer as handled so we don't re-ring while the new tab
    // is still setting up media and hasn't posted the answer signal yet.
    const offerSdp = this.activeCallSignal && this.activeCallSignal.offer && (this.activeCallSignal.offer.sdp || JSON.stringify(this.activeCallSignal.offer));
    if (offerSdp) {
      this.handledOffers.set(acceptedCall.id, offerSdp);
    }

    this.activeCall = null;
    this.activeCallSignal = null;
  }

  async declineCall() {
    if (!this.activeCall) return;

    this.stopRingtone();
    const modal = document.getElementById('menti-incoming-call-modal');
    if (modal) modal.remove();

    // Clear timeout
    if (this.callTimeout) {
      clearTimeout(this.callTimeout);
      this.callTimeout = null;
    }

    // Send decline signal to backend
    try {
      const headers = this.buildAuthHeaders();

      await fetch(`/api/video/${this.activeCall.id}/signal`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ userDeclined: true, timestamp: new Date().toISOString() })
      });

      // Log call decline in history
      await fetch('/api/call-history', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          appointmentId: this.activeCall.id,
          action: 'declined',
          timestamp: new Date().toISOString()
        })
      });
    } catch (err) {
      console.log('[MentiCallManager] Error declining call:', err.message);
    }

    this.activeCall = null;
  }

  async endCall() {
    this.stopRingtone();
    this.activeCall = null;
    const modal = document.getElementById('menti-incoming-call-modal');
    if (modal) modal.remove();
  }
}

// Initialize the call manager globally
window.mentiCallManager = new MentiCallManager();

// Make it accessible from other scripts
window.MentiCallManager = MentiCallManager;
