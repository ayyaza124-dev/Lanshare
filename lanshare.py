#!/usr/bin/env python3
"""
LANShare - move files, photos and videos between a laptop and a phone over a
Wi-Fi hotspot or any shared Wi-Fi. No internet needed. Zero dependencies (Python 3.8+).

Run it on ANY device that has Python (Windows / macOS / Linux laptop, or an Android
phone through Termux). That device is the HOST: everything that is sent lands in one
folder on the host's own storage (use --dir to point it at your SSD). Every other
device just opens the link in a browser - nothing to install.

    python3 lanshare.py                          # folder: ~/LANShare
    python3 lanshare.py --dir D:\\LANShare        # any folder, e.g. on your SSD
    python3 lanshare.py --https --pin 4821 --port 9000

Also used as the engine of the Windows app (windows_app.py) and the Android app (main.py).

Security
  * PIN login (random 6 digits per start, or --pin)
  * 5 wrong PINs -> 5 minute lockout per device (constant-time PIN compare)
  * Random session tokens in HttpOnly + SameSite=Strict cookies
  * Local-network only: public (internet) IPs are refused
  * CSRF protection (Origin check + custom header)
  * Strict CSP with per-request nonce; file names rendered as plain text (no XSS)
  * Uploaded files are never executed or rendered as web pages: only images, video,
    audio and PDF can be previewed, everything else is forced to download
  * File names are sanitised; path traversal is impossible (flat folder only)
  * Files are streamed to disk in 1 MB pieces, so multi-GB videos never fill RAM
  * Optional HTTPS (--https) with a self-signed certificate
"""
import argparse
import errno
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from http import cookies
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

try:                            # some Android Python builds ship without TLS: HTTPS is optional
    import ssl
except ImportError:
    ssl = None

CHUNK = 1024 * 1024
MAX_BODY = 4 * 1024            # JSON requests only; file uploads are streamed
RESERVE_BYTES = 64 * 1024 * 1024   # always leave this much free on the drive
SESSION_TTL = 12 * 3600
MAX_FAILS = 5
LOCK_SECONDS = 300

lock = threading.Lock()        # sessions + login attempts
fs_lock = threading.Lock()     # choosing final file names
sessions = {}                  # token -> {"exp"}
fails = {}                     # ip -> [count, locked_until]
CONFIG = {"pin": "", "https": False, "root": None, "max_bytes": 0, "allow_delete": True,
          "host_ui": False}
EVENT_HOOK = None              # apps set this to a function(str) to receive activity messages


def emit(msg):
    hook = EVENT_HOOK
    if hook:
        try:
            hook(msg)
        except Exception:
            pass
    else:
        print("  " + msg)


class FolderError(Exception):
    pass


class PortError(Exception):
    pass

# The only types the browser may display inline. Everything else is a forced download.
INLINE_TYPES = {
    ".png": ("image/png", "image"), ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"), ".gif": ("image/gif", "image"),
    ".webp": ("image/webp", "image"), ".avif": ("image/avif", "image"),
    ".bmp": ("image/bmp", "image"),
    ".mp4": ("video/mp4", "video"), ".m4v": ("video/mp4", "video"),
    ".mov": ("video/quicktime", "video"), ".webm": ("video/webm", "video"),
    ".mp3": ("audio/mpeg", "audio"), ".wav": ("audio/wav", "audio"),
    ".ogg": ("audio/ogg", "audio"), ".oga": ("audio/ogg", "audio"),
    ".m4a": ("audio/mp4", "audio"), ".aac": ("audio/aac", "audio"),
    ".flac": ("audio/flac", "audio"),
    ".pdf": ("application/pdf", "pdf"),
}

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>LANShare</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%232B59FF'/%3E%3Cpath d='M16 8v13M10 14l6-6 6 6M9 25h14' stroke='white' stroke-width='3' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style nonce="{{NONCE}}">
:root {
  --bg: #F1F5F3; --panel: #FFFFFF; --ink: #12201B; --muted: #566761; --line: #D3DCD8;
  --accent: #2B59FF; --accent-ink: #FFFFFF; --accent-soft: #E3EAFF;
  --danger: #B72E27; --ok: #17794F; --tile: #E4ECE8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101815; --panel: #18221E; --ink: #E7EFEB; --muted: #93A39C; --line: #2B3833;
    --accent: #86A2FF; --accent-ink: #0B1230; --accent-soft: #1C2A55;
    --danger: #FF9A90; --ok: #6ED3A2; --tile: #22302B;
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.45 "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif;
  padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
}
h1, h2 { margin: 0; line-height: 1.15; }
button, input { font: inherit; color: inherit; }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }

.wrap { max-width: 880px; margin: 0 auto; padding: 20px 16px 64px; }

/* ---------- header ---------- */
.top { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; padding: 4px 0 20px; }
.top h1 { font-size: 1.35rem; font-weight: 700; letter-spacing: -0.01em; }
.store { margin-left: auto; min-width: 200px; text-align: right; font-size: 0.85rem; color: var(--muted); }
.meter { height: 6px; border-radius: 3px; background: var(--line); margin-top: 6px; overflow: hidden; }
.meter > div { height: 100%; width: 0; background: var(--accent); }

/* ---------- drop zone ---------- */
.drop {
  display: block; cursor: pointer; text-align: center;
  border: 2px dashed var(--accent); border-radius: 18px; background: var(--panel);
  padding: clamp(28px, 7vw, 56px) 20px;
}
.drop:focus-within { outline: 3px solid var(--accent); outline-offset: 3px; }
.drop.over { background: var(--accent-soft); border-style: solid; }
.drop svg { width: 44px; height: 44px; color: var(--accent); }
.drop strong { display: block; font-size: clamp(1.25rem, 4.5vw, 1.75rem); margin: 10px 0 4px; letter-spacing: -0.01em; }
.drop > span { color: var(--muted); }
.only-touch { display: none; }
@media (hover: none) { .only-touch { display: inline; } .only-mouse { display: none; } }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* ---------- upload queue ---------- */
.queue { list-style: none; margin: 14px 0 0; padding: 0; display: grid; gap: 10px; }
.up { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }
.up-top { display: flex; gap: 12px; align-items: baseline; justify-content: space-between; }
.up-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.up-state { color: var(--muted); font-size: 0.85rem; white-space: nowrap; }
.up.failed .up-state { color: var(--danger); white-space: normal; text-align: right; }
.up.done .up-state { color: var(--ok); }
.bar { height: 8px; margin-top: 10px; border-radius: 4px; background: var(--tile); overflow: hidden; }
.bar > div { height: 100%; width: 0; background: var(--accent); }
.up.failed .bar > div { background: var(--danger); }
.up.done .bar > div { background: var(--ok); }
@media (prefers-reduced-motion: no-preference) { .bar > div { transition: width 0.2s linear; } }
.up-actions { display: flex; gap: 14px; margin-top: 8px; }

/* ---------- file list ---------- */
.list-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 36px 0 12px; }
.list-head h2 { font-size: 1.15rem; }
.count { color: var(--muted); font-size: 0.9rem; }
.filter {
  margin-left: auto; min-width: 0; width: 220px; max-width: 100%;
  border: 1px solid var(--line); border-radius: 10px; background: var(--panel); padding: 8px 12px;
}
.files { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.row { display: grid; grid-template-columns: 48px minmax(0, 1fr) auto; gap: 4px 14px; align-items: center; padding: 12px 14px; border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; }
.tile {
  width: 48px; height: 48px; border-radius: 10px; background: var(--tile); color: var(--muted);
  display: grid; place-items: center; font-size: 0.72rem; font-weight: 700; overflow: hidden;
}
.tile img { width: 100%; height: 100%; object-fit: cover; display: block; }
.fname { font-weight: 600; overflow-wrap: anywhere; }
button.fname, a.fname { background: none; border: 0; padding: 0; text-align: left; color: var(--ink); cursor: pointer; text-decoration: none; }
button.fname:hover, a.fname:hover { color: var(--accent); text-decoration: underline; }
.meta { color: var(--muted); font-size: 0.85rem; display: flex; gap: 4px 14px; flex-wrap: wrap; }
.acts { display: flex; gap: 8px; }
.empty { padding: 36px 20px; text-align: center; color: var(--muted); }
@media (max-width: 540px) {
  .store { order: 3; flex: 1 1 100%; text-align: left; margin-left: 0; }
  #logout { margin-left: auto; }
  .row { grid-template-columns: 48px minmax(0, 1fr); }
  .acts { grid-column: 2; }
  .filter { margin-left: 0; width: 100%; }
}

/* ---------- buttons ---------- */
.btn {
  display: inline-block; border: 1px solid var(--line); background: var(--panel); border-radius: 10px;
  padding: 7px 14px; cursor: pointer; text-decoration: none; font-size: 0.92rem; font-weight: 600; color: var(--ink);
}
.btn:hover { border-color: var(--accent); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.btn.danger { color: var(--danger); }
#logout { flex: none; }
.link { background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; font-size: 0.9rem; text-decoration: underline; }

/* ---------- login ---------- */
.login { min-height: 100vh; display: grid; place-items: center; padding: 20px; }
.login-card { width: 100%; max-width: 380px; background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 28px 24px; display: grid; gap: 14px; }
.login-card h1 { font-size: 1.6rem; }
.login-card p { margin: 0; color: var(--muted); }
.field { display: grid; gap: 6px; font-weight: 600; }
.field input { border: 1px solid var(--line); border-radius: 10px; background: var(--bg); padding: 12px; font-size: 1.1rem; letter-spacing: 0.08em; }
.error { color: var(--danger); min-height: 1.4em; }

/* ---------- viewer + status ---------- */
dialog { border: 0; padding: 0; border-radius: 16px; background: var(--panel); color: var(--ink); width: min(960px, 94vw); max-height: 92vh; overflow: hidden; }
dialog::backdrop { background: rgba(8, 14, 12, 0.72); }
.viewer-bar { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--line); }
.viewer-bar strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
#viewer-body { display: grid; place-items: center; background: #000; max-height: calc(92vh - 58px); }
#viewer-body img, #viewer-body video { max-width: 100%; max-height: calc(92vh - 58px); display: block; }
#viewer-body audio { width: 92%; margin: 40px 0; }
.status {
  position: fixed; left: 50%; bottom: calc(18px + env(safe-area-inset-bottom)); transform: translateX(-50%);
  background: var(--ink); color: var(--bg); padding: 10px 16px; border-radius: 10px; font-size: 0.92rem;
  max-width: 92vw; z-index: 20;
}
.status:empty { display: none; }
</style>
</head>
<body>
<noscript><p style="padding:20px">LANShare needs JavaScript. Please turn it on in your browser.</p></noscript>
<div id="status" class="status" role="status" aria-live="polite"></div>

<main id="login" class="login" hidden>
  <form id="login-form" class="login-card" autocomplete="off">
    <h1>LANShare</h1>
    <p>Enter the PIN shown on the device that is hosting.</p>
    <label class="field"><span>PIN</span>
      <input id="pin" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" required>
    </label>
    <button class="btn primary" type="submit">Connect</button>
    <p id="login-error" class="error" role="alert"></p>
  </form>
</main>

<div id="app" class="wrap" hidden>
  <header class="top">
    <h1>LANShare</h1>
    <div class="store">
      <div id="store-text">Checking storage</div>
      <div class="meter" aria-hidden="true"><div id="meter-fill"></div></div>
    </div>
    <button id="logout" class="link" type="button">Sign out</button>
  </header>

  <label id="drop" class="drop">
    <input id="picker" class="sr" type="file" multiple>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>
    <strong><span class="only-mouse">Drop files here, or click to choose</span><span class="only-touch">Tap to choose photos, videos or files</span></strong>
    <span>They go straight into the folder <b id="dest">on the host</b>.</span>
  </label>

  <ul id="queue" class="queue" aria-label="Uploads"></ul>

  <div class="list-head">
    <h2>In the shared folder</h2>
    <span id="count" class="count"></span>
    <input id="filter" class="filter" type="search" placeholder="Filter by name" aria-label="Filter files by name">
  </div>
  <ul id="files" class="files"></ul>
</div>

<dialog id="viewer" aria-label="Preview">
  <div class="viewer-bar"><strong id="viewer-title"></strong><button id="viewer-close" class="btn" type="button">Close</button></div>
  <div id="viewer-body"></div>
</dialog>

<script nonce="{{NONCE}}">
(() => {
'use strict';
const $ = (s) => document.querySelector(s);
const h = (tag, props = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids) if (kid) el.append(kid);
  return el;
};
const fmtSize = (n) => {
  if (n < 1024) return n + ' B';
  const u = ['KB', 'MB', 'GB', 'TB']; let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return (n >= 100 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
};
const fmtTime = (s) => new Date(s * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
const fileUrl = (f, dl) => '/d/' + encodeURIComponent(f.name) + (dl ? '?dl=1' : '');
let toastTimer;
const toast = (msg) => {
  const el = $('#status'); el.textContent = msg;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.textContent = ''; }, 4500);
};

async function call(path, opts = {}) {
  const r = await fetch(path, {
    credentials: 'same-origin', ...opts,
    headers: { 'X-Requested-With': 'lanshare', ...(opts.headers || {}) },
  });
  let body = null; try { body = await r.json(); } catch (e) { /* no body */ }
  if (r.status === 401 && path !== '/api/login') { showLogin(); throw new Error('Login required'); }
  if (!r.ok) throw new Error((body && body.error) || ('Request failed (' + r.status + ')'));
  return body;
}

/* ---------- login ---------- */
let poller = null;
function showLogin() {
  $('#app').hidden = true; $('#login').hidden = false;
  clearInterval(poller); poller = null;
  $('#pin').value = ''; $('#pin').focus();
}
function showApp() {
  $('#login').hidden = true; $('#app').hidden = false;
  refresh();
  if (!poller) poller = setInterval(() => { if (!document.hidden) refresh(); }, 4000);
}
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('#login-error').textContent = '';
  try {
    await call('/api/login', { method: 'POST', body: JSON.stringify({ pin: $('#pin').value }) });
    showApp();
  } catch (err) {
    $('#login-error').textContent = err.message;
  }
});
$('#logout').addEventListener('click', async () => {
  try { await call('/api/logout', { method: 'POST', body: '{}' }); } catch (e) { /* ignore */ }
  showLogin();
});

/* ---------- file list ---------- */
let files = [], canDelete = true, lastSig = '';
async function refresh() {
  try {
    const d = await call('/api/files');
    const sig = JSON.stringify(d);
    if (sig === lastSig) return;
    lastSig = sig; files = d.files; canDelete = d.can_delete;
    $('#dest').textContent = d.folder;
    const usedPct = d.total ? Math.min(100, Math.round(((d.total - d.free) / d.total) * 100)) : 0;
    $('#store-text').textContent = fmtSize(d.free) + ' free of ' + fmtSize(d.total);
    $('#meter-fill').style.width = usedPct + '%';
    render();
  } catch (e) { /* offline or logged out: keep last view */ }
}
function extBadge(name) {
  const i = name.lastIndexOf('.');
  return i > 0 && i < name.length - 1 ? name.slice(i + 1, i + 5) : 'FILE';
}
function render() {
  const q = $('#filter').value.trim().toLowerCase();
  const shown = files.filter((f) => f.name.toLowerCase().includes(q));
  $('#count').textContent = files.length ? (q ? shown.length + ' of ' : '') + files.length + (files.length === 1 ? ' file' : ' files') : '';
  const list = $('#files'); list.replaceChildren();
  if (!shown.length) {
    list.append(h('li', { class: 'empty', text: files.length ? 'No file matches that filter.' : 'Nothing here yet. Add a file above and it will show up on every connected device.' }));
    return;
  }
  for (const f of shown) {
    const tile = h('div', { class: 'tile' });
    if (f.kind === 'image' && f.size < 4e6) tile.append(h('img', { src: fileUrl(f), alt: '', loading: 'lazy' }));
    else tile.textContent = extBadge(f.name);

    let name;
    if (f.kind === 'pdf') name = h('a', { class: 'fname', href: fileUrl(f), target: '_blank', rel: 'noopener', text: f.name });
    else if (f.kind) name = h('button', { class: 'fname', type: 'button', text: f.name, onclick: () => preview(f) });
    else name = h('span', { class: 'fname', text: f.name });

    const acts = h('div', { class: 'acts' },
      h('a', { class: 'btn', href: fileUrl(f, true), download: f.name, text: 'Download' }),
      canDelete ? h('button', { class: 'btn danger', type: 'button', text: 'Delete', onclick: () => del(f) }) : null);

    list.append(h('li', { class: 'row' }, tile,
      h('div', {}, name, h('div', { class: 'meta' }, h('span', { text: fmtSize(f.size) }), h('span', { text: fmtTime(f.mtime) }))),
      acts));
  }
}
$('#filter').addEventListener('input', render);

async function del(f) {
  if (!confirm('Delete "' + f.name + '" from the shared folder? This cannot be undone.')) return;
  try {
    await call('/api/delete', { method: 'POST', body: JSON.stringify({ name: f.name }) });
    toast('Deleted ' + f.name);
    lastSig = ''; refresh();
  } catch (e) { toast(e.message); }
}

/* ---------- preview ---------- */
const viewer = $('#viewer');
function preview(f) {
  const box = $('#viewer-body'); box.replaceChildren();
  const src = fileUrl(f);
  if (f.kind === 'image') box.append(h('img', { src, alt: f.name }));
  else if (f.kind === 'video') box.append(h('video', { src, controls: '', autoplay: '', playsinline: '' }));
  else if (f.kind === 'audio') box.append(h('audio', { src, controls: '', autoplay: '' }));
  $('#viewer-title').textContent = f.name;
  viewer.showModal();
}
viewer.addEventListener('close', () => $('#viewer-body').replaceChildren());
viewer.addEventListener('click', (e) => { if (e.target === viewer) viewer.close(); });
$('#viewer-close').addEventListener('click', () => viewer.close());

/* ---------- uploads ---------- */
const MAX_ACTIVE = 2;
const queue = [];
let active = 0;

function addFiles(list) {
  for (const file of list) {
    const it = { file, status: 'waiting', xhr: null };
    const name = h('span', { class: 'up-name', text: file.name });
    it.state = h('span', { class: 'up-state', text: 'Waiting' });
    it.fill = h('div', {});
    it.bar = h('div', { class: 'bar', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': '0', 'aria-label': 'Upload progress for ' + file.name }, it.fill);
    it.actions = h('div', { class: 'up-actions' });
    it.el = h('li', { class: 'up' }, h('div', { class: 'up-top' }, name, it.state), it.bar, it.actions);
    $('#queue').append(it.el);
    queue.push(it);
    setActions(it);
  }
  pump();
}
function setActions(it) {
  it.actions.replaceChildren();
  if (it.status === 'waiting' || it.status === 'running') {
    it.actions.append(h('button', { class: 'link', type: 'button', text: 'Cancel', onclick: () => cancel(it) }));
  } else if (it.status === 'failed') {
    it.actions.append(
      h('button', { class: 'link', type: 'button', text: 'Try again', onclick: () => { it.status = 'waiting'; it.el.classList.remove('failed'); it.state.textContent = 'Waiting'; setActions(it); pump(); } }),
      h('button', { class: 'link', type: 'button', text: 'Dismiss', onclick: () => remove(it) }));
  }
}
function remove(it) {
  const i = queue.indexOf(it); if (i >= 0) queue.splice(i, 1);
  it.el.remove();
}
function cancel(it) {
  if (it.xhr && it.status === 'running') { it.cancelled = true; it.xhr.abort(); }
  remove(it);
  pump();
}
function pump() {
  while (active < MAX_ACTIVE) {
    const it = queue.find((x) => x.status === 'waiting');
    if (!it) break;
    start(it);
  }
}
function fail(it, msg) {
  it.status = 'failed'; active--;
  it.el.classList.add('failed'); it.state.textContent = msg; setActions(it); pump();
}
function start(it) {
  it.status = 'running'; active++; setActions(it);
  const xhr = new XMLHttpRequest(); it.xhr = xhr;
  const t0 = Date.now();
  xhr.open('POST', '/api/upload?name=' + encodeURIComponent(it.file.name));
  xhr.setRequestHeader('X-Requested-With', 'lanshare');
  xhr.setRequestHeader('Content-Type', 'application/octet-stream');
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.floor((e.loaded / e.total) * 100);
    const speed = e.loaded / Math.max(0.5, (Date.now() - t0) / 1000);
    it.fill.style.width = pct + '%';
    it.bar.setAttribute('aria-valuenow', String(pct));
    it.state.textContent = pct + '% at ' + fmtSize(speed) + '/s';
  };
  xhr.onload = () => {
    let body = null; try { body = JSON.parse(xhr.responseText); } catch (e) { /* not json */ }
    if (xhr.status === 201) {
      active--; it.status = 'done'; it.el.classList.add('done');
      it.fill.style.width = '100%';
      it.state.textContent = body && body.name && body.name !== it.file.name ? 'Saved as ' + body.name : 'Saved';
      setActions(it); lastSig = ''; refresh(); pump();
      setTimeout(() => remove(it), 5000);
    } else if (xhr.status === 401) {
      active--; it.status = 'waiting'; showLogin(); toast('Signed out. Enter the PIN to continue.');
    } else {
      fail(it, (body && body.error) || ('Upload failed (' + xhr.status + ')'));
    }
  };
  xhr.onerror = () => fail(it, 'Connection lost or refused. Check the host has free space, then try again.');
  xhr.onabort = () => { if (it.cancelled) active--; };
  xhr.send(it.file);
  pump();
}
window.addEventListener('beforeunload', (e) => { if (active > 0) { e.preventDefault(); e.returnValue = ''; } });

const drop = $('#drop');
$('#picker').addEventListener('change', (e) => { addFiles(Array.from(e.target.files)); e.target.value = ''; });
['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => {
  const picked = []; let skipped = 0;
  const items = e.dataTransfer && e.dataTransfer.items;
  if (items && items.length) {
    for (const item of items) {
      if (item.kind !== 'file') continue;
      const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
      if (entry && entry.isDirectory) { skipped++; continue; }
      const f = item.getAsFile(); if (f) picked.push(f);
    }
  } else if (e.dataTransfer) {
    picked.push(...e.dataTransfer.files);
  }
  if (skipped) toast("Folders can't be sent. Put them in a zip file first.");
  addFiles(picked);
});
['dragover', 'drop'].forEach((t) => window.addEventListener(t, (e) => e.preventDefault()));

/* ---------- start ---------- */
(async () => {
  try {
    const s = await call('/api/session');
    if (s.authed) showApp(); else showLogin();
  } catch (e) { showLogin(); }
})();
})();
</script>
</body>
</html>
'''


# --------------------------------------------------------------------------- helpers
def usable_ip(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private and not (a.is_loopback or a.is_unspecified or a.is_link_local
                                 or a.is_multicast or a.is_reserved)


def is_local(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def lan_ips():
    """Best-effort list of this device's private IPv4 addresses (works with no internet)."""
    found = []
    for target in ("10.254.254.254", "192.168.254.254", "172.16.254.254"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))
            found.append(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    if not any(usable_ip(i) for i in found):
        # Phone hotspots often have no default route: ask the OS directly.
        for cmd, rx in ((["ip", "-4", "-o", "addr"], r"inet (\d+\.\d+\.\d+\.\d+)/"),
                        (["ifconfig"], r"inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)")):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            found += re.findall(rx, out)
    out = []
    for ip in found:
        if ip not in out and usable_ip(ip):
            out.append(ip)
    return out


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} \
    | {"lpt%d" % i for i in range(1, 10)}


def safe_name(raw):
    """Turn whatever the browser sent into a harmless single file name."""
    name = str(raw or "").replace("\\", "/").split("/")[-1]
    name = "".join(c if c.isprintable() else "_" for c in name)
    name = _BAD_CHARS.sub("_", name).strip().strip(".").strip()
    stem, ext = os.path.splitext(name)
    ext = ext[:20]
    while len((stem + ext).encode("utf-8")) > 200 and stem:
        stem = stem[:-1]
    if not stem.strip():
        stem = "file"
    if stem.lower() in _RESERVED:
        stem = "_" + stem
    return stem + ext


def unique_path(root, name):
    p = root / name
    if not p.exists():
        return p
    stem, ext = os.path.splitext(name)
    for i in range(1, 100000):
        p = root / ("%s (%d)%s" % (stem, i, ext))
        if not p.exists():
            return p
    raise OSError("too many files with the same name")


def resolve_file(name):
    """Map a URL/API file name to a real regular file inside the shared folder, or None."""
    if (not name or name != os.path.basename(name) or "/" in name or "\\" in name
            or "\x00" in name or name.startswith(".") or (os.name == "nt" and ":" in name)):
        return None
    p = CONFIG["root"] / name
    try:
        if p.is_symlink() or not p.is_file():
            return None
    except OSError:
        return None
    return p


def kind_of(name):
    return INLINE_TYPES.get(os.path.splitext(name)[1].lower(), (None, None))[1]


def list_files():
    out = []
    try:
        with os.scandir(CONFIG["root"]) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if not e.is_file(follow_symlinks=False):
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                out.append({"name": e.name, "size": st.st_size,
                            "mtime": int(st.st_mtime), "kind": kind_of(e.name)})
    except OSError:
        pass
    out.sort(key=lambda f: (-f["mtime"], f["name"].lower()))
    return out


def ascii_fallback(name):
    return re.sub(r'[^\x20-\x7e]|["\\]', "_", name)


def get_session(token):
    """Caller must hold `lock`."""
    exp = sessions.get(token or "", {}).get("exp")
    if exp and exp > time.time():
        return sessions[token]
    sessions.pop(token or "", None)
    return None


class ClientGone(Exception):
    pass


# --------------------------------------------------------------------------- server
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "LANShare"
    sys_version = ""
    timeout = 60  # per socket read/write; a slow-but-moving transfer is fine

    def log_message(self, fmt, *args):
        line = str(fmt) % args
        if EVENT_HOOK or "/api/files" in line or "/api/session" in line:
            return  # don't spam the console with polling
        print("  %s  %s" % (self.client_address[0], line))

    def _headers(self, nonce=None, revalidate=False):
        self.send_header("Cache-Control", "private, no-cache" if revalidate else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if nonce:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'nonce-%s'; style-src 'nonce-%s'; "
                "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'" % (nonce, nonce))
        if CONFIG["https"]:
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def _json(self, code, obj, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _reject(self, code, msg):
        """Answer an error to a request whose body we may not have read yet."""
        try:
            left = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            left = 0
        if 0 < left <= 8 * CHUNK:           # small: read it so the browser sees our answer
            try:
                while left > 0:
                    got = self.rfile.read(min(CHUNK, left))
                    if not got:
                        break
                    left -= len(got)
            except OSError:
                pass
        self.close_connection = True
        self._json(code, {"error": msg})

    def _guard(self):
        if not is_local(self.client_address[0]):
            self._json(403, {"error": "Local network only"})
            return False
        return True

    def _csrf_ok(self):
        if self.headers.get("X-Requested-With") != "lanshare":
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def _token(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c["sid"].value if "sid" in c else None

    def _authed(self):
        with lock:
            return bool(get_session(self._token()))

    def _open_session(self):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with lock:
            for t in [t for t, s in sessions.items() if s["exp"] < now]:
                del sessions[t]
            sessions[token] = {"exp": now + SESSION_TTL}
        return token

    def _auto_cookie(self):
        """The host device's own browser (127.0.0.1) signs in without a PIN, apps only."""
        if (CONFIG["host_ui"] and self.client_address[0] in ("127.0.0.1", "::1")
                and not self._authed()):
            return [("Set-Cookie", self._cookie(self._open_session(), SESSION_TTL))]
        return []

    def _cookie(self, value, max_age):
        c = "sid=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (value, max_age)
        return c + ("; Secure" if CONFIG["https"] else "")

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n < 0 or n > MAX_BODY:
                return None
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # ---- GET
    def do_GET(self):
        if not self._guard():
            return
        url = urlparse(self.path)
        if url.path == "/":
            nonce = secrets.token_urlsafe(16)
            html = PAGE.replace("{{NONCE}}", nonce).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self._headers(nonce)
            for k, v in self._auto_cookie():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(html)
        elif url.path == "/api/session":
            extra = self._auto_cookie()
            self._json(200, {"authed": bool(extra) or self._authed()}, extra)
        elif url.path == "/api/files":
            if not self._authed():
                return self._json(401, {"error": "Login required"})
            try:
                du = shutil.disk_usage(str(CONFIG["root"]))
                free, total = du.free, du.total
            except OSError:
                free = total = 0
            self._json(200, {"files": list_files(), "folder": CONFIG["root"].name,
                             "free": free, "total": total,
                             "can_delete": CONFIG["allow_delete"]})
        elif url.path.startswith("/d/"):
            if not self._authed():
                return self._json(401, {"error": "Login required"})
            self._download(url)
        else:
            self._json(404, {"error": "Not found"})

    def _download(self, url):
        name = unquote(url.path[3:])
        p = resolve_file(name)
        if not p:
            return self._json(404, {"error": "File not found"})
        try:
            st = p.stat()
            f = open(p, "rb")
        except OSError:
            return self._json(404, {"error": "File not found"})
        with f:
            size = st.st_size
            etag = '"%x-%x"' % (size, st.st_mtime_ns)
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self._headers(revalidate=True)
                self.end_headers()
                return
            ctype, kind = INLINE_TYPES.get(os.path.splitext(name)[1].lower(), (None, None))
            inline = bool(ctype) and "dl" not in parse_qs(url.query)
            start, end, status = 0, size - 1, 200
            rng = (self.headers.get("Range") or "").strip()
            if_range = self.headers.get("If-Range")
            if rng and size > 0 and (not if_range or if_range == etag):
                m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
                if m and (m.group(1) or m.group(2)):
                    if m.group(1) == "":
                        start = max(0, size - int(m.group(2)))
                    else:
                        start = int(m.group(1))
                        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */%d" % size)
                        self.send_header("Content-Length", "0")
                        self._headers()
                        self.end_headers()
                        return
                    status = 206
            length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", ctype if inline else "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
            if status == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Content-Disposition", "%s; filename=\"%s\"; filename*=UTF-8''%s" % (
                "inline" if inline else "attachment", ascii_fallback(name), quote(name, safe="")))
            self._headers(revalidate=True)
            self.end_headers()
            try:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except OSError:
                self.close_connection = True   # client went away (e.g. seeked in a video)

    # ---- POST
    def do_POST(self):
        if not self._guard():
            return
        url = urlparse(self.path)
        path = url.path
        if not self._csrf_ok():
            return self._reject(403, "Bad request origin")
        if path == "/api/upload":
            return self._upload(url)
        data = self._body()
        if data is None:
            return self._json(400, {"error": "Invalid or oversized request"})

        if path == "/api/login":
            return self._login(data)

        if path == "/api/logout":
            with lock:
                sessions.pop(self._token() or "", None)
            return self._json(200, {"ok": True}, [("Set-Cookie", self._cookie("", 0))])

        if path == "/api/delete":
            if not self._authed():
                return self._json(401, {"error": "Login required"})
            if not CONFIG["allow_delete"]:
                return self._json(403, {"error": "Deleting is turned off on this host"})
            p = resolve_file(str(data.get("name") or ""))
            if not p:
                return self._json(404, {"error": "File not found"})
            try:
                p.unlink()
            except OSError as e:
                return self._json(500, {"error": "Could not delete: %s" % e.strerror})
            emit("Deleted %s" % p.name)
            return self._json(200, {"ok": True})

        self._json(404, {"error": "Not found"})

    def _upload(self, url):
        if not self._authed():
            return self._reject(401, "Login required")
        root = CONFIG["root"]
        raw = (parse_qs(url.query).get("name") or [""])[0]
        name = safe_name(raw)
        try:
            size = int(self.headers.get("Content-Length", ""))
            if size < 0:
                raise ValueError
        except ValueError:
            return self._reject(411, "Upload needs a known size")
        if CONFIG["max_bytes"] and size > CONFIG["max_bytes"]:
            return self._reject(413, "File is bigger than the host allows (%d GB)"
                                % (CONFIG["max_bytes"] // 2**30))
        try:
            free = shutil.disk_usage(str(root)).free
        except OSError:
            return self._reject(500, "Storage folder is not available on the host")
        if size + RESERVE_BYTES > free:
            return self._reject(507, "Not enough free space on the host drive")

        tmp = root / (".upload-%s.part" % secrets.token_hex(8))
        received, ok, err = 0, False, None
        try:
            with open(tmp, "wb") as f:
                while received < size:
                    try:
                        chunk = self.rfile.read(min(CHUNK, size - received))
                    except OSError:
                        chunk = b""
                    if not chunk:
                        raise ClientGone()
                    f.write(chunk)
                    received += len(chunk)
            with fs_lock:
                final = unique_path(root, name)
                os.rename(tmp, final)
            ok = True
        except ClientGone:
            err = "gone"
        except OSError as e:
            err = e
        finally:
            if not ok:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        if ok:
            emit("Received %s (%d bytes)" % (final.name, size))
            return self._json(201, {"name": final.name, "size": size})
        self.close_connection = True
        if err == "gone":
            emit("Upload of %s stopped early (device disconnected or cancelled)" % name)
            return
        msg = ("The host drive is full" if getattr(err, "errno", None) == errno.ENOSPC
               else "The host could not write the file (is the drive still connected?)")
        self._json(500, {"error": msg})

    def _login(self, data):
        ip = self.client_address[0]
        now = time.time()
        with lock:
            count, locked_until = fails.get(ip, [0, 0])
            if locked_until > now:
                return self._json(429, {"error": "Too many attempts. Try again in %ds."
                                        % (int(locked_until - now) + 1)})
        pin = str(data.get("pin") or "")[:64]
        if hmac.compare_digest(pin.encode(), CONFIG["pin"].encode()):
            with lock:
                fails.pop(ip, None)
            token = self._open_session()
            return self._json(200, {"ok": True},
                              [("Set-Cookie", self._cookie(token, SESSION_TTL))])
        with lock:
            count += 1
            fails[ip] = [0, now + LOCK_SECONDS] if count >= MAX_FAILS else [count, 0]
        time.sleep(0.5)
        self._json(401, {"error": "Wrong PIN"})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32


def make_cert(ips, base):
    cert, key = base / "lanshare-cert.pem", base / "lanshare-key.pem"
    if not (cert.exists() and key.exists()):
        print("Generating self-signed certificate...")
        san = ",".join(["IP:%s" % i for i in ips] + ["IP:127.0.0.1", "DNS:localhost"])
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
             "-keyout", str(key), "-out", str(cert), "-subj", "/CN=lanshare",
             "-addext", "subjectAltName=" + san],
            check=True, capture_output=True)
        try:
            key.chmod(0o600)
        except OSError:
            pass
    return cert, key


def default_folder():
    home = Path.home()
    shared = home / "storage" / "shared"     # Termux, after `termux-setup-storage`
    return (shared if shared.is_dir() else home) / "LANShare"


def gb(n):
    return "%.1f GB" % (n / 2**30)


def prepare_folder(path):
    root = Path(path).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve()
        probe = root / (".probe-%s" % secrets.token_hex(4))
        probe.write_bytes(b"ok")
        probe.unlink()
        for old in root.glob(".upload-*.part"):     # leftovers from an interrupted run
            old.unlink()
    except OSError as e:
        raise FolderError("Cannot write to the folder %s\n  %s\n"
                          "  Check the path exists, the drive is plugged in, and you have permission."
                          % (root, e))
    return root


def serve(root, pin, port=8080, https=False, max_gb=0, allow_delete=True,
          host_ui=False, cert_dir=None):
    """Start the server in a background thread and return it. Stop it with stop(srv)."""
    if len(pin) < 4:
        raise ValueError("PIN must be at least 4 characters")
    root = prepare_folder(root)
    with lock:
        sessions.clear()
        fails.clear()
    CONFIG.update(pin=pin, https=https, root=root, max_bytes=int(max_gb * 2**30),
                  allow_delete=allow_delete, host_ui=host_ui)
    try:
        srv = Server(("0.0.0.0", port), Handler)
    except OSError as e:
        raise PortError("Cannot use port %d (%s). Close the other program or pick another port."
                        % (port, e.strerror or e))
    if https:
        if ssl is None:
            srv.server_close()
            raise ValueError("HTTPS is not available on this device")
        cert, key = make_cert(lan_ips(), Path(cert_dir or Path.cwd()))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def stop(srv):
    try:
        srv.shutdown()
    finally:
        srv.server_close()


def main():
    ap = argparse.ArgumentParser(description="Share files over Wi-Fi or a hotspot, no internet needed")
    ap.add_argument("--dir", help="folder where files are stored (default: ~/LANShare). "
                                  "Point it at your SSD, e.g. D:\\LANShare or /Volumes/SSD/LANShare")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--pin", help="set your own PIN (default: random 6 digits)")
    ap.add_argument("--https", action="store_true", help="encrypt traffic (needs openssl)")
    ap.add_argument("--max-gb", type=float, default=0, help="refuse single files bigger than this")
    ap.add_argument("--no-delete", action="store_true", help="don't let other devices delete files")
    args = ap.parse_args()

    pin = args.pin or os.environ.get("LANSHARE_PIN") or "%06d" % secrets.randbelow(10**6)
    if len(pin) < 4:
        ap.error("PIN must be at least 4 characters")

    try:
        srv = serve(Path(args.dir) if args.dir else default_folder(), pin, args.port, args.https,
                    args.max_gb, not args.no_delete, cert_dir=Path(__file__).resolve().parent)
    except (FolderError, PortError) as e:
        sys.exit(str(e))
    root, scheme, ips = CONFIG["root"], "https" if args.https else "http", lan_ips()

    du = shutil.disk_usage(str(root))
    print("\n  LANShare is running (no internet needed)")
    print("  Files are saved in: %s   (%s free)" % (root, gb(du.free)))
    print("  Open one of these on any device on the same Wi-Fi / hotspot:")
    if ips:
        for ip in ips:
            print("      %s://%s:%d" % (scheme, ip, args.port))
    else:
        print("      (could not detect this device's address - look it up in the Wi-Fi/hotspot")
        print("       settings and open  %s://<that address>:%d )" % (scheme, args.port))
    print("  PIN: %s" % pin)
    if not args.https:
        print("  Note: traffic is unencrypted. Add --https on shared/public Wi-Fi.")
    print("  Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopped.")
        stop(srv)


if __name__ == "__main__":
    main()
