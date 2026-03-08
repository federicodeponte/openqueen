#!/usr/bin/env node
/**
 * openqueen-wa-listener — standalone WhatsApp connection for openqueen.
 * Receives messages from Federico, sends replies back.
 *
 * Exposes POST http://127.0.0.1:19234/send { text, image? }
 * so openqueen (agent.py) can send notifications back.
 *
 * No commands to memorize — natural language understood for status/stop/log.
 */

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const { Boom } = require("@hapi/boom");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const qrcode = require("qrcode-terminal");
const path = require("path");
const os = require("os");

const AUTH_DIR  = path.join(__dirname, "auth");
const DISPATCH  = path.join(__dirname, "..", "dispatch.py");
const LOGS_DIR  = "/root/openqueen/logs";
const SENTINEL  = "/root/openqueen/WA_NEEDS_RELINK";
const QR_FILE   = "/root/openqueen/wa-qr.txt";
const QUEUE_FILE = process.env.OQ_QUEUE_FILE || path.join(process.env.OPENQUEEN_HOME || path.join(os.homedir(), "openqueen"), "QUEUE.json");
const PORT = 19234;

const GROUP_JID = process.env.OQ_GROUP_JID || "";

let sock = null;
let replyJid = GROUP_JID;

fs.mkdirSync(AUTH_DIR, { recursive: true });
fs.mkdirSync(LOGS_DIR, { recursive: true });

// ── Intent detection — no commands to memorize ────────────────────────────────

function detectIntent(text) {
  const t = text.toLowerCase().trim();
  const short = t.length < 50;

  // Stop intent: "stop", "cancel it", "kill it", "abort", "stop that", "stop the task"
  if (short && /\b(stop|cancel|abort|halt|kill)\b/.test(t)) return "stop";

  // Status intent: "?", "what's running", "running?", "status", "what are you doing"
  if (short && /^\?+$|^status\??$|^running\??$|what.{0,15}running|what.{0,15}doing|what.{0,15}working/.test(t)) return "status";

  // Log intent: "log", "what's happening", "how's it going", "progress", "any updates"
  if (short && /^logs?\??$|what.{0,20}happen|how.{0,15}go(ing)?|progress\??|any updates?/.test(t)) return "log";

  // Resume intent: "resume", "continue", "pick up", "carry on"
  if (short && /\bresume\b|\bcontinue\b|pick.{0,5}up|carry.{0,5}on/.test(t)) return "resume";

  return null; // treat as a task
}

// ── Dispatch helper ────────────────────────────────────────────────────────────

function runDispatch(arg) {
  const logFile = `${LOGS_DIR}/dispatch-${Date.now()}.log`;
  const out = fs.openSync(logFile, "a");
  const proc = spawn("python3", [DISPATCH, arg], {
    detached: true,
    stdio: ["ignore", out, out],
    env: { ...process.env },
  });
  proc.unref();
  fs.closeSync(out);
}

// ── Write to queue file (standalone mode, no clawdbot) ───────────────────────

function writeToQueue(text) {
  const key = ;
  const entry = { [key]: { task_path: null, nl: text, ts: new Date().toISOString() } };
  // Merge with existing queue if present (unlikely but safe)
  let queue = {};
  try { queue = JSON.parse(fs.readFileSync(QUEUE_FILE, "utf8")); } catch (_) {}
  Object.assign(queue, entry);
  fs.mkdirSync(path.dirname(QUEUE_FILE), { recursive: true });
  fs.writeFileSync(QUEUE_FILE, JSON.stringify(queue, null, 2));
  console.log();
}

// ── HTTP send API ─────────────────────────────────────────────────────────────

const server = http.createServer((req, res) => {
  if (req.url === "/health") {
    res.writeHead(200);
    res.end('{"ok":true}');
    return;
  }
  if (req.method !== "POST" || req.url !== "/send") {
    res.writeHead(404);
    res.end();
    return;
  }
  let body = "";
  req.on("data", (d) => (body += d));
  req.on("end", async () => {
    try {
      const { text, image } = JSON.parse(body);
      if (!sock) throw new Error("Not connected");
      const jid = replyJid;
      if (image && fs.existsSync(image)) {
        await sock.sendMessage(jid, { image: fs.readFileSync(image), mimetype: "image/png", caption: text || "" });
      } else {
        await sock.sendMessage(jid, { text: text || "" });
      }
      console.log(`[wa-listener] sent → ${jid}`);
      res.writeHead(200);
      res.end('{"ok":true}');
    } catch (e) {
      console.error("[send]", e.message);
      res.writeHead(500);
      res.end(JSON.stringify({ error: e.message }));
    }
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[wa-listener] send API: http://127.0.0.1:${PORT}/send`);
});

// ── WhatsApp connection ───────────────────────────────────────────────────────

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger: require("pino")({ level: "silent" }),
    browser: ["OpenQueen", "Chrome", "1.0.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log("[wa-listener] Scan this QR code to link WhatsApp:");
      qrcode.generate(qr, { small: true });
      try {
        let qrText = "";
        qrcode.generate(qr, { small: true }, (s) => { qrText = s; });
        fs.writeFileSync(QR_FILE, `Generated: ${new Date().toISOString()}\n\n${qrText}\n`);
      } catch (_) {}
    }
    if (connection === "open") {
      console.log("[wa-listener] Connected to WhatsApp");
      try { fs.unlinkSync(SENTINEL); } catch (_) {}
      try { fs.unlinkSync(QR_FILE); } catch (_) {}
    }
    if (connection === "close") {
      const code = lastDisconnect?.error instanceof Boom
        ? lastDisconnect.error.output.statusCode : 0;
      if (code === DisconnectReason.loggedOut) {
        console.error("[wa-listener] *** LOGGED OUT *** After restart: cat ~/openqueen/wa-qr.txt");
        try { fs.writeFileSync(SENTINEL, `Logged out at ${new Date().toISOString()}\nScan QR: cat ~/openqueen/wa-qr.txt\n`); } catch (_) {}
        try { fs.readdirSync(AUTH_DIR).forEach(f => fs.unlinkSync(path.join(AUTH_DIR, f))); } catch (_) {}
        process.exit(1);
      }
      console.log(`[wa-listener] Disconnected (${code}), reconnecting in 5s...`);
      sock = null;
      setTimeout(connect, 5000);
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      if (!msg.message) continue;

      const jid = msg.key.remoteJid;
      if (!GROUP_JID) { console.log(`[wa-listener] OQ_GROUP_JID not set — ignoring`); continue; }
      if (jid !== GROUP_JID) continue;

      replyJid = jid;

      // ── Input type filtering ──────────────────────────────────────────────
      const msgType = Object.keys(msg.message)[0];
      if (["audioMessage", "videoMessage", "stickerMessage", "pttMessage"].includes(msgType)) {
        await sock.sendMessage(jid, { text: "Type your task — voice/video not supported here." });
        continue;
      }

      const text =
        msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        msg.message?.imageMessage?.caption ||
        "";

      if (!text.trim()) continue;

      // Ignore pure-emoji or reaction messages (no Latin/digit chars)
      if (!/[a-zA-Z0-9?]/.test(text)) continue;

      const trimmed = text.trim();
      console.log(`[wa-listener] → ${trimmed.slice(0, 100)}`);

      // ── Natural language intent detection ─────────────────────────────────
      const intent = detectIntent(trimmed);

      if (intent === "status" || intent === "stop" || intent === "log" || intent === "resume") {
        // Route to dispatch.py which handles these with lock awareness
        runDispatch(`__${intent}__`);
        continue;
      }

      // ── Dispatch task + write to standalone queue ──────────────────────────────
      runDispatch(trimmed);
      writeToQueue(trimmed);
    }
  });
}

connect();
