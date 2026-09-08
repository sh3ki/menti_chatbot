(function () {
    const modeKey = 'menti_background_music_mode';
    const trackKey = 'menti_background_music_url';
    let audio = null;
    let tracks = [];

    function chooseRandomTrack() {
        if (!tracks.length) return;
        const track = tracks[Math.floor(Math.random() * tracks.length)];
        localStorage.setItem(trackKey, track.url);
        localStorage.removeItem('menti_background_music_position');
        localStorage.removeItem('menti_background_music_saved_at');
        playTrack(track.url, false);
    }

    function playTrack(url, loop) {
        if (!url) return;
        if (!audio) audio = new Audio();
        audio.src = url;
        audio.loop = loop;
        audio.volume = 0.25;
        audio.onended = loop ? null : chooseRandomTrack;
        const savedUrl = localStorage.getItem(trackKey);
        const savedPosition = parseFloat(localStorage.getItem('menti_background_music_position') || '0');
        const savedAt = parseInt(localStorage.getItem('menti_background_music_saved_at') || '0', 10);
        audio.onloadedmetadata = () => {
            if (savedUrl === url && Number.isFinite(savedPosition) && savedPosition > 0) {
                const elapsed = savedAt ? Math.max(0, (Date.now() - savedAt) / 1000) : 0;
                audio.currentTime = (savedPosition + elapsed) % audio.duration;
            }
        };
        audio.play().catch(() => {});
    }

    function savePlaybackPosition() {
        if (audio && Number.isFinite(audio.currentTime)) {
            localStorage.setItem('menti_background_music_position', String(audio.currentTime));
            localStorage.setItem('menti_background_music_saved_at', String(Date.now()));
        }
    }

    window.addEventListener('pagehide', savePlaybackPosition);

    fetch('/api/music')
        .then(response => response.json())
        .then(data => {
            tracks = Object.values(data.music || {}).flat();
            const mode = localStorage.getItem(modeKey) || 'off';
            if (mode === 'shuffle') {
                const savedUrl = localStorage.getItem(trackKey);
                if (savedUrl && tracks.some(track => track.url === savedUrl)) {
                    playTrack(savedUrl, false);
                } else {
                    chooseRandomTrack();
                }
            } else if (mode === 'loop') {
                const url = localStorage.getItem(trackKey);
                playTrack(url, true);
            }
        })
        .catch(() => {});
})();
