// Offline tests for the approvals Worker. Run:  node --test worker/test
import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

function setup() {
  const data = new Map();
  const STORE = {
    async get(k, type) {
      if (!data.has(k)) return null;
      return type === "json" ? JSON.parse(data.get(k)) : data.get(k);
    },
    async put(k, v) { data.set(k, v); },
    async delete(k) { data.delete(k); },
    async list({ prefix }) {
      return { keys: [...data.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })),
               list_complete: true };
    },
  };
  const env = { STORE, BOT_TOKEN: "t", OWNER_ID: "42", CHANNEL_ID: "-100123", API_SECRET: "s3cret" };
  const calls = [];
  let nextId = 500, failChannel = false;
  globalThis.fetch = async (url, init) => {
    const method = String(url).split("/").pop();
    const params = JSON.parse(init.body);
    calls.push({ method, params });
    if (failChannel && method === "sendMessage" && params.chat_id === "-100123") {
      return new Response(JSON.stringify({ ok: false, description: "boom" }));
    }
    return new Response(JSON.stringify({ ok: true, result: { message_id: ++nextId } }));
  };
  const api = (method, path, body) => worker.fetch(new Request("https://w.example" + path, {
    method, headers: { authorization: "Bearer s3cret" },
    body: body ? JSON.stringify(body) : undefined }), env);
  const hook = (update, secret = "s3cret") => worker.fetch(new Request("https://w.example/tg", {
    method: "POST", headers: { "x-telegram-bot-api-secret-token": secret },
    body: JSON.stringify(update) }), env);
  const tap = (data, from = 42, message_id = 7) => hook({ update_id: 1, callback_query: {
    id: "c", from: { id: from }, data, message: { message_id, chat: { id: 42 } } } });
  const say = (text, extra = {}, id = 9) => hook({ update_id: id, message: {
    chat: { id: 42, type: "private" }, from: { id: 42 }, text, date: 1700000000, ...extra } });
  const toChannel = () => calls.filter((c) => c.method === "sendMessage" && c.params.chat_id === "-100123");
  return { env, data, calls, api, hook, tap, say, toChannel, fail: (v) => { failChannel = v; } };
}

const batch = { items: ["a"], messages: ["part one", "part two"], subject: "Disco helpers" };

test("post tap posts every part once, records the decision, collapses the preview", async () => {
  const t = setup();
  assert.equal((await t.api("PUT", "/pending/b1", batch)).status, 200);
  await t.tap("post:b1");
  assert.deepEqual(t.toChannel().map((c) => c.params.text), ["part one", "part two"]);
  const edit = t.calls.find((c) => c.method === "editMessageText");
  assert.match(edit.params.text, /Posted.*Disco helpers/);
  assert.equal(t.data.has("pending:b1"), false);

  await t.tap("post:b1");                       // second tap on the same message
  assert.equal(t.toChannel().length, 2);
  assert.equal(t.calls.some((c) => c.method === "deleteMessage"), false);

  await t.tap("post:b1", 42, 8);                // tap on a reminder copy
  assert.equal(t.toChannel().length, 2);
  assert.equal(t.calls.filter((c) => c.method === "deleteMessage")[0].params.message_id, 8);

  const sync = await (await t.api("GET", "/sync")).json();
  assert.equal(sync.decisions[0].outcome, "posted");
  assert.deepEqual(sync.decisions[0].items, ["a"]);
  await t.api("POST", "/ack", { keys: [sync.decisions[0].key, "pending:zzz"] });
  assert.equal((await (await t.api("GET", "/sync")).json()).decisions.length, 0);
});

test("skip posts nothing", async () => {
  const t = setup();
  await t.api("PUT", "/pending/b1", batch);
  await t.tap("skip:b1");
  assert.equal(t.toChannel().length, 0);
  assert.equal(JSON.parse(t.data.get("decision:b1")).outcome, "skipped");
});

test("only the owner can tap, and only Telegram with the secret can call the webhook", async () => {
  const t = setup();
  await t.api("PUT", "/pending/b1", batch);
  await t.tap("post:b1", 99);
  assert.equal(t.toChannel().length, 0);
  assert.equal(t.data.has("pending:b1"), true);
  assert.equal((await t.hook({ update_id: 1 }, "wrong")).status, 403);
  const r = await worker.fetch(new Request("https://w.example/sync"), t.env);
  assert.equal(r.status, 403);
});

test("a failed post hands the rest to the poster and says why", async () => {
  const t = setup();
  await t.api("PUT", "/pending/b1", batch);
  t.data.set("pending:b1", JSON.stringify({ ...batch, sent: 1 }));   // part one already went
  t.fail(true);
  await t.tap("post:b1");
  assert.equal(t.data.has("pending:b1"), false);
  const d = JSON.parse(t.data.get("decision:b1"));
  assert.equal(d.outcome, "approved");
  assert.equal(d.sent, 1);
  assert.match(d.error, /boom/);
  const alert = t.calls.find((c) => c.method === "answerCallbackQuery");
  assert.equal(alert.params.show_alert, true);
  assert.match(alert.params.text, /boom/);
  assert.ok(alert.params.text.length <= 200);
  assert.match(t.calls.find((c) => c.method === "editMessageText").params.text, /Approved/);
});

test("re-uploading a decided batch does not bring it back", async () => {
  const t = setup();
  await t.api("PUT", "/pending/b1", batch);
  await t.tap("skip:b1");
  await t.api("PUT", "/pending/b1", batch);
  assert.equal(t.data.has("pending:b1"), false);
});

test("owner messages are queued as feedback and acknowledged at once", async () => {
  const t = setup();
  await t.say("Keep policies to one line", { reply_to_message: { text: "the post" } });
  const sync = await (await t.api("GET", "/sync")).json();
  assert.equal(sync.messages[0].text, "Keep policies to one line");
  assert.equal(sync.messages[0].about, "the post");
  assert.equal(sync.notes.length, 1);
  assert.match(t.calls.at(-1).params.text, /Noted/);
});

test("strangers are ignored; /help and /learned are not feedback", async () => {
  const t = setup();
  await t.hook({ update_id: 3, message: { chat: { id: 5, type: "private" }, from: { id: 5 }, text: "hi" } });
  assert.equal(t.calls.length, 0);
  await t.api("PUT", "/learnings", { text: "- Be brief <always>" });
  await t.say("/learned");
  assert.match(t.calls.at(-1).params.text, /Be brief &lt;always&gt;/);
  await t.say("/help");
  await t.say("/nonsense");
  assert.equal((await (await t.api("GET", "/sync")).json()).messages.length, 0);
});

test("secrets pasted with spaces or quotes still work", async () => {
  const t = setup();
  t.env.OWNER_ID = " 42\n";
  t.env.API_SECRET = '"s3cret" ';
  await t.say("/help");
  assert.match(t.calls.at(-1).params.text, /Feedback/);
});
