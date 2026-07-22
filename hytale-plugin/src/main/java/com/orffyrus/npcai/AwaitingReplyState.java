package com.orffyrus.npcai;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Tracks which NPCs are currently waiting on a real orchestrator round trip -
 * set the moment TalkToAIAction/PlayerChatToAIListener calls
 * bridge.sendDialogue(), cleared the moment that specific reply's callback
 * fires. Drives IsAwaitingReplySensor, which triggers a floating "thinking"
 * particle above the NPC's head (see the role JSONs' "IsAwaitingReply"
 * sensor block) - a reply that just appears in chat with zero visual
 * buildup reads as unnaturally instant, breaking the sense that the NPC is
 * actually thinking about what was said. Paired with main.py's
 * MIN_REPLY_DELAY_S, which keeps a fast LLM response from arriving so
 * quickly there's nothing for the icon to cover.
 *
 * TTL-swept the same way ThreatMemory is - a reply that never arrives
 * (orchestrator restart mid-request, connection drop) shouldn't leave this
 * NPC showing a "thinking" icon forever.
 */
public final class AwaitingReplyState {

    private static final long TTL_MILLIS = 20_000L;

    private static final ConcurrentHashMap<String, Long> AWAITING = new ConcurrentHashMap<>();

    static {
        new java.util.Timer(true).scheduleAtFixedRate(new java.util.TimerTask() {
            public void run() {
                long now = System.currentTimeMillis();
                AWAITING.entrySet().removeIf(e -> now - e.getValue() > TTL_MILLIS);
            }
        }, TTL_MILLIS, TTL_MILLIS);
    }

    private AwaitingReplyState() { }

    public static void start(String npcId) {
        AWAITING.put(npcId, System.currentTimeMillis());
    }

    public static void clear(String npcId) {
        AWAITING.remove(npcId);
    }

    public static boolean isAwaiting(String npcId) {
        Long startedAt = AWAITING.get(npcId);
        return startedAt != null && System.currentTimeMillis() - startedAt <= TTL_MILLIS;
    }
}
