const { default: makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion } = require("@whiskeysockets/baileys");
const path = require("path");

const AUTH_DIR = path.join(__dirname, "../wa-listener/auth");
const GROUP_JID = "120363424057336289@g.us";

async function main() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version, auth: state,
    printQRInTerminal: false,
    logger: require("pino")({ level: "silent" }),
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async ({ connection }) => {
    if (connection === "open") {
      try {
        const msgs = await sock.fetchMessagesFromWA(GROUP_JID, 10);
        console.log("=== Last 10 messages ===");
        for (const m of msgs) {
          const text = m.message?.conversation || m.message?.extendedTextMessage?.text || "[media]";
          const from = m.key.fromMe ? "ME" : m.key.participant || m.key.remoteJid;
          const ts = new Date(m.messageTimestamp * 1000).toISOString().slice(11,16);
          console.log(`[${ts}] ${from.slice(0,20)}: ${text.slice(0,80)}`);
        }
      } catch(e) {
        // Try loadMessages instead
        try {
          const store = {};
          const msgs = await sock.loadMessages(GROUP_JID, 10);
          console.log(JSON.stringify(msgs?.messages?.slice(-10).map(m => ({
            from: m.key.fromMe ? "ME" : m.key.participant,
            text: m.message?.conversation || m.message?.extendedTextMessage?.text || "[other]",
            ts: new Date(m.messageTimestamp * 1000).toISOString().slice(11,16)
          })), null, 2));
        } catch(e2) {
          console.log("fetch error:", e.message, e2.message);
        }
      }
      process.exit(0);
    }
  });

  setTimeout(() => { console.log("timeout"); process.exit(1); }, 15000);
}

main();
