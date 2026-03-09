# LinkedIn Post — OpenQueen Launch

**Score: 7.5/10**

Good personal story structure. The "dinner" anecdote works but is generic — replacing it with a specific real task (actual project name, actual outcome) would make it significantly more credible and shareable. LinkedIn rewards specificity and authentic detail. The question at the end is strong.

**Timing:** Tuesday or Wednesday, 8–11am CET. Do not post Friday.

---

## Draft

```
I got tired of babysitting AI agents.

You know the drill: you give Claude a task, then you sit there watching the terminal,
checking if it's still running, hoping it doesn't get stuck.

So I built something: OpenQueen.

You control it from WhatsApp.

Text "add dark mode to the dashboard" from your phone.
Gemini compiles it into a structured task.
Claude executes it on your server.
You get a WhatsApp notification when it's done — with proof.

Last week I used it to fix the citation validator in OpenPaper — a bug that
had been failing on null DOIs. Sent the task from my phone, went to make coffee.
Got the Done notification 38 minutes later. 3 tests added, 1 commit, all green.

The architecture is simple:
→ WhatsApp listens for messages
→ Gemini 3.1 Pro acts as orchestrator, compiles NL into tasks
→ Claude (or Codex) does the actual work
→ A watchdog monitors, enforces Done When conditions, notifies you

It runs as a systemd service on your Linux server. Always on.

It's open source. MIT. 30-second install.

→ openqueen.buildingopen.org
→ github.com/buildingopen/openqueen

Curious: how do you currently handle long-running AI agent tasks?
Do you babysit them or do you have a better system?

#OpenSource #AIAgents #DeveloperTools #Claude #BuildingOpen
```

---

## What would push this to 9/10

- Replace "I used it last week to refactor a module while I was having dinner" with the actual story: real project, real task, real time it took. E.g. "I used it to migrate my OpenPaper backend from REST to GraphQL while I was on a 3-hour train to Hamburg."
- Attach the WhatsApp screenshot (incoming task + outgoing Done notification) as an image — LinkedIn heavily boosts posts with images
- Add 3–5 relevant hashtags at the end: #OpenSource #AIAgents #DeveloperTools #Claude #BuildingOpen
