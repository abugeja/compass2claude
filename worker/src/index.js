/**
 * compass2claude approvals Worker.
 *
 * Telegram sends every button tap and owner message here (webhook), so a tap
 * on "Post to channel" posts within a second, with no poster running.
 *
 * The poster (compass_post.py) still decides what may be posted. It uploads
 * each approved-for-preview batch here, and on every run collects what
 * happened: decisions, feedback messages and the ids of notes sent from here.
 *
 * KV keys
 *   pending:<batch>     the exact messages to post if the owner taps Post
 *   decision:<batch>    what the owner chose, until the poster collects it
 *   inbox:<update_id>   an owner feedback message, until collected
 *   note:<message_id>   a bot reply sent from here, so the poster can tidy it
 *   learnings           copy of learnings.md, served by /learned
 *
 * Secrets (set with `wrangler secret put`): BOT_TOKEN, OWNER_ID, CHANNEL_ID,
 * API_SECRET. Variable: TZ_NAME.
 */

const PENDING_TTL = 60 * 60 * 24 * 21;
const RECORD_TTL = 60 * 60 * 24 * 30;

const HELP =
  "<b>Feedback</b>\n" +
  "Send me any message and it's saved as feedback for the next Claude run. " +
  "Reply to a preview or a note to attach it to that post.\n\n" +
  "/learned shows what has been learned so far.";

const esc = (s) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });

function same(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !b) return false;
  let diff = a.length ^ b.length;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i % b.length);
  return diff === 0;
}

function clock(env) {
  const s = new Intl.DateTimeFormat("en-AU", {
    timeZone: env.TZ_NAME || "Australia/Melbourne",
    hour: "numeric", minute: "2-digit", hour12: true,
  }).format(new Date());
  return s.replace(/\s/g, "").toLowerCase().replace(":00", "");
}

async function tg(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  const body = await r.json().catch(() => ({}));
  if (!body.ok) throw new Error(`Telegram ${method}: ${body.description || r.status}`);
  return body.result;
}

const quiet = (p) => p.catch(() => null);

async function note(env, text) {
  const res = await tg(env, "sendMessage", {
    chat_id: env.OWNER_ID, text, parse_mode: "HTML",
    disable_web_page_preview: true, disable_notification: true,
  });
  await env.STORE.put(`note:${res.message_id}`,
    JSON.stringify({ id: res.message_id, at: Math.floor(Date.now() / 1000) }),
    { expirationTtl: RECORD_TTL });
}

// ------------------------------------------------------------ telegram side

async function onTap(env, cq) {
  const answer = (text) =>
    quiet(tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text }));
  if (String(cq.from?.id) !== String(env.OWNER_ID)) return answer("Not allowed.");

  const [action, batch] = String(cq.data || "").split(":");
  const tapped = cq.message?.message_id;
  const key = `pending:${batch}`;
  const pending = await env.STORE.get(key, "json");

  if (!pending) {
    await answer("Already handled.");
    const done = await env.STORE.get(`decision:${batch}`, "json");
    if (tapped && (!done || done.message_id !== tapped)) {
      // A leftover copy with dead buttons: remove it.
      await quiet(tg(env, "deleteMessage", { chat_id: env.OWNER_ID, message_id: tapped }));
    }
    return;
  }

  let outcome = "skipped";
  if (action === "post") {
    // Remember how far we got, so a retry never repeats a part.
    for (let i = pending.sent || 0; i < pending.messages.length; i++) {
      try {
        await tg(env, "sendMessage", {
          chat_id: env.CHANNEL_ID, text: pending.messages[i],
          parse_mode: "HTML", disable_web_page_preview: true,
        });
      } catch (e) {
        console.log(`post failed for ${batch}: ${e.message}`);
        await answer("Could not post. Nothing was lost; tap again in a minute.");
        return;
      }
      pending.sent = i + 1;
      await env.STORE.put(key, JSON.stringify(pending), { expirationTtl: PENDING_TTL });
    }
    outcome = "posted";
  } else if (action !== "skip") {
    return answer("Unknown button.");
  }

  await env.STORE.put(`decision:${batch}`, JSON.stringify({
    batch, outcome, items: pending.items || [],
    at: Math.floor(Date.now() / 1000), message_id: tapped ?? null,
  }), { expirationTtl: RECORD_TTL });
  await env.STORE.delete(key);

  await answer(outcome === "posted" ? "Posted to the channel." : "Skipped.");
  if (tapped) {
    const [icon, word] = outcome === "posted" ? ["✅", "Posted"] : ["⏭️", "Skipped"];
    await quiet(tg(env, "editMessageText", {
      chat_id: env.OWNER_ID, message_id: tapped, parse_mode: "HTML",
      text: `${icon} <b>${word}</b> · ${esc(pending.subject || batch)} · ${clock(env)}`,
      reply_markup: { inline_keyboard: [] },
    }));
  }
}

async function onMessage(env, updateId, msg) {
  const owner = String(env.OWNER_ID);
  if (msg.chat?.type !== "private" || String(msg.chat?.id) !== owner ||
      String(msg.from?.id) !== owner) return;
  let text = (msg.text || msg.caption || "").trim();
  if (!text) return;

  const space = text.indexOf(" ");
  const cmd = (space < 0 ? text : text.slice(0, space)).split("@")[0].toLowerCase();
  if (cmd === "/start" || cmd === "/help") return note(env, HELP);
  if (cmd === "/learned") {
    const body = ((await env.STORE.get("learnings")) || "").trim();
    return note(env, `🧠 <b>Learned so far</b>\n${body ? esc(body.slice(0, 3500)) : "Nothing learned yet."}`);
  }
  if (cmd === "/feedback") {
    text = space < 0 ? "" : text.slice(space + 1).trim();
    if (!text) return note(env, HELP);
  } else if (text.startsWith("/")) {
    return note(env, HELP);
  }

  await env.STORE.put(`inbox:${updateId}`, JSON.stringify({
    text: text.slice(0, 2000),
    about: (msg.reply_to_message?.text || "").slice(0, 300) || null,
    at: msg.date || Math.floor(Date.now() / 1000),
  }), { expirationTtl: RECORD_TTL });
  await note(env, "📝 Noted. It will be applied from the next run.");
}

async function webhook(request, env) {
  if (!same(request.headers.get("x-telegram-bot-api-secret-token"), env.API_SECRET)) {
    return new Response("forbidden", { status: 403 });
  }
  const update = await request.json().catch(() => null);
  try {
    if (update?.callback_query) await onTap(env, update.callback_query);
    else if (update?.message) await onMessage(env, update.update_id, update.message);
  } catch (e) {
    console.log(`update failed: ${e.message}`);
  }
  // Always 200: a failed tap is answered in the chat, not retried by Telegram.
  return new Response("ok");
}

// ------------------------------------------------------------ poster side

async function collect(env, prefix) {
  const out = [];
  let cursor;
  do {
    const page = await env.STORE.list({ prefix, cursor });
    for (const k of page.keys) {
      const value = await env.STORE.get(k.name, "json");
      if (value) out.push({ key: k.name, ...value });
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}

async function api(request, env, path) {
  const auth = request.headers.get("authorization") || "";
  if (!same(auth.replace(/^Bearer\s+/i, ""), env.API_SECRET)) {
    return new Response("forbidden", { status: 403 });
  }
  const m = request.method;

  const pend = path.match(/^\/pending\/([A-Za-z0-9_-]{1,64})$/);
  if (pend && m === "PUT") {
    const b = await request.json();
    if (!Array.isArray(b.messages) || !b.messages.length) return json({ error: "messages" }, 400);
    if (await env.STORE.get(`decision:${pend[1]}`)) return json({ ok: true, decided: true });
    const old = await env.STORE.get(`pending:${pend[1]}`, "json");
    await env.STORE.put(`pending:${pend[1]}`, JSON.stringify({
      items: b.items || [], messages: b.messages, subject: b.subject || "",
      created: b.created || null, sent: old?.sent || 0,
    }), { expirationTtl: PENDING_TTL });
    return json({ ok: true });
  }
  if (pend && m === "DELETE") {
    await env.STORE.delete(`pending:${pend[1]}`);
    return json({ ok: true });
  }
  if (path === "/learnings" && m === "PUT") {
    const b = await request.json();
    await env.STORE.put("learnings", String(b.text || "").slice(0, 20000));
    return json({ ok: true });
  }
  if (path === "/sync" && m === "GET") {
    return json({
      decisions: await collect(env, "decision:"),
      messages: await collect(env, "inbox:"),
      notes: await collect(env, "note:"),
      pending: (await env.STORE.list({ prefix: "pending:" })).keys.map((k) => k.name.slice(8)),
    });
  }
  if (path === "/ack" && m === "POST") {
    const b = await request.json();
    const keys = (b.keys || []).filter((k) => /^(decision|inbox|note):/.test(k));
    await Promise.all(keys.map((k) => env.STORE.delete(k)));
    return json({ ok: true, deleted: keys.length });
  }
  return new Response("not found", { status: 404 });
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname.replace(/\/+$/, "") || "/";
    if (!env.API_SECRET || !env.BOT_TOKEN || !env.OWNER_ID || !env.CHANNEL_ID) {
      return new Response("not configured", { status: 503 });
    }
    if (path === "/tg" && request.method === "POST") return webhook(request, env);
    return api(request, env, path);
  },
};
