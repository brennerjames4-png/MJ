// --- Tab switching ---
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.tab).classList.add('active');
    });
});

// --- Helpers ---
async function api(url, opts = {}) {
    const resp = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    });
    if (!resp.ok) throw new Error(`API error ${resp.status}`);
    return resp.json();
}

function songCard(track, actions = '') {
    return `
        <div class="song-card" data-track-id="${track.id || ''}">
            ${track.album_image ? `<img src="${track.album_image}" alt="">` : ''}
            <div class="song-info">
                <div class="name">${track.name || track.track_name || ''}</div>
                <div class="artist">${track.artist || track.artist_name || ''}</div>
                ${track.meta ? `<div class="meta">${track.meta}</div>` : ''}
                ${track.spotify_url ? `<a class="spotify-link" href="${track.spotify_url}" target="_blank">Open in Spotify</a>` : ''}
            </div>
            <div class="song-actions">${actions}</div>
        </div>
    `;
}

// --- Load feed ---
async function loadFeed() {
    const el = document.getElementById('feed-list');
    try {
        const songs = await api('/api/shared');
        if (songs.length === 0) {
            el.innerHTML = '<p class="loading">No shared songs yet. Share one!</p>';
            return;
        }
        el.innerHTML = songs.map(s => {
            const isMine = s.from_user_id === MY_ID;
            const direction = isMine ? `You sent` : `From ${s.from_name}`;
            const meta = `${direction} ${s.message ? '· "' + s.message + '"' : ''} · ${new Date(s.created_at).toLocaleDateString()}`;
            const likeActive = s.my_reaction === 'like' ? 'active' : '';
            const dislikeActive = s.my_reaction === 'dislike' ? 'active' : '';
            return songCard(
                { ...s, name: s.track_name, artist: s.artist_name, meta },
                `<button class="reaction-btn ${likeActive}" onclick="react(${s.id}, 'like', this)" title="Like">&#x1F44D;</button>
                 <button class="reaction-btn ${dislikeActive}" onclick="react(${s.id}, 'dislike', this)" title="Dislike">&#x1F44E;</button>`
            );
        }).join('');
    } catch (e) {
        el.innerHTML = '<p class="loading">Could not load feed.</p>';
    }
}

async function react(songId, reaction, btn) {
    await api('/api/react', {
        method: 'POST',
        body: JSON.stringify({ shared_song_id: songId, reaction }),
    });
    // Toggle UI
    const parent = btn.parentElement;
    parent.querySelectorAll('.reaction-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}

// --- Search & share ---
let selectedTrack = null;

document.getElementById('search-btn').addEventListener('click', async () => {
    const q = document.getElementById('search-input').value.trim();
    if (!q) return;
    const results = await api(`/api/search?q=${encodeURIComponent(q)}`);
    document.getElementById('search-results').innerHTML = results.map(t =>
        songCard(t, `<button class="share-btn" onclick='selectTrack(${JSON.stringify(t).replace(/'/g, "&#39;")})'>Share</button>`)
    ).join('');
});

document.getElementById('search-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('search-btn').click();
});

window.selectTrack = async function(track) {
    selectedTrack = track;
    document.getElementById('share-form').classList.remove('hidden');
    // Load users
    const users = await api('/api/users');
    const sel = document.getElementById('share-to');
    sel.innerHTML = users.map(u => `<option value="${u.id}">${u.display_name}</option>`).join('');
    if (users.length === 0) sel.innerHTML = '<option disabled>No other users connected yet</option>';
};

document.getElementById('send-btn').addEventListener('click', async () => {
    if (!selectedTrack) return;
    const to = document.getElementById('share-to').value;
    const msg = document.getElementById('share-message').value;
    await api('/api/share', {
        method: 'POST',
        body: JSON.stringify({
            to_user_id: to,
            track_id: selectedTrack.id,
            track_name: selectedTrack.name,
            artist_name: selectedTrack.artist,
            album_image: selectedTrack.album_image,
            preview_url: selectedTrack.preview_url,
            spotify_url: selectedTrack.spotify_url,
            message: msg,
        }),
    });
    document.getElementById('share-form').classList.add('hidden');
    document.getElementById('share-message').value = '';
    selectedTrack = null;
    alert('Song shared!');
    // Switch to feed
    document.querySelector('[data-tab="feed"]').click();
    loadFeed();
});

// --- Compare ---
async function loadCompareUsers() {
    try {
        const users = await api('/api/users');
        const sel = document.getElementById('compare-user');
        sel.innerHTML = users.map(u => `<option value="${u.id}">${u.display_name}</option>`).join('');
        if (users.length === 0) sel.innerHTML = '<option disabled>No other users to compare with</option>';
    } catch (e) {}
}

document.getElementById('compare-btn').addEventListener('click', async () => {
    const otherId = document.getElementById('compare-user').value;
    if (!otherId) return;
    try {
        const data = await api(`/api/compare/${otherId}`);
        document.getElementById('compare-results').classList.remove('hidden');
        document.getElementById('compat-number').textContent = data.compatibility_score;
        document.getElementById('shared-artists').innerHTML = data.shared_artists.length
            ? data.shared_artists.map(a => `<li>${a}</li>`).join('')
            : '<li>No shared artists found</li>';
        document.getElementById('my-genres').innerHTML = data.my_top_genres.map(g => `<li>${g}</li>`).join('');
        document.getElementById('their-genres').innerHTML = data.their_top_genres.map(g => `<li>${g}</li>`).join('');
    } catch (e) {
        alert('Could not compare. Make sure both users are connected.');
    }
});

// --- My top tracks ---
async function loadTopTracks() {
    const el = document.getElementById('top-tracks-list');
    try {
        const tracks = await api('/api/me/top-tracks');
        el.innerHTML = tracks.length
            ? tracks.map((t, i) => songCard({ ...t, meta: `#${i + 1}` })).join('')
            : '<p class="loading">No top tracks data yet. Listen to more music!</p>';
    } catch (e) {
        el.innerHTML = '<p class="loading">Could not load top tracks.</p>';
    }
}

// --- Init ---
loadFeed();
loadTopTracks();
loadCompareUsers();
