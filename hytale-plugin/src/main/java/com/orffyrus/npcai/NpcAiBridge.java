package com.orffyrus.npcai;

/**
 * Hytale plugin side of the bridge - minimal async WebSocket client.
 *
 * NOTE: Hytale's plugin API surface is still shifting during 2026 (the
 * changelogs list NPC/ECS renames), so this file deliberately contains
 * ZERO Hytale-specific imports. It's the transport layer only. NpcAiPlugin
 * wires sendDialogue()/sendAmbient()/sendOutcome() into the actual NPC
 * event hooks, and handles "say" responses by applying them to the entity
 * (chat bubble, dialogue UI, etc).
 *
 * Uses java.net.http.WebSocket (JDK 11+) - no extra dependencies.
 */
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.util.UUID;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentHashMap;

public class NpcAiBridge implements WebSocket.Listener {

    /** (npcId, text, action, isCompanion) - action is one of the
     * orchestrator's llm_client.VALID_ACTIONS ("none", "offer_guide",
     * "offer_fight", "decline_guide", "accept_tame").
     * isCompanion is the Postgres-backed taming truth (taming.py),
     * resent on every reply so the plugin's own ephemeral CompanionState
     * can resync after a restart - see CompanionState.java. No built-in
     * java.util.function type takes four args, hence this. */
    @FunctionalInterface
    public interface SayHandler {
        void onSay(String npcId, String text, String action, boolean isCompanion);
    }

    /** How long an in-flight request's handler is kept waiting for a reply
     * before being reaped - well past the orchestrator's own dialogue
     * budget (3s slot wait + 6s call timeout, see priority_queue.py), so
     * anything still pending this long means the reply is never coming
     * (orchestrator restarted, connection dropped mid-flight, etc). Keeps
     * pendingReplies bounded even under sustained connection trouble. */
    private static final long PENDING_REPLY_TTL_MILLIS = 20_000;

    private record PendingReply(SayHandler handler, long sentAtMillis) { }

    private volatile WebSocket ws;
    private final URI uri;
    private final StringBuilder partial = new StringBuilder();
    /** req_id -> pending reply handler, one-shot: consumed and removed the
     * moment its reply arrives (or reaped by sweepStalePendingReplies() if
     * it never does). Keyed per-REQUEST rather than per-NPC on purpose -
     * an earlier version keyed this by npcId alone, so two players talking
     * to the same NPC entity concurrently (e.g. both chatting with a single
     * shared "Adventurer") would silently steal each other's handler: the
     * second player's registration overwrote the first's, so whichever
     * reply arrived later got delivered through the wrong player's
     * callback - one player's private conversation text landing in
     * another's chat, or a reply just vanishing for whoever got
     * overwritten. req_id is already generated fresh per call and already
     * echoed back verbatim by the orchestrator (see main.py's "say"
     * response) - this just actually uses it. */
    private final ConcurrentHashMap<String, PendingReply> pendingReplies =
            new ConcurrentHashMap<>();

    public NpcAiBridge(String url) {
        this.uri = URI.create(url);
        scheduleReplySweep();
    }

    public void connect() {
        HttpClient.newHttpClient().newWebSocketBuilder()
                .buildAsync(uri, this)
                .whenComplete((socket, err) -> {
                    if (err != null) {
                        // retry with backoff; NPCs fall back to canned lines meanwhile
                        scheduleReconnect();
                    } else {
                        this.ws = socket;
                    }
                });
    }

    private void scheduleReconnect() {
        new java.util.Timer(true).schedule(new java.util.TimerTask() {
            public void run() { connect(); }
        }, 5000);
    }

    private void scheduleReplySweep() {
        new java.util.Timer(true).scheduleAtFixedRate(new java.util.TimerTask() {
            public void run() { sweepStalePendingReplies(); }
        }, PENDING_REPLY_TTL_MILLIS, PENDING_REPLY_TTL_MILLIS);
    }

    private void sweepStalePendingReplies() {
        long now = System.currentTimeMillis();
        for (Iterator<Map.Entry<String, PendingReply>> it = pendingReplies.entrySet().iterator();
                it.hasNext(); ) {
            if (now - it.next().getValue().sentAtMillis() > PENDING_REPLY_TTL_MILLIS) {
                it.remove();
            }
        }
    }

    /** Fire-and-forget: never block the game thread on this. */
    private void send(String json) {
        WebSocket s = ws;
        if (s != null) s.sendText(json, true);
        // if null (disconnected), silently drop - fallback lines cover it
    }

    private static String esc(String v) {
        // Escapes every JSON-illegal control character, not just \n/\r - a
        // player chat message containing a raw tab or other C0 control byte
        // (plausible from paste/clipboard input) used to pass through
        // unescaped, producing invalid JSON that orchestrator-side
        // json.loads() rejects outright, silently dropping that whole
        // dialogue turn.
        StringBuilder sb = new StringBuilder(v.length());
        for (int i = 0; i < v.length(); i++) {
            char c = v.charAt(i);
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '"': sb.append("\\\""); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        return sb.toString();
    }

    /**
     * Sends a dialogue request and registers onSay against a fresh
     * per-request id, atomically (registration happens before the request
     * ever goes out) - see pendingReplies' javadoc for the cross-player bug
     * this replaced.
     */
    public void sendDialogue(String npcId, String npcName, String npcRole,
                             String playerId, String playerName,
                             String playerText, String situation,
                             SayHandler onSay) {
        String reqId = UUID.randomUUID().toString();
        pendingReplies.put(reqId, new PendingReply(onSay, System.currentTimeMillis()));
        send(String.format(
            "{\"type\":\"dialogue\",\"req_id\":\"%s\",\"npc_id\":\"%s\"," +
            "\"npc_name\":\"%s\",\"npc_role\":\"%s\",\"player_id\":\"%s\"," +
            "\"player_name\":\"%s\",\"text\":\"%s\",\"situation\":\"%s\"}",
            reqId, esc(npcId), esc(npcName), esc(npcRole),
            esc(playerId), esc(playerName), esc(playerText), esc(situation)));
    }

    public void sendAmbient(String npcId, String npcName, String npcRole,
                            String situation) {
        send(String.format(
            "{\"type\":\"ambient\",\"req_id\":\"%s\",\"npc_id\":\"%s\"," +
            "\"npc_name\":\"%s\",\"npc_role\":\"%s\",\"situation\":\"%s\"}",
            UUID.randomUUID(), esc(npcId), esc(npcName), esc(npcRole),
            esc(situation)));
    }

    public void sendOutcome(String npcId, String npcRole, String playerId, String outcome) {
        // npc_role is required here for the same reason sendDialogue/
        // sendAmbient carry it: orchestrator-side handle_outcome() resolves
        // this NPC's personality baseline from DEFAULT_BASELINES[npc_role]
        // (main.py). If the very first event ever recorded for an npc_id is
        // an outcome (plausible for e.g. a hostile encounter before any
        // dialogue happened) and it arrived with no role, the NPC's baseline
        // row would get created with the generic fallback baseline and stay
        // wrong forever - personality.py's baseline is written once at row
        // creation and never corrected afterward.
        send(String.format(
            "{\"type\":\"outcome\",\"npc_id\":\"%s\",\"npc_role\":\"%s\"," +
            "\"player_id\":\"%s\",\"outcome\":\"%s\"}",
            esc(npcId), esc(npcRole), esc(playerId), esc(outcome)));
    }

    // -- incoming ----------------------------------------------------------

    @Override
    public CompletionStage<?> onText(WebSocket socket, CharSequence data, boolean last) {
        partial.append(data);
        if (last) {
            String msg = partial.toString();
            partial.setLength(0);
            handleMessage(msg);
        }
        socket.request(1);
        return null;
    }

    private void handleMessage(String json) {
        // Tiny extraction to avoid a JSON dependency; swap in Gson/Jackson
        // (already on most plugin classpaths) for anything more complex.
        String reqId = extract(json, "req_id");
        String npcId = extract(json, "npc_id");
        String text = extract(json, "text");
        String action = extract(json, "action");
        boolean isCompanion = extractBoolean(json, "is_companion");
        if (reqId == null || npcId == null) return;
        // One-shot: remove() instead of get() so a reply can never be
        // delivered twice and a stale/duplicate message can't resurrect an
        // already-answered handler.
        PendingReply pending = pendingReplies.remove(reqId);
        if (pending != null && text != null) {
            // Always invoke the handler once text is present, even "" -
            // 2026-07-21: the orchestrator now legitimately replies with
            // empty text when the NPC has nothing to say this turn (GPU
            // busy/timeout - see priority_queue.py, BUSY_LINES removed) and
            // callers still need this to fire so they clear
            // AwaitingReplyState (the "thinking" particle) and consider the
            // request answered - it's the handler's own job to skip sending
            // an empty chat message, not this dispatch layer's.
            //
            // IMPORTANT: hop back onto the game/entity thread before touching
            // world state - this callback arrives on the websocket thread.
            pending.handler().onSay(npcId, text, action != null ? action : "none", isCompanion);
        }
    }

    private static String extract(String json, String key) {
        // Python's json.dumps() (orchestrator/main.py) inserts a space after
        // the colon by default ("npc_id": "x", not "npc_id":"x") - a literal
        // "\"key\":\"" match never fires against real orchestrator replies,
        // so the callback silently never runs. Skip whitespace after the
        // colon instead of assuming none.
        String keyPat = "\"" + key + "\"";
        int i = json.indexOf(keyPat);
        if (i < 0) return null;
        int colon = json.indexOf(':', i + keyPat.length());
        if (colon < 0) return null;
        int start = colon + 1;
        while (start < json.length() && Character.isWhitespace(json.charAt(start))) start++;
        if (start >= json.length() || json.charAt(start) != '"') return null;
        start++;
        StringBuilder sb = new StringBuilder();
        for (int j = start; j < json.length(); j++) {
            char c = json.charAt(j);
            if (c == '\\' && j + 1 < json.length()) {
                char next = json.charAt(++j);
                // 2026-07-22 real bug found live: this only ever handled
                // single-char escapes (correct for "\"" -> '"', "\\" -> '\'),
                // but a Unicode escape (backslash, then 'u', then 4 hex
                // digits) has 'u' as the char right after the backslash -
                // falling into this same branch appended just 'u' and left
                // the four hex digits to be appended as plain characters by
                // the loop's next iterations, silently mangling e.g. an
                // em-dash ("-", U+2014, from Python's json.dumps() default
                // ensure_ascii=True escaping any non-ASCII character the
                // model happens to use) into the literal text "u2014" glued
                // onto whatever word came next - confirmed live in the real
                // server log: "Emerald Wildsu2014I've got a feeling...".
                // Decode the Unicode escape properly; \n/\r/\t as a
                // defensive completeness match (dialogue text
                // is never expected to contain a literal one after
                // _parse_dialogue_response()'s tag-stripping, but a hand-
                // rolled parser handling some JSON escapes and not others is
                // exactly how this bug happened in the first place).
                if (next == 'u' && j + 4 < json.length()) {
                    sb.append((char) Integer.parseInt(json.substring(j + 1, j + 5), 16));
                    j += 4;
                } else if (next == 'n') { sb.append('\n'); }
                else if (next == 'r') { sb.append('\r'); }
                else if (next == 't') { sb.append('\t'); }
                else { sb.append(next); }  // "\"", "\\", "\/" - the escaped char itself
            }
            else if (c == '"') break;
            else sb.append(c);
        }
        return sb.toString();
    }

    private static boolean extractBoolean(String json, String key) {
        // Same whitespace-tolerant lookup as extract(), but JSON booleans
        // are bare literals (true/false), not quoted strings.
        String keyPat = "\"" + key + "\"";
        int i = json.indexOf(keyPat);
        if (i < 0) return false;
        int colon = json.indexOf(':', i + keyPat.length());
        if (colon < 0) return false;
        int start = colon + 1;
        while (start < json.length() && Character.isWhitespace(json.charAt(start))) start++;
        return json.regionMatches(start, "true", 0, 4);
    }

    @Override
    public void onError(WebSocket socket, Throwable error) {
        scheduleReconnect();
    }
}
