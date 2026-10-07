// MonitorIT Dashboard Controller

let ws = null;
let activeProfile = "sample_profile";
let loadedHosts = [];
let customNames = {};
let selectedHostIp = null;

let slot1Ip = null;
let slot2Ip = null;

let pingInterval = 3;
let tracerouteInterval = 1;
let audioMuted = localStorage.getItem('monitorit_audio_muted') === 'true';

// Helper to escape HTML characters in input values
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Web Audio API Beep Synthesizer
function playRouteAlertSound() {
    if (audioMuted) return;

    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();

        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, audioCtx.currentTime);
        gain.gain.setValueAtTime(0.1, audioCtx.currentTime);

        osc.connect(gain);
        gain.connect(audioCtx.destination);

        osc.start();
        osc.stop(audioCtx.currentTime + 0.3);
    } catch (e) {
        console.log("Audio alert blocked by browser context policy.");
    }
}

// DOM Elements
const profileSelect = document.getElementById('profileSelect');
const hostTableBody = document.getElementById('hostTableBody');
const hostFilterInput = document.getElementById('hostFilterInput');

const statTotalHosts = document.getElementById('statTotalHosts');
const statOnlineHosts = document.getElementById('statOnlineHosts');
const statOfflineHosts = document.getElementById('statOfflineHosts');
const wsStatusBadge = document.getElementById('wsStatusBadge');

const btnToggleAudio = document.getElementById('btnToggleAudio');
const tracerouteEmptyState = document.getElementById('tracerouteEmptyState');

const tracerouteSlot1 = document.getElementById('tracerouteSlot1');
const slot1IpDisplay = document.getElementById('slot1IpDisplay');
const btnLockBaselineSlot1 = document.getElementById('btnLockBaselineSlot1');
const btnStopSlot1 = document.getElementById('btnStopSlot1');
const routeAlertSlot1 = document.getElementById('routeAlertSlot1');
const routeAlertDetailsSlot1 = document.getElementById('routeAlertDetailsSlot1');
const tracerouteTableBodySlot1 = document.getElementById('tracerouteTableBodySlot1');

const tracerouteSlot2 = document.getElementById('tracerouteSlot2');
const slot2IpDisplay = document.getElementById('slot2IpDisplay');
const btnLockBaselineSlot2 = document.getElementById('btnLockBaselineSlot2');
const btnStopSlot2 = document.getElementById('btnStopSlot2');
const routeAlertSlot2 = document.getElementById('routeAlertSlot2');
const routeAlertDetailsSlot2 = document.getElementById('routeAlertDetailsSlot2');
const tracerouteTableBodySlot2 = document.getElementById('tracerouteTableBodySlot2');

const importModal = document.getElementById('importModal');
const importTextarea = document.getElementById('importTextarea');

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
    updateAudioButtonState();
    fetchProfiles();
    connectWebSocket();
    setupEventListeners();
});

function updateAudioButtonState() {
    if (audioMuted) {
        btnToggleAudio.textContent = "🔇 Audio: OFF";
        btnToggleAudio.classList.add('btn-danger');
        btnToggleAudio.classList.remove('btn-secondary');
    } else {
        btnToggleAudio.textContent = "🔊 Audio: ON";
        btnToggleAudio.classList.remove('btn-danger');
        btnToggleAudio.classList.add('btn-secondary');
    }
}

// WebSocket Connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        wsStatusBadge.textContent = "Connected";
        wsStatusBadge.className = "badge badge-online";
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'ping_update') {
            handlePingUpdate(data.hosts);
        } else if (data.type === 'traceroute_update') {
            handleTracerouteUpdate(data);
        }
    };

    ws.onclose = () => {
        wsStatusBadge.textContent = "Disconnected";
        wsStatusBadge.className = "badge badge-offline";
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (err) => {
        console.error("WebSocket error:", err);
        ws.close();
    };
}

// Event Listeners Setup
function setupEventListeners() {
    btnToggleAudio.addEventListener('click', () => {
        audioMuted = !audioMuted;
        localStorage.setItem('monitorit_audio_muted', audioMuted ? 'true' : 'false');
        updateAudioButtonState();
    });

    setupToggleGroup('pingIntervalGroup', (val) => {
        pingInterval = parseInt(val);
        updateIntervalConfig();
    });

    setupToggleGroup('tracerouteIntervalGroup', (val) => {
        tracerouteInterval = parseInt(val);
        updateIntervalConfig();
    });

    document.getElementById('btnLoadProfile').addEventListener('click', () => {
        const selected = profileSelect.value;
        if (selected) loadProfile(selected);
    });

    document.getElementById('btnSaveProfile').addEventListener('click', () => {
        if (document.activeElement) document.activeElement.blur();
        saveProfile(activeProfile);
    });

    document.getElementById('btnSaveProfileAs').addEventListener('click', () => {
        if (document.activeElement) document.activeElement.blur();
        const name = prompt("Enter new profile name:", activeProfile);
        if (name) saveProfile(name);
    });

    document.getElementById('btnOpenImport').addEventListener('click', () => {
        importTextarea.value = "";
        importModal.classList.remove('hidden');
    });

    document.getElementById('btnCloseImport').addEventListener('click', () => {
        importModal.classList.add('hidden');
    });

    document.getElementById('btnCancelImport').addEventListener('click', () => {
        importModal.classList.add('hidden');
    });

    document.getElementById('btnProcessImport').addEventListener('click', processImportHosts);

    hostFilterInput.addEventListener('input', () => {
        renderHostTable(true);
    });

    // Slot 1 & Slot 2 Controls
    btnLockBaselineSlot1.addEventListener('click', () => lockTracerouteBaseline(slot1Ip));
    btnStopSlot1.addEventListener('click', () => stopTracerouteTarget(slot1Ip));

    btnLockBaselineSlot2.addEventListener('click', () => lockTracerouteBaseline(slot2Ip));
    btnStopSlot2.addEventListener('click', () => stopTracerouteTarget(slot2Ip));
}

function setupToggleGroup(groupId, callback) {
    const group = document.getElementById(groupId);
    const buttons = group.querySelectorAll('.btn-toggle');
    buttons.forEach(btn => {
        btn.addEventListener('click', () => {
            buttons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            callback(btn.getAttribute('data-value'));
        });
    });
}

// API Interactions
async function fetchProfiles() {
    try {
        const res = await fetch('/api/profiles');
        const data = await res.json();
        
        profileSelect.innerHTML = '';
        data.profiles.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p;
            opt.textContent = p;
            if (p === data.active_profile) opt.selected = true;
            profileSelect.appendChild(opt);
        });
        activeProfile = data.active_profile;
    } catch (e) {
        console.error("Failed to fetch profiles:", e);
    }
}

async function loadProfile(profileName) {
    try {
        const res = await fetch(`/api/profiles/load/${profileName}`, { method: 'POST' });
        if (res.ok) {
            activeProfile = profileName;
            customNames = {};

            // Clear active traceroute slots on profile change
            slot1Ip = null;
            slot2Ip = null;
            slot1IpDisplay.textContent = "None";
            slot2IpDisplay.textContent = "None";
            tracerouteTableBodySlot1.innerHTML = '';
            tracerouteTableBodySlot2.innerHTML = '';
            routeAlertSlot1.classList.add('hidden');
            routeAlertSlot2.classList.add('hidden');
            updateDrawerLayout();

            fetchProfiles();
        }
    } catch (e) {
        console.error("Failed to load profile:", e);
    }
}

async function saveProfile(profileName) {
    const profilePayload = {
        profile_name: profileName,
        ping_interval: pingInterval,
        traceroute_interval: tracerouteInterval,
        hosts: loadedHosts.map(h => ({ ip: h.ip, name: h.name, group: h.group || 'Default' }))
    };

    try {
        const res = await fetch('/api/profiles/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(profilePayload)
        });
        if (res.ok) {
            alert(`Profile '${profileName}' saved successfully.`);
            fetchProfiles();
        }
    } catch (e) {
        console.error("Failed to save profile:", e);
    }
}

async function processImportHosts() {
    const rawText = importTextarea.value;
    if (!rawText.trim()) return;

    const appendMode = document.getElementById('chkAppendHosts').checked;

    try {
        const res = await fetch('/api/hosts/parse', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ raw_text: rawText })
        });
        const data = await res.json();

        if (data.hosts && data.hosts.length > 0) {
            await fetch('/api/hosts/set', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hosts: data.hosts, append: appendMode })
            });

            if (!appendMode) {
                customNames = {};
            }

            importModal.classList.add('hidden');
            renderHostTable(true);
        }
    } catch (e) {
        console.error("Failed to parse import hosts:", e);
    }
}

async function deleteHost(ip) {
    try {
        const res = await fetch('/api/hosts/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip: ip })
        });

        if (res.ok) {
            loadedHosts = loadedHosts.filter(h => h.ip !== ip);
            delete customNames[ip];
            
            if (slot1Ip === ip) {
                slot1Ip = null;
                slot1IpDisplay.textContent = "None";
                tracerouteTableBodySlot1.innerHTML = '';
                routeAlertSlot1.classList.add('hidden');
            }
            if (slot2Ip === ip) {
                slot2Ip = null;
                slot2IpDisplay.textContent = "None";
                tracerouteTableBodySlot2.innerHTML = '';
                routeAlertSlot2.classList.add('hidden');
            }

            updateDrawerLayout();
            renderHostTable(true);
        }
    } catch (e) {
        console.error(`Failed to delete host ${ip}:`, e);
    }
}

async function saveHostRenameBackend(ip, newName) {
    try {
        await fetch('/api/hosts/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip: ip, name: newName })
        });
    } catch (e) {
        console.error(`Failed to sync host rename for ${ip}:`, e);
    }
}

async function updateIntervalConfig() {
    try {
        await fetch('/api/config/intervals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ping_interval: pingInterval, traceroute_interval: tracerouteInterval })
        });
    } catch (e) {
        console.error("Failed to update intervals:", e);
    }
}

// Dual Slot Traceroute Management
async function requestStartTraceroute(ip) {
    if (ip === slot1Ip || ip === slot2Ip) return;

    let targetSlot = null;

    if (!slot1Ip) {
        targetSlot = 1;
    } else if (!slot2Ip) {
        targetSlot = 2;
    } else {
        const choice = confirm(`Both traceroute slots are active:\nSlot 1: ${slot1Ip}\nSlot 2: ${slot2Ip}\n\nClick 'OK' to replace Slot 1 (${slot1Ip}), or 'Cancel' to replace Slot 2 (${slot2Ip}).`);
        targetSlot = choice ? 1 : 2;
    }

    try {
        const res = await fetch('/api/traceroute/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_ip: ip, slot: targetSlot })
        });

        if (res.ok) {
            if (targetSlot === 1) {
                slot1Ip = ip;
                slot1IpDisplay.textContent = ip;
                routeAlertSlot1.classList.add('hidden');
                tracerouteTableBodySlot1.innerHTML = '';
            } else {
                slot2Ip = ip;
                slot2IpDisplay.textContent = ip;
                routeAlertSlot2.classList.add('hidden');
                tracerouteTableBodySlot2.innerHTML = '';
            }
            updateDrawerLayout();
            renderHostTable(true);
        }
    } catch (e) {
        console.error("Failed to start traceroute:", e);
    }
}

async function stopTracerouteTarget(ip) {
    if (!ip) return;

    try {
        const res = await fetch('/api/traceroute/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_ip: ip })
        });

        if (res.ok) {
            if (ip === slot1Ip) {
                slot1Ip = null;
                slot1IpDisplay.textContent = "None";
                tracerouteTableBodySlot1.innerHTML = '';
                routeAlertSlot1.classList.add('hidden');
            } else if (ip === slot2Ip) {
                slot2Ip = null;
                slot2IpDisplay.textContent = "None";
                tracerouteTableBodySlot2.innerHTML = '';
                routeAlertSlot2.classList.add('hidden');
            }
            updateDrawerLayout();
            renderHostTable(true);
        }
    } catch (e) {
        console.error("Failed to stop traceroute:", e);
    }
}

async function lockTracerouteBaseline(ip) {
    if (!ip) return;

    try {
        const res = await fetch('/api/traceroute/baseline/lock', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_ip: ip })
        });
        if (res.ok) {
            alert(`Locked baseline path for target ${ip}.`);
        }
    } catch (e) {
        console.error("Failed to lock baseline:", e);
    }
}

function updateDrawerLayout() {
    if (!slot1Ip && !slot2Ip) {
        tracerouteEmptyState.classList.remove('hidden');
        tracerouteSlot1.classList.add('hidden');
        tracerouteSlot2.classList.add('hidden');
    } else {
        tracerouteEmptyState.classList.add('hidden');
        if (slot1Ip) tracerouteSlot1.classList.remove('hidden');
        else tracerouteSlot1.classList.add('hidden');

        if (slot2Ip) tracerouteSlot2.classList.remove('hidden');
        else tracerouteSlot2.classList.add('hidden');
    }
}

// UI Render Handlers
function handlePingUpdate(hosts) {
    hosts.forEach(h => {
        if (customNames[h.ip] !== undefined) {
            h.name = customNames[h.ip];
        } else {
            customNames[h.ip] = h.name;
        }
    });

    loadedHosts = hosts;
    
    let online = 0;
    let offline = 0;
    hosts.forEach(h => {
        if (h.is_alive) online++;
        else offline++;
    });

    statTotalHosts.textContent = hosts.length;
    statOnlineHosts.textContent = online;
    statOfflineHosts.textContent = offline;

    renderHostTable(false);
}

function renderHostTable(forceRebuild = false) {
    const filter = hostFilterInput.value.toLowerCase();

    const filteredHosts = loadedHosts.filter(h => 
        !filter || h.ip.toLowerCase().includes(filter) || h.name.toLowerCase().includes(filter)
    );

    if (filteredHosts.length === 0) {
        hostTableBody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">No hosts loaded. Click "+ Add / Import Hosts" to add targets.</td></tr>`;
        return;
    }

    const existingRows = Array.from(hostTableBody.querySelectorAll('tr[data-ip]'));
    const existingIps = existingRows.map(r => r.getAttribute('data-ip'));
    const filteredIps = filteredHosts.map(h => h.ip);

    const canUpdateInPlace = !forceRebuild && existingRows.length > 0 && 
        existingIps.length === filteredIps.length && 
        existingIps.every((ip, i) => ip === filteredIps[i]);

    if (canUpdateInPlace) {
        filteredHosts.forEach(h => {
            const tr = hostTableBody.querySelector(`tr[data-ip="${CSS.escape(h.ip)}"]`);
            if (!tr) return;

            if (selectedHostIp === h.ip) tr.classList.add('selected');
            else tr.classList.remove('selected');

            const statusTd = tr.children[0];
            const rttTd = tr.children[3];
            const sentTd = tr.children[4];
            const recvTd = tr.children[5];
            const lossTd = tr.children[6];
            const actionTd = tr.children[7];

            statusTd.innerHTML = h.is_alive 
                ? `<span class="badge badge-online">Online</span>`
                : `<span class="badge badge-offline">Offline</span>`;

            rttTd.textContent = h.rtt_ms !== null ? `${h.rtt_ms} ms` : '-';
            sentTd.textContent = h.sent;
            recvTd.textContent = h.received;
            lossTd.className = h.loss_pct > 0 ? 'text-danger' : 'text-success';
            lossTd.textContent = `${h.loss_pct}%`;

            const nameInput = tr.querySelector('.table-input-name');
            if (nameInput && document.activeElement !== nameInput) {
                nameInput.value = h.name;
            }

            const btnTrace = actionTd.querySelector('.btn-trace');
            if (btnTrace) {
                if (h.ip === slot1Ip) {
                    btnTrace.textContent = "Tracing (Slot 1)";
                    btnTrace.className = "btn btn-warning btn-sm btn-trace";
                } else if (h.ip === slot2Ip) {
                    btnTrace.textContent = "Tracing (Slot 2)";
                    btnTrace.className = "btn btn-warning btn-sm btn-trace";
                } else {
                    btnTrace.textContent = "Monitor Route";
                    btnTrace.className = "btn btn-primary btn-sm btn-trace";
                }
            }
        });
        return;
    }

    hostTableBody.innerHTML = '';

    filteredHosts.forEach(h => {
        const tr = document.createElement('tr');
        tr.setAttribute('data-ip', h.ip);
        if (selectedHostIp === h.ip) tr.classList.add('selected');

        const statusBadge = h.is_alive 
            ? `<span class="badge badge-online">Online</span>`
            : `<span class="badge badge-offline">Offline</span>`;

        const rttDisplay = h.rtt_ms !== null ? `${h.rtt_ms} ms` : '-';

        let traceBtnLabel = "Monitor Route";
        let traceBtnClass = "btn-primary";
        if (h.ip === slot1Ip) {
            traceBtnLabel = "Tracing (Slot 1)";
            traceBtnClass = "btn-warning";
        } else if (h.ip === slot2Ip) {
            traceBtnLabel = "Tracing (Slot 2)";
            traceBtnClass = "btn-warning";
        }

        tr.innerHTML = `
            <td>${statusBadge}</td>
            <td><strong>${h.ip}</strong></td>
            <td><input type="text" class="table-input-name" data-ip="${h.ip}" value="${escapeHtml(h.name)}"></td>
            <td>${rttDisplay}</td>
            <td>${h.sent}</td>
            <td>${h.received}</td>
            <td class="${h.loss_pct > 0 ? 'text-danger' : 'text-success'}">${h.loss_pct}%</td>
            <td>
                <div class="action-cell">
                    <button class="btn ${traceBtnClass} btn-sm btn-trace" data-ip="${h.ip}">${traceBtnLabel}</button>
                    <button class="btn btn-danger btn-icon btn-delete" data-ip="${h.ip}" title="Remove Host from List">✕</button>
                </div>
            </td>
        `;

        const nameInput = tr.querySelector('.table-input-name');
        
        nameInput.addEventListener('click', (e) => e.stopPropagation());
        nameInput.addEventListener('mousedown', (e) => e.stopPropagation());
        nameInput.addEventListener('focus', (e) => e.stopPropagation());

        nameInput.addEventListener('input', (e) => {
            const newName = e.target.value;
            h.name = newName;
            customNames[h.ip] = newName;
        });

        nameInput.addEventListener('blur', (e) => {
            const finalName = e.target.value.trim();
            h.name = finalName;
            customNames[h.ip] = finalName;
            saveHostRenameBackend(h.ip, finalName);
        });

        nameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                nameInput.blur();
            }
        });

        const btnTrace = tr.querySelector('.btn-trace');
        btnTrace.addEventListener('click', (e) => {
            e.stopPropagation();
            selectedHostIp = h.ip;
            renderHostTable(true);
            requestStartTraceroute(h.ip);
        });

        const btnDelete = tr.querySelector('.btn-delete');
        btnDelete.addEventListener('click', (e) => {
            e.stopPropagation();
            deleteHost(h.ip);
        });

        tr.addEventListener('click', () => {
            selectedHostIp = h.ip;
            const allRows = hostTableBody.querySelectorAll('tr');
            allRows.forEach(r => r.classList.remove('selected'));
            tr.classList.add('selected');
        });

        hostTableBody.appendChild(tr);
    });
}

function handleTracerouteUpdate(data) {
    const slot = data.slot;
    const targetBody = slot === 1 ? tracerouteTableBodySlot1 : tracerouteTableBodySlot2;
    const alertBanner = slot === 1 ? routeAlertSlot1 : routeAlertSlot2;
    const alertDetails = slot === 1 ? routeAlertDetailsSlot1 : routeAlertDetailsSlot2;

    targetBody.innerHTML = '';

    if (data.path_changed || data.baseline_diff) {
        alertBanner.classList.remove('hidden');
        alertDetails.textContent = data.baseline_diff 
            ? "Current path deviates from LOCKED BASELINE route!" 
            : "Path shift detected between consecutive sweeps.";
        playRouteAlertSound();
    } else {
        alertBanner.classList.add('hidden');
    }

    data.hops.forEach((hop, idx) => {
        const tr = document.createElement('tr');
        
        let baselineIp = "-";
        if (data.baseline_path && data.baseline_path[idx]) {
            baselineIp = data.baseline_path[idx];
            if (hop.ip !== "*" && hop.ip !== baselineIp) {
                tr.classList.add('hop-changed');
            }
        }

        const rttDisplay = hop.rtt_ms !== null ? `${hop.rtt_ms} ms` : '*';
        const destFlag = hop.is_destination ? ` <span class="text-success">(Target)</span>` : '';

        tr.innerHTML = `
            <td><strong>Hop ${hop.hop_number}</strong></td>
            <td>${hop.ip}${destFlag}</td>
            <td>${rttDisplay}</td>
            <td>${baselineIp}</td>
        `;

        targetBody.appendChild(tr);
    });
}