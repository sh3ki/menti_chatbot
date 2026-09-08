# Menti Video Calling System - Complete Implementation Guide

## Overview

The Menti video calling system has been redesigned to match **Google Meet/Zoom** by implementing the exact architecture from the yunafied system. Video calls now open in **separate browser tabs** (not inline on the same page) providing a professional, immersive experience.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      Menti Platform                         │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Provider Dashboard          User Dashboard                 │
│  (provider_video_call.html)  (counseling_appointments.html) │
│         │                              │                    │
│         │ "Call user"                  │ Receives incoming   │
│         │                              │ call notification   │
│         ↓                              ↓                    │
│    startCall()              onMentiCallAccepted()           │
│    Sends OFFER              handler triggered              │
│         │                              │                    │
│         └──────────────────┬───────────┘                    │
│                            │                                 │
│                   window.open() NEW TAB                     │
│                            │                                 │
│         ┌──────────────────┴───────────┐                    │
│         ↓                              ↓                    │
│  /video/{id}?role=provider  /video/{id}?role=user         │
│  (video_call_room.html)     (video_call_room.html)         │
│         │                              │                    │
│    ┌────┴─────────────────────────────┴────┐               │
│    │  WebRTC RTCPeerConnection              │               │
│    │  - Offer/Answer exchange               │               │
│    │  - ICE candidate polling               │               │
│    │  - Media streams (video + audio)       │               │
│    └────┬─────────────────────────────────┬─┘               │
│         │                                  │                 │
│         └──────┬───────────────────────────┘                |
│                │                                              │
│          Connection Established                              │
│          Professional Video Conference                       │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Call Flow - Step by Step

### 1. Provider Initiates Call

**Location**: Provider Dashboard (`provider_video_call.html`)

```
1. Provider clicks "Call user" button
2. JavaScript calls startCall(appointmentId)
3. Opens new browser tab: window.open('/video/{id}?role=provider')
4. Simultaneously sends OFFER to /api/video/{id}/signal endpoint
```

**Provider's startCall() Function**:
```javascript
async function startCall(id) { 
    // Open video call room in separate tab
    const videoCallUrl = `/video/${id}?role=provider`;
    window.open(videoCallUrl, `menti-call-${id}`, 'width=1280,height=720');
    
    // Generate and send OFFER
    const stream = await navigator.mediaDevices.getUserMedia({ 
        video: true, audio: true 
    });
    const pc = new RTCPeerConnection({ iceServers: [STUN/TURN servers] });
    stream.getTracks().forEach(t => pc.addTrack(t, stream));
    
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    
    // Send OFFER to backend
    await fetch(`/api/video/${id}/signal`, {
        method: 'POST',
        body: JSON.stringify({ offer })
    });
}
```

### 2. User Receives Incoming Call

**Location**: Any page the user is on (call-manager.js monitors across all pages)

```
1. Global call-manager.js polls every 2 seconds
2. Detects OFFER signal on approved appointment
3. Shows professional incoming call modal
4. Plays phone ringtone (Web Audio API)
5. Displays provider name, role, session time
6. Shows Accept/Decline buttons with 30-second auto-decline
```

**Incoming Call Modal** (styled after yunafied):
- Pulsing avatar with provider initials
- Provider name and role (Psychiatrist/Psychologist)
- Session details
- Accept button (green, circular, 64px)
- Decline button (red outline, circular, 64px)
- Countdown timer to auto-decline

### 3. User Accepts Call

**Location**: Any page (triggers from incoming call modal)

```
1. User clicks Accept button on modal
2. acceptCall() method is triggered in call-manager.js
3. Calls window.onMentiCallAccepted(appointment)
4. This opens new tab: window.open('/video/{id}?role=user')
```

**User's onMentiCallAccepted Handler**:
```javascript
window.onMentiCallAccepted = (appointment) => {
    const videoCallUrl = `/video/${appointment.id}?role=user`;
    window.open(videoCallUrl, `menti-call-${appointment.id}`, 'width=1280,height=720');
};
```

### 4. Both Participants in Video Call Room

**Location**: New browser tab at `/video/{appointmentId}?role=user|provider`

**Video Call Room Flow**:

#### A. Page Loads (video_call_room.html)

```javascript
1. Determine role from URL query parameter (?role=provider or ?role=user)
2. Get user's Firebase ID token from window.__mentiUserToken
3. Load available camera and microphone devices
4. Build ICE servers list (Google STUN + Free TURN relay)
```

#### B. Provider's Flow (Creates Offer)

```javascript
// Step 1: Get local media stream
const stream = await navigator.mediaDevices.getUserMedia({
    video: {
        width: { ideal: 1280 },
        height: { ideal: 720 },
        frameRate: { ideal: 30 }
    },
    audio: {
        echoCancellation: true,
        noiseSuppression: true,
        sampleRate: 48000
    }
});
localStream = stream;

// Step 2: Create RTCPeerConnection with ICE servers
pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });

// Step 3: Add local stream tracks
stream.getTracks().forEach(track => pc.addTrack(track, stream));

// Step 4: Handle remote track
pc.ontrack = (event) => {
    remoteVideo.srcObject = event.streams[0];
};

// Step 5: Handle ICE candidates
pc.onicecandidate = async (event) => {
    if (!event.candidate) return;
    
    // Send ICE candidate to backend
    await fetch(`/api/video/${roomToken}/signal`, {
        method: 'POST',
        body: JSON.stringify({
            iceCandidates: [event.candidate.toJSON()]
        })
    });
};

// Step 6: Handle connection state
pc.onconnectionstatechange = () => {
    if (pc.connectionState === 'connected') {
        // Set bitrate to 2.5 Mbps for optimal quality
        pc.getSenders().forEach(sender => {
            if (sender.track?.kind === 'video') {
                const params = sender.getParameters();
                params.encodings[0].maxBitrate = 2_500_000;
                params.encodings[0].maxFramerate = 30;
                sender.setParameters(params);
            }
        });
        startCallTimer();
    }
};

// Step 7: Create and send OFFER
const offer = await pc.createOffer();
await pc.setLocalDescription(offer);

await fetch(`/api/video/${roomToken}/signal`, {
    method: 'POST',
    body: JSON.stringify({ offer })
});

// Step 8: Poll for ANSWER
await pollForAnswer();
```

#### C. User's Flow (Waits for Offer, Sends Answer)

```javascript
// Step 1: Poll for OFFER from provider
const signal = await pollForOffer(); // waits for signal.offer

// Step 2: Get local media stream (same as provider)
const stream = await navigator.mediaDevices.getUserMedia({...});
localStream = stream;

// Step 3: Create RTCPeerConnection with ICE servers
pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });

// Step 4: Add local stream tracks
stream.getTracks().forEach(track => pc.addTrack(track, stream));

// Step 5: Set remote description (provider's OFFER)
await pc.setRemoteDescription(new RTCSessionDescription(signal.offer));

// Step 6: Add any received ICE candidates from provider
for (const candidate of signal.providerCandidates || []) {
    try {
        await pc.addIceCandidate(new RTCIceCandidate(candidate));
    } catch (err) {
        console.error('Error adding ICE candidate:', err);
    }
}

// Step 7: Create ANSWER and send it
const answer = await pc.createAnswer();
await pc.setLocalDescription(answer);

await fetch(`/api/video/${roomToken}/signal`, {
    method: 'POST',
    body: JSON.stringify({ answer })
});

// Step 8: Start call timer (connection already in progress)
startCallTimer();
```

#### D. Media Stream Setup

Once OFFER and ANSWER are exchanged and ICE candidates collected:

```
Provider Tab                  Backend (Firestore)              User Tab
     │                                │                          │
     ├──────── OFFER ───────────────>│                           │
     │                                ├─── Polling ──────────>   │
     │                                │<───── Gets OFFER ────    │
     │                                │                      │   │
     │                                │<───── ANSWER ────────┤   │
     │<───────────────────── ANSWER ──┤                          │
     │                                │                          │
     ├─── ICE Candidates 1 ────────>│                           │
     │     (every new candidate)       ├─── Polling ──────────>   │
     │                                │<─ Gets candidates ────    │
     │                                │                          │
     │<─────────────────── ICE Candidates 2 ─────────────────┤   │
     │     (every new candidate)                                 │
     │                                                            │
     ├──────────────────────── Media Stream ─────────────────>  │
     │<──────────────────────── Media Stream ──────────────────  │
     │                                                            │
     └───────────── FULL VIDEO CONFERENCE ESTABLISHED ──────────┘
```

### 5. Video Conference Active

**Video Call Room UI**:

```
┌──────────────────────────────────────────────────────┐
│                   Menti Video Call                   │
│  Connection: Connected (green indicator)  00:05     │
│  Camera: [Camera 1 ▼]  Mic: [Microphone 1 ▼]        │
├──────────────────────────────────────────────────────┤
│                                                       │
│                                                       │
│               Remote Video (Full Screen)            │
│                                                       │
│                                                       │
│                    ┌──────────┐                       │
│                    │  Local   │  ┌─ Microphone Toggle
│                    │  Video   │─┤─ Camera Toggle
│                    │ PIP      │  └─ Hangup
│                    └──────────┘                       │
│                                                       │
└──────────────────────────────────────────────────────┘
        ▲                                   ▲
        │                                   │
   320x180px               Bottom-right corner
(picture-in-picture)       with 24px padding
  Green border             Box shadow
```

**Call Controls**:
- 🎤 Microphone Toggle - Mute/Unmute audio
- 📹 Camera Toggle - Turn video off/on
- ☎️ Hangup Button - End call and close tab

**Information Display**:
- Call Timer: MM:SS format, updates every second
- Connection State: "Connecting" → "Connected" → "Disconnected"
- Device Selectors: Switch camera and microphone during call

### 6. Call Ends

**When User Clicks Hangup**:

```javascript
async function endCall() {
    // Stop all media tracks
    if (localStream) {
        localStream.getTracks().forEach(track => track.stop());
    }
    
    // Close peer connection
    if (pc) {
        pc.close();
    }
    
    // Clear video elements
    if (localVideo) localVideo.srcObject = null;
    if (remoteVideo) remoteVideo.srcObject = null;
    
    // Update backend with call status
    try {
        await fetch(`/api/video/${roomToken}/status`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${window.__mentiUserToken}` },
            body: JSON.stringify({ status: 'ended' })
        });
    } catch (err) {
        console.error('Error updating call status:', err);
    }
    
    // Close tab after 1 second
    setTimeout(() => window.close(), 1000);
}
```

## Signaling Protocol (REST-based)

### Endpoint: `GET /api/video/<appointment_id>/signal`

**Purpose**: Retrieve current signal state (OFFER, ANSWER, ICE candidates)

**Response**:
```json
{
    "offer": {
        "type": "offer",
        "sdp": "v=0\r\no=- ... (full SDP)"
    },
    "answer": {
        "type": "answer",
        "sdp": "v=0\r\no=- ... (full SDP)"
    },
    "providerCandidates": [
        {
            "candidate": "candidate:...",
            "sdpMLineIndex": 0,
            "sdpMid": "video"
        },
        // ... more ICE candidates
    ],
    "userCandidates": [
        // ... user's ICE candidates
    ],
    "active": true,
    "updatedAt": "2024-01-15T10:30:45Z"
}
```

### Endpoint: `POST /api/video/<appointment_id>/signal`

**Purpose**: Send OFFER, ANSWER, or ICE candidates

**Request Body Options**:

```json
{
    "offer": {
        "type": "offer",
        "sdp": "v=0\r\no=- ..."
    }
}
```

or

```json
{
    "answer": {
        "type": "answer",
        "sdp": "v=0\r\no=- ..."
    }
}
```

or

```json
{
    "iceCandidates": [
        {
            "candidate": "candidate:...",
            "sdpMLineIndex": 0,
            "sdpMid": "video"
        }
    ]
}
```

or

```json
{
    "userDeclined": true
}
```

**Response**:
```json
{
    "success": true
}
```

## ICE Servers Configuration

**Google STUN Servers** (Discovery):
- `stun:stun.l.google.com:19302`
- `stun:stun1.l.google.com:19302`
- `stun:stun2.l.google.com:19302`

**Free TURN Relay Servers** (NAT Traversal):
- `turn:openrelay.metered.ca:80`
- `turn:openrelay.metered.ca:443`
- `turn:openrelay.metered.ca:443?transport=tcp`

**Username/Password**: 
- Username: `openrelayproject`
- Credential: `openrelayproject`

**Note**: Free TURN server has bandwidth limits. For production, consider private TURN server.

## Media Constraints

**Video**:
- Minimum resolution: 640x480
- Ideal resolution: 1280x720
- Ideal frame rate: 30 fps
- Facing mode: User (front camera on mobile)

**Audio**:
- Echo cancellation: Enabled
- Noise suppression: Enabled
- Sample rate: 48000 Hz
- Default bitrate: 64 kbps (auto-adjusting)

**Overall Bitrate**:
- Maximum video bitrate: 2.5 Mbps (when connected)
- Maximum frame rate: 30 fps
- Adapts to available bandwidth

## Error Handling

### No Media Devices
```
Alert: "Unable to access camera/microphone. Check permissions."
Action: User must grant permissions or try different device
```

### Connection Failed
```
Alert: "Connection lost. Ending call."
Action: Automatic call termination and tab closure
```

### Timeout Waiting for Offer/Answer
```
After 30 seconds (user) or 60 seconds (provider)
Action: Automatic disconnect and error message
```

### ICE Candidate Errors
```
Non-fatal - logged but call continues
Reason: Some candidates may fail in certain network conditions
```

## Browser Compatibility

✅ Chrome/Chromium 60+
✅ Firefox 55+
✅ Safari 11+
✅ Edge 79+
❌ Internet Explorer (not supported)

**Mobile Support**:
✅ iOS Safari 11+ (with responsive design)
✅ Android Chrome/Firefox

## Testing Checklist

### Provider Flow
- [ ] Click "Call user" button on provider dashboard
- [ ] New tab opens with `/video/{id}?role=provider`
- [ ] Camera/microphone permission prompt appears
- [ ] Local video displays in picture-in-picture
- [ ] Connection state shows "Calling"
- [ ] User receives incoming call notification
- [ ] After user accepts, connection state changes to "Connected"
- [ ] Remote video displays (provider sees user)
- [ ] Mic/Camera toggle buttons work
- [ ] Call timer runs accurately
- [ ] Device dropdown allows switching camera/mic
- [ ] Hangup button closes tab

### User Flow
- [ ] Accept incoming call from any page
- [ ] New tab opens with `/video/{id}?role=user`
- [ ] Camera/microphone permission prompt appears
- [ ] Local video displays in picture-in-picture
- [ ] Wait a few seconds for connection
- [ ] Connection state changes to "Connected"
- [ ] Remote video displays (user sees provider)
- [ ] Mic/Camera toggle buttons work
- [ ] Call timer shows elapsed time
- [ ] Hangup button closes tab

### Network Scenarios
- [ ] Same Wi-Fi network
- [ ] Different networks (across internet)
- [ ] Mobile data to Wi-Fi
- [ ] Low bandwidth (throttle in DevTools)
- [ ] High latency (throttle in DevTools)

### Edge Cases
- [ ] Decline incoming call → Closes modal
- [ ] Auto-decline after 30 seconds → Closes modal
- [ ] Provider calls non-existent user → Error message
- [ ] User accepts after decline → Reconnection works
- [ ] Close tab during call → Backend notified
- [ ] Network disconnect → "Connection lost" alert

## Deployment Notes

1. **SSL/HTTPS**: Required for getUserMedia() and TURN server
2. **Firebase ID Token**: Must be available on all pages
3. **CORS**: Ensure `/api/video` endpoints accept CORS if needed
4. **Database**: Ensure Firestore `video_signals` and `call_history` collections exist
5. **Permissions**: Users must grant camera/microphone permissions

## Performance Metrics

**Ideal Connection Time**:
- OFFER sent: ~0.5s after provider clicks "Call"
- ANSWER sent: ~2-3s after user accepts
- Full connection: ~5-10 seconds

**Bitrate Usage**:
- Video (1280x720@30fps): 1.5-2.5 Mbps
- Audio: 64 kbps
- Total: ~1.6-2.6 Mbps

**Latency**:
- Same network: <50ms
- Different networks: 20-200ms (depending on distance/ISP)

## Known Limitations

1. **REST Polling Delay**: 500ms polling interval (consider WebSocket upgrade)
2. **Single Peer**: 1-to-1 only (no group conferences yet)
3. **No Screen Sharing**: Not implemented (can be added)
4. **No Recording**: Call recording not implemented (can be added)
5. **Free TURN Bandwidth**: Limited by public relay server

## Future Enhancements

1. **WebSocket Signaling**: Real-time signaling instead of polling
2. **Private TURN Server**: Dedicated relay for reliability
3. **Screen Sharing**: Share provider's screen for demonstrations
4. **Call Recording**: Record session for review
5. **Group Conferences**: Support 3+ participants
6. **Mobile App**: Native iOS/Android implementation
7. **Virtual Backgrounds**: Blur or replace background
8. **Chat During Call**: Text communication in video interface

---

**Implementation Date**: January 2024
**Based On**: Yunafied video conferencing system
**Architecture**: Modeled after Google Meet/Zoom
