package com.orffyrus.npcai;

import com.hypixel.hytale.logger.HytaleLogger;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "am I actively guiding someone somewhere right now, and to what
 * kind of destination" state, set the moment the orchestrator's reply
 * carries action="offer_guide" (the NPC's own in-character decision - see
 * llm_client.py's SYSTEM_TEMPLATE rules). Read every tick by
 * SeekLandmarkSensor, which also clears this automatically once the NPC
 * arrives - see that class's javadoc.
 *
 * Unlike PendingShopOpen (consume-once), this is persistent while active,
 * same shape as CompanionState - it stays set across many ticks until the
 * NPC arrives (or a caller explicitly stops it).
 */
public final class GuideState {

    /** Which NearbyLandmarks query SeekLandmarkSensor should walk toward. */
    public enum Target {
        /** NearbyLandmarks.closestPosition() - whatever discoverable zone is nearest. */
        NEAREST_LANDMARK,
        /** NearbyLandmarks.closestWaterPosition() - nearest Ocean/Shallow_Ocean/
         * Shore zone. The closest real approximation to "lake"/"river"/"water"
         * Hytale's worldgen actually tracks - confirmed via the real shipped
         * Zone.json assets in Assets.zip that there is no discrete lake/river
         * zone type at all, only Ocean/Shore/biome-region/Temple/Tier zones. */
        NEAREST_WATER
    }

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();
    private static final ConcurrentHashMap<String, Target> GUIDING = new ConcurrentHashMap<>();

    private GuideState() { }

    public static void startGuiding(String npcId, Target target) {
        // Logged unconditionally (not just on a mode change) so a live test
        // can directly confirm the orchestrator's OFFER_GUIDE decision
        // actually reached this point, rather than inferring it indirectly
        // from spoken dialogue text alone.
        LOGGER.atInfo().log(npcId + " started guiding (target=" + target + ")");
        GUIDING.put(npcId, target);
    }

    public static void stopGuiding(String npcId) {
        GUIDING.remove(npcId);
    }

    public static boolean isGuiding(String npcId) {
        return GUIDING.containsKey(npcId);
    }

    /** The active guide mode for this NPC, or null if not currently guiding. */
    public static Target getTarget(String npcId) {
        return GUIDING.get(npcId);
    }
}
