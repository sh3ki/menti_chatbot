# Menti Video Call Process

This document explains how a counseling video call moves through the Menti system, from an approved appointment to call cleanup and history storage.

## 1. Main components

| Component | Responsibility |
| --- | --- |
| `templates/provider_video_call.html` | Provider dashboard, appointment actions, and **Call user** button |
| `assets/js/call-manager.js` | Runs on user-facing pages; detects incoming provider calls and shows the accept/decline dialog |
| `templates/video_call_room.html` | Separate browser-tab call room and WebRTC client |
| `app.py` | Authentication, room access, signaling API, status API, SSE streams, and call-history persistence |
| Firestore `appointments` | Stores the approved counseling appointment and participant IDs |
| Firestore `video_signals` | Temporary signaling state for one appointment/call |
| Firestore `call_history` | Initiated, accepted, declined, and completed call events |

## 2. Required starting state

The appointment must already be approved. The appointment document contains the user and provider IDs, and the caller must match one of those IDs:

- Provider authentication uses `session['provider_uid']`.
- User authentication uses the Firebase user token/session resolved by `_current_user_uid()`.
- Every video route checks that the authenticated participant belongs to the appointment.

An unapproved appointment cannot create or read signaling data.

## 3. End-to-end flow

```text
Provider dashboard
      |
      |  Opens /video/<appointment_id>?role=provider
      v
Provider room creates WebRTC offer
      |
      |  POST /api/video/<id>/signal { offer }
      v
Firestore video_signals
      |
      |  User call manager receives SSE/Firebase update
      |  (or fallback polling)
      v
Incoming-call modal on user's current page
      |
      |  Accept -> opens /video/<id>?role=user
      v
User room creates WebRTC answer
      |
      |  POST /api/video/<id>/signal { answer }
      v
Provider applies answer; both sides exchange ICE candidates
      |
      v
Direct WebRTC audio/video connection
      |
      |  Hang up, browser close, decline, or connection failure
      v
Signal marked ended; duration and call history are persisted
```

## 4. Provider starts the call

1. The provider opens the provider dashboard at `/providers/video-call`.
2. The dashboard loads approved appointments through the provider appointment APIs.
3. Clicking **Call user** opens a new tab/window:

   ```text
   /video/<appointment_id>?role=provider
   ```

4. Flask serves `video_call_room.html` after validating provider access.
5. The provider room:
   - Enumerates available camera and microphone devices.
   - Requests permission with `navigator.mediaDevices.getUserMedia()`.
   - Creates an `RTCPeerConnection` with STUN/TURN ICE servers.
   - Adds the local audio and video tracks.
   - Creates an SDP offer and sets it as the local description.
   - Sends the offer to `POST /api/video/<id>/signal`.
6. The server stores the offer in `video_signals` and logs an `initiated` event in `call_history`.

The provider sees the **Calling/Ringing** phase while the system waits for the user answer.

## 5. User receives and accepts the call

`assets/js/call-manager.js` is loaded on user-facing pages, including the chat page. It uses this order of preference:

1. `GET /api/video/incoming-stream` using Server-Sent Events (SSE).
2. Firebase Firestore listeners when the SSE path is unavailable.
3. HTTP polling as a final fallback.

Only approved appointments belonging to the current user are watched. When a signal contains a new provider offer and no answer yet, the manager:

- Shows the incoming counseling call modal.
- Plays a browser-generated ringtone.
- Starts a 30-second auto-decline timer.

### Accept

Accepting the modal:

1. Stops the ringtone and removes the modal.
2. Opens `/video/<appointment_id>?role=user` in a separate tab.
3. Logs an `accepted` event through `POST /api/call-history`.
4. Remembers the handled offer so it does not ring repeatedly while the room initializes.

The user room waits for the offer, obtains local media, creates an `RTCPeerConnection`, applies the provider offer, creates an SDP answer, and sends:

```json
{ "answer": { "type": "answer", "sdp": "..." } }
```

### Decline or timeout

Declining, or allowing the 30-second timer to expire, sends `userDeclined: true` to the signaling endpoint and logs a `declined` event. The signal becomes inactive and the provider is notified through the same signaling channel.

## 6. WebRTC negotiation and media

The server does not carry the live audio/video stream. It only relays WebRTC setup information:

- Provider SDP offer
- User SDP answer
- Provider ICE candidates
- User ICE candidates
- End/decline state

Each participant sends ICE candidates as they are generated. They are stored in the `providerCandidates` or `userCandidates` arrays in `video_signals`. The browser uses the configured STUN/TURN servers to find a working network path.

When the peer connection reaches `connected`:

- The remote stream is attached to `#remote-video`.
- The local stream is attached to `#local-video`.
- The call timer starts.
- The room changes from the connecting overlay to the active call UI.

The call room also supports:

- Microphone mute/unmute by enabling or disabling the local audio track.
- Camera on/off by enabling or disabling the local video track.
- Camera and microphone replacement during the call.
- ICE reconnection/restart attempts when the connection stalls.
- A video bitrate cap of approximately 2.5 Mbps and 30 FPS when connected.

## 7. Realtime status and fallbacks

The provider dashboard listens to `/providers/api/video-status-stream` through SSE. This updates the appointment card with states such as `initiated`, `connected`, `declined`, or `completed`.

If the stream disconnects, the dashboard falls back to status requests against:

```text
GET /api/video/<appointment_id>/status
```

The user call manager similarly falls back from SSE/Firebase listeners to HTTP polling. The call room falls back from Firestore `onSnapshot` signaling to polling approximately every 1.2 seconds.

## 8. Ending and cleanup

The call can end when either participant:

- Confirms the **End call** action.
- Closes the call tab.
- Experiences a failed/disconnected peer connection.
- Receives a remote ended/declined signal.

The room sends `callEnded: true`, the ending role, and the calculated duration to the signaling endpoint. It also posts `status: ended` to the status endpoint. Cleanup then:

1. Stops signal listeners and polling.
2. Stops the timer.
3. Stops local media tracks.
4. Closes the `RTCPeerConnection`.
5. Clears both video elements.
6. Marks the room ended and closes the tab after a short ended-state display.

The `beforeunload` handler attempts to send an end signal with `sendBeacon` when a tab is closed unexpectedly.

## 9. Backend endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /video/<room_token>` | Authorizes and renders the call room |
| `GET /api/video/<appointment_id>/signal` | Reads current signaling state |
| `POST /api/video/<appointment_id>/signal` | Writes offer, answer, ICE candidates, decline, or end state |
| `GET /api/video/<appointment_id>/status` | Returns effective call status and history summary |
| `POST /api/video/<appointment_id>/status` | Marks a call ended and persists duration |
| `GET /api/video/incoming-stream` | User incoming-call SSE stream |
| `GET /providers/api/video-status-stream` | Provider status SSE stream |
| `POST /api/call-history` | Adds an explicit call event |

## 10. Firestore records

### `video_signals/{appointment_id}`

The document is a per-appointment signaling mailbox. Important fields include `offer`, `answer`, `providerCandidates`, `userCandidates`, `active`, `userDeclined`, `callEnded`, `endedBy`, `connectedAt`, `endedAt`, and `updatedAt`.

### `call_history`

Each record identifies the appointment, user, provider, action, timestamp, optional duration, and the actor that created the record. The completed event is updated or created with the final duration.

### `appointments/{appointment_id}`

On completion, the appointment receives `callDurationSeconds`, `callEndedAt`, `callEndedBy`, and `updatedAt`.

## 11. Troubleshooting checklist

- Verify the appointment is `approved` and contains the correct user/provider IDs.
- Verify the browser has camera and microphone permission.
- Check that the user is authenticated before the call manager starts listening.
- Check `video_signals/{appointment_id}` for an offer, answer, and ICE candidates.
- If realtime updates are missing, inspect SSE/Firebase errors and confirm polling fallback is active.
- If negotiation succeeds but media does not connect, check STUN/TURN reachability and browser console WebRTC errors.
- Check `call_history` and the appointment document after ending the call to confirm duration persistence.

## Source files

- `app.py`
- `assets/js/call-manager.js`
- `templates/provider_video_call.html`
- `templates/video_call_room.html`
- `assets/css/call-ui.css`
