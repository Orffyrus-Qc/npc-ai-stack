package com.orffyrus.npcai;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "last hostile creature I noticed" snapshot, written by
 * NoteNearbyThreatAction (fired from a "Mob"+"Attitude" Sensor in the NPC's
 * Instructions tree - see Adventurer.json) and read by TalkToAIAction /
 * PlayerChatToAIListener when building the situation string sent to the
 * orchestrator.
 *
 * Unlike NearbyLandmarks (world geography - fixed forever once computed),
 * a threat is a live entity that moves and may leave - snapshots expire
 * after STALE_AFTER_MILLIS so the AI doesn't keep talking about a monster
 * that wandered off five minutes ago.
 */
public final class ThreatMemory {

    private static final long STALE_AFTER_MILLIS = 20_000L;

    private record Snapshot(String description, long observedAtMillis) { }

    private static final ConcurrentHashMap<String, Snapshot> LAST_SEEN = new ConcurrentHashMap<>();

    private ThreatMemory() { }

    public static void record(String npcId, double distance) {
        int rounded = (int) Math.round(distance);
        LAST_SEEN.put(npcId, new Snapshot(
                "A hostile creature is nearby, about " + rounded + " blocks away.",
                System.currentTimeMillis()));
    }

    /** Returns the last-noted threat description, or "" if none/stale. */
    public static String describe(String npcId) {
        Snapshot s = LAST_SEEN.get(npcId);
        if (s == null) {
            return "";
        }
        if (System.currentTimeMillis() - s.observedAtMillis() > STALE_AFTER_MILLIS) {
            LAST_SEEN.remove(npcId);
            return "";
        }
        return s.description();
    }
}
