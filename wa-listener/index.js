#!/usr/bin/env node
/**
 * openqueen-wa-listener — standalone WhatsApp connection for openqueen.
 * Receives messages from Federico, sends replies back.
 * Clawdbot is not involved at all.
 *
 * Exposes POST http://127.0.0.1:19234/send { text, image? }
 * so openqueen (agent.py) can send notifications back.
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

const AUTH_DIR = path.join(__dirname, "auth");
const DISPATCH = path.join(__dirname, "..", "dispatch.py");
const LOGS_DIR = "/root/openqueen/logs";
const PORT = 19234;

// Group JID to listen on — set via env var after first link
const GROUP_JID = process.env.OQ_GROUP_JID || "";

let sock = null;
let replyJid = GROUP_JID;

fs.mkdirSync(AUTH_DIR, { recursive: true });
fs.mkdirSync(LOGS_DIR, { recursive: true });

// ── HTTP send API ─────────────────────────────────────────────────────────────

const server = http.createServer((req, res) => {
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
        await sock.sendMessage(jid, {
          image: fs.readFileSync(image),
          caption: text || "",
        });
      } else {
        await sock.sendMessage(jid, { text: text || "" });
      }
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
    }
    if (connection === "open") {
      console.log("[wa-listener] Connected to WhatsApp");
    }
    if (connection === "close") {
      const code = lastDisconnect?.error instanceof Boom
        ? lastDisconnect.error.output.statusCode
        : 0;
      if (code === DisconnectReason.loggedOut) {
        console.error("[wa-listener] Logged out — delete auth/ and restart");
        process.exit(1);
      }
      console.log(`[wa-listener] Disconnected (${code}), reconnecting in 5s...`);
      setTimeout(connect, 5000);
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      if (!msg.message) continue;

      const jid = msg.key.remoteJid;
      // Refuse ALL messages unless OQ_GROUP_JID is explicitly configured
      if (!GROUP_JID) { console.log(`[wa-listener] OQ_GROUP_JID not set — ignoring jid=${jid}`); continue; }
      if (jid !== GROUP_JID) continue;

      replyJid = jid;

      const text =
        msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        msg.message?.imageMessage?.caption ||
        "";

      if (!text.trim()) continue;

      console.log(`[wa-listener] jid=${jid} → ${text.slice(0, 100)}`);

      const logFile = `${LOGS_DIR}/dispatch-${Date.now()}.log`;
      const out = fs.openSync(logFile, "a");
      const proc = spawn("python3", [DISPATCH, text.trim()], {
        detached: true,
        stdio: ["ignore", out, out],
        env: { ...process.env },
      });
      proc.unref();
      fs.closeSync(out);
    }
  });
}

connect();
