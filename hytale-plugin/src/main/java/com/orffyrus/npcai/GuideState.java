package com.orffyrus.npcai;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "am I actively guiding someone to a landmark right now" flag, set
 * the moment the orchestrator's reply carries action="offer_guide" (the
 * NPC's own in-character decision - see llm_client.py's SYSTEM_TEMPLATE
 * rules). Read every tick by SeekLandmarkSensor, which also clears this
 * automatically once the NPC arrives near NearbyLandmarks.closestPosition()
 * - see that class's javadoc.
 *
 * Unlike PendingShopOpen (consume-once), this is persistent while active,
 * same shape as CompanionState - it stays true across many ticks until the
 * NPC arrives (or a caller explicitly stops it).
 */
public final class GuideState {

    private static final ConcurrentHashMap<String, Boolean> GUIDING = new ConcurrentHashMap<>();

    private GuideState() { }

    public static void startGuiding(String npcId) {
        GUIDING.put(npcId, Boolean.TRUE);
    }

    public static void stopGuiding(String npcId) {
        GUIDING.remove(npcId);
    }

    public static boolean isGuiding(String npcId) {
        return GUIDING.containsKey(npcId);
    }
}
