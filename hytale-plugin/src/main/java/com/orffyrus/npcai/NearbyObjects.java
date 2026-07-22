package com.orffyrus.npcai;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "closest notable real block nearby" snapshot - written by
 * NoteNearbyObjectsAction (a tick-based, throttled scan of actual world
 * BLOCKS around the NPC - see that class's javadoc) and read by
 * TalkToAIAction/PlayerChatToAIListener when building the situation
 * string, same shape as ThreatMemory.
 *
 * 2026-07-22, "what is the color of the flower on the ground between
 * us": rather than trying to parse a color out of a block's own internal
 * asset id in Java (e.g. "Plant_Flower_Common_Blue"), this just reports
 * the real id to the model as-is - it (with real Hytale wiki grounding
 * already available via wiki_knowledge) is far better positioned to
 * interpret what a given item actually looks like than a brittle
 * Java-side string parser would be.
 */
public final class NearbyObjects {

    private static final long STALE_AFTER_MILLIS = 20_000L;
    /** Minimum real time between actual block scans per NPC - lets
     * NoteNearbyObjectsAction run on a bare, unconditional "Any" sensor
     * every tick without actually re-scanning dozens of blocks that
     * often; nothing on the ground changes that fast. */
    private static final long RESCAN_INTERVAL_MILLIS = 5_000L;

    private record Snapshot(String blockId, double distance, long observedAtMillis) { }

    private static final ConcurrentHashMap<String, Snapshot> LAST_SEEN = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, Long> LAST_SCAN_MILLIS = new ConcurrentHashMap<>();

    private NearbyObjects() { }

    /** True at most once every RESCAN_INTERVAL_MILLIS per NPC - side-
     * effecting (updates the internal timer as a side effect of
     * returning true), same throttle pattern as GuideState.shouldLogStuck(). */
    public static boolean shouldRescan(String npcId) {
        long now = System.currentTimeMillis();
        Long last = LAST_SCAN_MILLIS.get(npcId);
        if (last != null && now - last < RESCAN_INTERVAL_MILLIS) {
            return false;
        }
        LAST_SCAN_MILLIS.put(npcId, now);
        return true;
    }

    /** blockId null means "nothing notable found this scan" - clears any
     * previous snapshot rather than keeping a stale one around. */
    public static void record(String npcId, String blockId, double distance) {
        if (blockId == null) {
            LAST_SEEN.remove(npcId);
            return;
        }
        LAST_SEEN.put(npcId, new Snapshot(blockId, distance, System.currentTimeMillis()));
    }

    /** Returns a short situation-string fragment naming the closest
     * notable real block near this NPC, or "" if none/stale. The raw
     * block id is passed through untranslated on purpose - see class
     * javadoc for why interpreting it (color, etc.) is left to the model. */
    public static String describe(String npcId) {
        Snapshot s = LAST_SEEN.get(npcId);
        if (s == null) {
            return "";
        }
        if (System.currentTimeMillis() - s.observedAtMillis() > STALE_AFTER_MILLIS) {
            LAST_SEEN.remove(npcId);
            return "";
        }
        return "You can see a " + s.blockId() + " on the ground nearby (~"
                + Math.round(s.distance()) + " blocks away).";
    }
}
