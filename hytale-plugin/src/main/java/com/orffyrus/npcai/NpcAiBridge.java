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
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.BiConsumer;

public class NpcAiBridge implements WebSocket.Listener {

    private volatile WebSocket ws;
    private final URI uri;
    private final StringBuilder partial = new StringBuilder();
    /** npcId -> callback(text). Register when an NPC entity spawns. */
    private final ConcurrentHashMap<String, BiConsumer<String, String>> sayHandlers =
            new ConcurrentHashMap<>();

    public NpcAiBridge(String url) { this.uri = URI.create(url); }

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

    public void registerNpc(String npcId, BiConsumer<String, String> onSay) {
        sayHandlers.put(npcId, onSay);
    }

    public void unregisterNpc(String npcId) { sayHandlers.remove(npcId); }

    /** Fire-and-forget: never block the game thread on this. */
    private void send(String json) {
        WebSocket s = ws;
        if (s != null) s.sendText(json, true);
        // if null (disconnected), silently drop - fallback lines cover it
    }

    private static String esc(String v) {
        return v.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", " ").replace("\r", " ");
    }

    public void sendDialogue(String npcId, String npcName, String npcRole,
                             String playerId, String playerName,
                             String playerText, String situation) {
        send(String.format(
            "{\"type\":\"dialogue\",\"req_id\":\"%s\",\"npc_id\":\"%s\"," +
            "\"npc_name\":\"%s\",\"npc_role\":\"%s\",\"player_id\":\"%s\"," +
            "\"player_name\":\"%s\",\"text\":\"%s\",\"situation\":\"%s\"}",
            UUID.randomUUID(), esc(npcId), esc(npcName), esc(npcRole),
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

    public void sendOutcome(String npcId, String playerId, String outcome) {
        send(String.format(
            "{\"type\":\"outcome\",\"npc_id\":\"%s\",\"player_id\":\"%s\"," +
            "\"outcome\":\"%s\"}", esc(npcId), esc(playerId), esc(outcome)));
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
        String npcId = extract(json, "npc_id");
        String text = extract(json, "text");
        if (npcId == null) return;
        BiConsumer<String, String> handler = sayHandlers.get(npcId);
        if (handler != null && text != null && !text.isEmpty()) {
            // IMPORTANT: hop back onto the game/entity thread before touching
            // world state - this callback arrives on the websocket thread.
            handler.accept(npcId, text);
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
            if (c == '\\' && j + 1 < json.length()) { sb.append(json.charAt(++j)); }
            else if (c == '"') break;
            else sb.append(c);
        }
        return sb.toString();
    }

    @Override
    public void onError(WebSocket socket, Throwable error) {
        scheduleReconnect();
    }
}
