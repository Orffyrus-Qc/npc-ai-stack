package com.orffyrus.npcai;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Cached "map within ~200 blocks" situation fragments for Mori.
 *
 * Full landmark queries need live ECS refs ({@link NearbyLandmarks#describe}),
 * which chat turns do not have. TalkToAI / NoteNearbyObjects refresh this
 * cache; chat reads the latest snapshot so Mori still "knows" the map when
 * addressed by name.
 */
public final class MapSense200 {

    public static final int RADIUS_BLOCKS = 200;

    private static final long STALE_AFTER_MILLIS = 30_000L;

    private record Snapshot(String text, long atMillis) { }

    private static final ConcurrentHashMap<String, Snapshot> CACHE = new ConcurrentHashMap<>();

    private MapSense200() { }

    public static void record(String npcId, String situationFragment) {
        if (npcId == null) {
            return;
        }
        if (situationFragment == null || situationFragment.isBlank()) {
            CACHE.remove(npcId);
            return;
        }
        // Keep the cache bounded — situation strings can be long.
        String clipped = situationFragment.length() > 1200
                ? situationFragment.substring(0, 1200) + "…"
                : situationFragment;
        CACHE.put(npcId, new Snapshot(clipped, System.currentTimeMillis()));
    }

    public static String describeCached(String npcId) {
        Snapshot s = CACHE.get(npcId);
        if (s == null) {
            return "";
        }
        if (System.currentTimeMillis() - s.atMillis() > STALE_AFTER_MILLIS) {
            CACHE.remove(npcId);
            return "";
        }
        return "Map sense (~" + RADIUS_BLOCKS + " blocks): " + s.text();
    }
}
