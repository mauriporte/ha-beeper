// Beeper chat card: chat list + conversation + reply box, talking to the beeper integration over HA's WebSocket.
// Message text and titles always go through textContent (never innerHTML): they come from other people.
// Links are built as <a> elements (http/https only) and media load through the integration's signed asset URLs.
// Formatting: HTML messages are parsed inert (DOMParser runs nothing) and only an allowlist of tags is rebuilt, with no
// attributes except a checked href; plain messages get WhatsApp-style *bold* _italic_ ~strike~ `code`.

const NETWORK_ICONS = {
  whatsapp: "mdi:whatsapp", telegram: "mdi:send", signal: "mdi:message-lock", instagram: "mdi:instagram",
  facebook: "mdi:facebook-messenger", messenger: "mdi:facebook-messenger", discord: "mdi:discord", slack: "mdi:slack",
  linkedin: "mdi:linkedin", twitter: "mdi:twitter", x: "mdi:twitter", googlemessages: "mdi:message-text",
  gmessages: "mdi:message-text", imessage: "mdi:apple", beeper: "mdi:alpha-b-circle",
};

const STYLE = `
  :host { display: block; }
  ha-card { display: flex; height: var(--beeper-height, 560px); overflow: hidden; }
  .list { width: 34%; min-width: 200px; border-right: 1px solid var(--divider-color); overflow-y: auto; }
  .conv { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .chat { display: flex; gap: 10px; align-items: center; padding: 10px 12px; cursor: pointer; border-bottom: 1px solid var(--divider-color); }
  .chat:hover, .chat.sel { background: var(--secondary-background-color); }
  .chat ha-icon { color: var(--secondary-text-color); flex: none; --mdc-icon-size: 20px; }
  .meta { flex: 1; min-width: 0; }
  .title { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .preview { color: var(--secondary-text-color); font-size: 0.85em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .badge { background: var(--primary-color); color: var(--text-primary-color); border-radius: 10px; padding: 0 7px; font-size: 0.8em; min-width: 8px; text-align: center; }
  .head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--divider-color); font-weight: 500; min-height: 24px; }
  .back { display: none; cursor: pointer; }
  .msgs { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 6px; }
  .msg { max-width: 75%; padding: 6px 10px; border-radius: 12px; background: var(--secondary-background-color); white-space: pre-wrap; word-wrap: break-word; }
  .msg.mine { align-self: flex-end; background: var(--primary-color); color: var(--text-primary-color); }
  .sender { font-size: 0.75em; font-weight: 600; opacity: 0.8; }
  .time { font-size: 0.7em; opacity: 0.7; text-align: right; }
  .msg a { color: inherit; text-decoration: underline; word-break: break-all; }
  .media { display: block; margin: 4px 0; max-width: 100%; max-height: 320px; border-radius: 8px; background: rgba(127,127,127,.15); }
  img.media { width: auto; height: auto; cursor: zoom-in; }
  .media.sticker { max-height: 140px; background: none; }
  audio.media { width: 260px; max-width: 100%; height: 40px; background: none; }
  .file { display: inline-block; margin: 4px 0; }
  .reel-btn { display: block; margin: 6px 0 2px; padding: 6px 12px; font-size: .9em; }
  .map { display: block; margin: 6px 0; width: 100%; max-width: 420px; height: 300px; border: 0; border-radius: 8px; }
  video.reel { max-width: 360px; max-height: 560px; background: #000; }
  .msg blockquote { margin: 4px 0; padding-left: 8px; border-left: 3px solid currentColor; opacity: .85; }
  .msg p { margin: 0 0 4px; }
  .msg ul, .msg ol { margin: 2px 0; padding-left: 20px; }
  .msg code, .msg pre { font-family: var(--code-font-family, monospace); font-size: .9em; background: rgba(127,127,127,.2); border-radius: 4px; padding: 0 3px; }
  .msg pre { padding: 4px 6px; white-space: pre-wrap; }
  .more { align-self: center; }
  .compose { display: flex; gap: 8px; padding: 8px 12px; border-top: 1px solid var(--divider-color); }
  textarea { flex: 1; resize: none; font: inherit; padding: 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  button { font: inherit; cursor: pointer; border: none; border-radius: 8px; padding: 0 14px; background: var(--primary-color); color: var(--text-primary-color); }
  button:disabled { opacity: 0.5; cursor: default; }
  .empty, .error { margin: auto; color: var(--secondary-text-color); padding: 16px; text-align: center; }
  .error { color: var(--error-color); }
  @media (max-width: 700px) {
    .list { width: 100%; border-right: none; }
    .conv { display: none; }
    ha-card.open .list { display: none; }
    ha-card.open .conv { display: flex; }
    .back { display: inline-block; }
  }
`;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

const URL_RE = /https?:\/\/[^\s<>"']+/g;

// Safe rich text: these tags are rebuilt (no attributes, except a checked href on <a>); unknown tags are unwrapped,
// dangerous ones dropped with their content.
const KEEP = new Set(["B", "STRONG", "I", "EM", "U", "S", "DEL", "STRIKE", "CODE", "PRE", "BLOCKQUOTE", "P", "BR", "UL", "OL", "LI", "A"]);
const DROP = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "SVG", "MATH", "TEMPLATE", "NOSCRIPT", "IMG", "VIDEO", "AUDIO", "FORM", "INPUT", "BUTTON", "TEXTAREA", "SELECT", "LINK", "META"]);
const HTML_RE = /<\/?(strong|b|em|i|u|s|del|strike|code|pre|blockquote|p|br|ul|ol|li|a|span|div)\b[^>]*>/i;

function link(url, label) {
  const a = el("a", null, label ?? url);
  if (/^https?:\/\//i.test(url)) { a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; }
  return a;
}

function rebuild(node, out, inLink) {
  for (const n of node.childNodes) {
    if (n.nodeType === Node.TEXT_NODE) { out.append(inLink ? n.textContent : linkified(n.textContent)); continue; }
    if (n.nodeType !== Node.ELEMENT_NODE || DROP.has(n.tagName)) continue;
    if (!KEEP.has(n.tagName)) { rebuild(n, out, inLink); continue; }  // e.g. <span>, <div>: keep the content only
    if (n.tagName === "A") {
      const a = link(n.getAttribute("href") || "", "");
      rebuild(n, a, true);
      out.append(a);
      continue;
    }
    const e = document.createElement(n.tagName);
    rebuild(n, e, inLink);
    out.append(e);
  }
}

// Plain text: links, then *bold* _italic_ ~strike~ `code` (WhatsApp style) and **bold** around them.
const MARK_RE = /(?<![\p{L}\p{N}_*~`])(\*\*[^*\n]+\*\*|\*[^*\s](?:[^*\n]*[^*\s])?\*|_[^_\s](?:[^_\n]*[^_\s])?_|~[^~\s](?:[^~\n]*[^~\s])?~|`[^`\n]+`)(?![\p{L}\p{N}_*~`])/u;  // only at word edges, like WhatsApp
const MARK_TAG = { "**": "strong", "*": "strong", "_": "em", "~": "s", "`": "code" };

function marked(text) {
  const frag = document.createDocumentFragment();
  for (const part of text.split(MARK_RE)) {
    if (!part) continue;
    const m = part.match(/^(\*\*|\*|_|~|`)([\s\S]+)\1$/);
    if (m && MARK_RE.test(part)) { const e = el(MARK_TAG[m[1]], null, m[2]); frag.append(e); }
    else frag.append(part);
  }
  return frag;
}

function linkified(text) {
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const m of text.matchAll(URL_RE)) {
    let url = m[0];
    const trail = url.match(/[).,!?;:]+$/);
    if (trail) url = url.slice(0, -trail[0].length);
    frag.append(marked(text.slice(last, m.index)), link(url));
    last = m.index + url.length;
  }
  frag.append(marked(text.slice(last)));
  return frag;
}

function richText(text) {
  const box = el("div");
  if (HTML_RE.test(text)) rebuild(new DOMParser().parseFromString(text, "text/html").body, box, false);
  else box.append(linkified(text));
  return box;
}

// Instagram reels/posts linked in a message, played inline. Instagram's own embed won't play reels (it sends you to
// the app), so the video comes from an optional helper on the PC that resolves the public video file: the
// `sensor.vault_images` entity gives its key and LAN address (https pages use the same origin's /vault-img route).
// Without that helper the button is not shown and the link opens Instagram as usual. Loaded only when tapped.
const IG_RE = /https?:\/\/(?:www\.)?instagram\.com\/(?:[\w.]+\/)?(reels?|p|tv)\/([\w-]{5,})/gi;

function instagramPosts(text) {
  const seen = new Map();
  for (const m of text.matchAll(IG_RE)) seen.set(m[2], m[1].toLowerCase().startsWith("reel") ? "reel" : "p");
  return [...seen].map(([code, kind]) => ({ code, kind }));
}

function reelPlayer(post, hass) {
  const helper = hass.states["sensor.vault_images"]?.attributes;
  if (!helper?.key) return null;
  const base = location.protocol === "https:" ? "" : (helper.lan || "");
  const btn = el("button", "reel-btn", post.kind === "reel" ? "▶ Ver reel" : "▶ Ver vídeo");
  btn.addEventListener("click", () => {
    const v = el("video", "media reel");
    v.controls = v.autoplay = v.playsInline = true;
    v.preload = "auto";
    v.src = `${base}/vault-img/${encodeURIComponent(helper.key)}/reel/${encodeURIComponent(post.code)}`;
    v.addEventListener("error", () => v.replaceWith(el("div", "time", "No se pudo reproducir (¿cuenta privada?). Abre el enlace en Instagram.")), { once: true });
    btn.replaceWith(v);
  }, { once: true });
  return btn;
}

// Google Maps links: Google's keyless map embed, loaded only when tapped. Full links say the place themselves; share
// links (maps.app.goo.gl) are looked up once by the same PC helper as the reels (without it they're skipped).
const GMAPS_RE = /https?:\/\/(?:(?:www\.|maps\.)?google\.[a-z.]+\/maps[^\s<>"']*|maps\.google\.[a-z.]+\/[^\s<>"']*|maps\.app\.goo\.gl\/[\w-]+|goo\.gl\/maps\/[\w-]+)/gi;

function mapEmbed(url) {  // same rules as the PC helper (links.py maps_embed_url)
  let u;
  try { u = new URL(url); } catch { return ""; }
  const coords = u.pathname.match(/@(-?\d+\.\d+),(-?\d+\.\d+)(?:,(\d+(?:\.\d+)?)z)?/);
  const place = u.pathname.match(/\/maps\/(?:place|search)\/([^/@]+)/);
  const q = u.searchParams.get("query") || u.searchParams.get("q")
    || (place ? decodeURIComponent(place[1].replace(/\+/g, " ")) : "") || (coords ? `${coords[1]},${coords[2]}` : "");
  if (!q || /\/maps\/dir\//.test(u.pathname)) return "";
  const z = coords?.[3] ? Math.round(+coords[3]) : 15;
  return `https://maps.google.com/maps?q=${encodeURIComponent(q)}&z=${z}&output=embed`;
}

function mapLinks(text) {
  const out = new Map();
  for (const m of text.matchAll(GMAPS_RE)) {
    const url = m[0].replace(/[).,!?;:]+$/, "");
    out.set(url, /goo\.gl/i.test(url) ? "short" : mapEmbed(url));
  }
  return [...out].filter(([, e]) => e);
}

function mapPlayer(url, embed, hass) {
  let src = embed;
  if (embed === "short") {
    const helper = hass.states["sensor.vault_images"]?.attributes;
    if (!helper?.key) return null;
    const base = location.protocol === "https:" ? "" : (helper.lan || "");
    src = `${base}/vault-img/${encodeURIComponent(helper.key)}/maps?u=${encodeURIComponent(url)}`;
  }
  const btn = el("button", "reel-btn", "🗺 Ver mapa");
  btn.addEventListener("click", () => {
    const f = el("iframe", "map");
    f.src = src;
    f.loading = "lazy";
    f.referrerPolicy = "no-referrer-when-downgrade";
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox");
    btn.replaceWith(f);
  }, { once: true });
  return btn;
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

class BeeperChatCard extends HTMLElement {
  setConfig(config) {
    this._config = { show_archived: false, show_muted: true, ...config };
    if (config.height) this.style.setProperty("--beeper-height", config.height);
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._init();
  }

  getCardSize() { return 10; }
  getGridOptions() { return { columns: 12, rows: 8, min_columns: 6 }; }

  _init() {
    const root = this.attachShadow({ mode: "open" });
    root.appendChild(el("style", null, STYLE));
    this._card = el("ha-card");
    this._list = el("div", "list");
    const conv = el("div", "conv");
    this._head = el("div", "head");
    this._msgs = el("div", "msgs");
    const compose = el("div", "compose");
    this._input = el("textarea");
    this._input.rows = 2;
    this._input.placeholder = "Mensaje…";
    this._send = el("button", null, "Enviar");
    compose.append(this._input, this._send);
    conv.append(this._head, this._msgs, compose);
    this._card.append(this._list, conv);
    root.appendChild(this._card);

    this._send.addEventListener("click", () => this._sendMessage());
    this._input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._sendMessage(); }
    });
    this._renderConversation();
    this._loadChats();
    this._subscribe();
  }

  disconnectedCallback() {
    if (this._unsub) { this._unsub.then((u) => u()); this._unsub = null; }
  }

  connectedCallback() {
    if (this._hass && !this._unsub) { this._subscribe(); this._loadChats(); }
  }

  _subscribe() {
    this._unsub = this._hass.connection.subscribeEvents((ev) => {
      clearTimeout(this._listTimer);
      this._listTimer = setTimeout(() => this._loadChats(), 1000);  // bursts of events → one reload
      if (ev.data.chat_id === this._chatId) {
        clearTimeout(this._msgTimer);
        this._msgTimer = setTimeout(() => this._loadMessages(true), 700);
      }
    }, "beeper_chat_update");
  }

  async _loadChats() {
    try {
      const res = await this._hass.callWS({ type: "beeper/chats" });
      this._chats = res.chats.filter((c) => (this._config.show_archived || !c.archived) && (this._config.show_muted || !c.muted));
      this._available = res.available;
    } catch (err) {
      this._chats = [];
      this._listError = err.message || String(err);
    }
    this._renderList();
  }

  _renderList() {
    this._list.replaceChildren();
    if (this._listError) { this._list.appendChild(el("div", "error", this._listError)); return; }
    if (!this._available) this._list.appendChild(el("div", "error", "Beeper Desktop no responde (¿PC apagado?)"));
    const chats = [...this._chats].sort((a, b) => (b.pinned - a.pinned) || (b.last_activity || "").localeCompare(a.last_activity || ""));
    for (const c of chats) {
      const row = el("div", "chat" + (c.id === this._chatId ? " sel" : ""));
      const icon = el("ha-icon");
      icon.icon = c.pinned ? "mdi:pin" : (NETWORK_ICONS[(c.network || "").toLowerCase().replace(/[^a-z]/g, "")] || "mdi:chat");
      const meta = el("div", "meta");
      meta.append(el("div", "title", c.title), el("div", "preview", c.preview || ""));
      row.append(icon, meta);
      if (c.unread) row.appendChild(el("span", "badge", String(c.unread)));
      row.addEventListener("click", () => this._open(c));
      this._list.appendChild(row);
    }
  }

  async _open(chat) {
    this._chatId = chat.id;
    this._chat = chat;
    this._messages = [];
    this._cursor = null;
    this._card.classList.add("open");
    this._renderList();
    this._renderConversation();
    await this._loadMessages(true);
    if (chat.unread) this._hass.callWS({ type: "beeper/mark_read", chat_id: chat.id }).catch(() => {});
  }

  async _loadMessages(latest) {
    if (!this._chatId) return;
    const chatId = this._chatId;
    try {
      const req = { type: "beeper/messages", chat_id: chatId };
      if (!latest && this._cursor) req.cursor = this._cursor;
      const res = await this._hass.callWS(req);
      if (chatId !== this._chatId) return;  // the user switched chats meanwhile
      if (latest) {
        const older = this._messages.filter((m) => !res.messages.some((n) => n.id === m.id));
        const keepOlder = this._cursor ? older : [];
        this._messages = [...keepOlder, ...res.messages];
        if (!this._cursor) { this._cursor = res.cursor; this._hasMore = res.has_more; }
      } else {
        this._messages = [...res.messages, ...this._messages];
        this._cursor = res.cursor;
        this._hasMore = res.has_more;
      }
      this._msgError = null;
    } catch (err) {
      this._msgError = err.message || String(err);
    }
    this._renderConversation(latest);
  }

  _renderConversation(scrollToEnd = true) {
    this._head.replaceChildren();
    const back = el("ha-icon", "back");
    back.icon = "mdi:arrow-left";
    back.addEventListener("click", () => { this._card.classList.remove("open"); });
    this._head.append(back, el("span", null, this._chat ? this._chat.title : "Beeper"));
    this._msgs.replaceChildren();
    const canSend = this._chat && !this._chat.read_only;
    this._input.disabled = !canSend;
    this._send.disabled = !canSend;
    if (!this._chat) { this._msgs.appendChild(el("div", "empty", "Elige un chat")); return; }
    if (this._msgError) this._msgs.appendChild(el("div", "error", this._msgError));
    if (this._hasMore) {
      const more = el("button", "more", "Cargar anteriores");
      more.addEventListener("click", () => this._loadMessages(false));
      this._msgs.appendChild(more);
    }
    const group = this._chat.type === "group";
    for (const m of this._messages) {
      const b = el("div", "msg" + (m.mine ? " mine" : ""));
      if (group && !m.mine) b.appendChild(el("div", "sender", m.sender || ""));
      for (const a of m.attachments || []) b.appendChild(this._media(a));
      if (m.text) {
        b.appendChild(richText(m.text));
        for (const post of instagramPosts(m.text)) { const p = reelPlayer(post, this._hass); if (p) b.appendChild(p); }
        for (const [url, embed] of mapLinks(m.text)) { const p = mapPlayer(url, embed, this._hass); if (p) b.appendChild(p); }
      }
      b.appendChild(el("div", "time", fmtTime(m.time) + (m.mine && m.status && m.status !== "SUCCESS" ? " · " + m.status.toLowerCase() : "")));
      this._msgs.appendChild(b);
    }
    if (scrollToEnd) this._msgs.scrollTop = this._msgs.scrollHeight;
  }

  // <img>/<video>/<audio> can't send HA's token, so each file gets a signed URL (valid 24 h, cached per file).
  async _signed(url) {
    this._signedUrls ??= new Map();
    if (!this._signedUrls.has(url)) {
      this._signedUrls.set(url, this._hass.callWS({
        type: "auth/sign_path", path: "/api/beeper/asset?url=" + encodeURIComponent(url), expires: 86400,
      }).then((r) => r.path));
    }
    return this._signedUrls.get(url);
  }

  _media(a) {
    let node;
    if (a.type === "img") {
      node = el("img", "media" + (a.sticker ? " sticker" : ""));
      node.loading = "lazy";
      node.alt = a.name || "imagen";
      if (a.width && a.height) { node.width = a.width; node.height = a.height; }  // keeps the layout still while loading
      node.addEventListener("click", async () => window.open(await this._signed(a.url), "_blank", "noopener"));
      node.style.cursor = "zoom-in";
    } else if (a.type === "video") {
      node = el("video", "media");
      node.controls = true;
      node.preload = "metadata";
      node.playsInline = true;
      if (a.gif) { node.autoplay = node.loop = node.muted = true; }
      if (a.poster) this._signed(a.poster).then((p) => { node.poster = p; });
    } else if (a.type === "audio") {
      node = el("audio", "media");
      node.controls = true;
      node.preload = "none";
    } else {
      node = el("a", "file", "📎 " + (a.name || "archivo"));
      node.target = "_blank";
      node.rel = "noopener noreferrer";
      this._signed(a.url).then((p) => { node.href = p; });
      return node;
    }
    this._signed(a.url).then((p) => { node.src = p; }).catch(() => node.replaceWith(el("div", "time", "[no se pudo cargar]")));
    return node;
  }

  async _sendMessage() {
    const text = this._input.value.trim();
    if (!text || !this._chatId || this._sending) return;
    this._sending = true;
    this._send.disabled = true;
    try {
      await this._hass.callWS({ type: "beeper/send", chat_id: this._chatId, text });
      this._input.value = "";
      setTimeout(() => this._loadMessages(true), 800);
    } catch (err) {
      this._msgError = "No se pudo enviar: " + (err.message || err);
      this._renderConversation();
    } finally {
      this._sending = false;
      this._send.disabled = false;
      this._input.focus();
    }
  }

  static getStubConfig() { return {}; }
}

if (!customElements.get("beeper-chat-card")) {
  customElements.define("beeper-chat-card", BeeperChatCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "beeper-chat-card", name: "Beeper chat", description: "Read and reply to your Beeper chats." });
}
