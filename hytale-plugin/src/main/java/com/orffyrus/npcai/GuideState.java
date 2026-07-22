package com.orffyrus.npcai;

import com.hypixel.hytale.logger.HytaleLogger;

import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "am I actively guiding someone somewhere right now, and to what
 * kind of destination" state, set the moment the orchestrator's reply
 * carries action="offer_guide" (the NPC's own in-character decision - see
 * llm_client.py's SYSTEM_TEMPLATE rules). Read every tick by
 * SeekLandmarkSensor, which also clears this automatically once the NPC
 * arrives - see that class's javadoc.
 *
 * This is persistent while active, same shape as CompanionState - it stays
 * set across many ticks until the NPC arrives (or a caller explicitly
 * stops it).
 *
 * Guide takes priority over companion-follow in every role's Watching
 * Instructions (no Continue on the guide node) - live testing showed that
 * without a give-up timeout, an NPC whose target is far away, unreachable,
 * or repeatedly re-requested (almost every reply decides OFFER_GUIDE, even
 * for generic "I'll follow you" lines) would just keep beelining toward a
 * fixed point forever instead of ever falling back to following the
 * player, which from the player's side looks exactly like "he stopped
 * following me after I talked to him." hasTimedOut() is the safety net for
 * that: SeekLandmarkSensor checks it every tick and gives up regardless of
 * why arrival never happened.
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
        NEAREST_WATER,
        /** NearbyLandmarks.closestNamedPosition(npcId, keyword, ...) - a
         * free-text keyword the LLM extracted from what the player actually
         * asked for (see llm_client.py's GUIDE_TARGET tag), matched against
         * both real discoverable zone names AND real unique world-gen
         * prefab names (temples, ruins, structures - see NearbyLandmarks'
         * javadoc). Added 2026-07-22 ("know the map... find what I need") -
         * replaces the old Java-side 9-word WATER_KEYWORDS substring match
         * (still used as a fallback if the model's GUIDE_TARGET is missing
         * or matches nothing) with the model's own understanding of what
         * was actually requested. See Guiding record's keyword field. */
        NAMED
    }

    /** Give up and resume normal behavior (companion-follow, if applicable)
     * after this long without arriving - see the class javadoc. */
    private static final long MAX_GUIDE_MILLIS = 25_000;

    /** keyword is only meaningful (non-null) when target == NAMED. */
    private record Guiding(Target target, String keyword, long startedAtMillis) { }

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();
    private static final ConcurrentHashMap<String, Guiding> GUIDING = new ConcurrentHashMap<>();

    private GuideState() { }

    public static void startGuiding(String npcId, Target target) {
        startGuiding(npcId, target, null);
    }

    public static void startGuiding(String npcId, Target target, String keyword) {
        // If already guiding toward this same target (and keyword, for
        // NAMED), don't touch the start time - README notes "almost every
        // reply decides OFFER_GUIDE," so a player who keeps chatting
        // *during* an active guide used to silently reset the give-up clock
        // on every single turn, defeating hasTimedOut() forever (the exact
        // "stopped following after I talked to him" symptom the timeout
        // exists to prevent - just triggered by talking DURING the guide
        // instead of only once). A genuinely different target/keyword still
        // gets a fresh window, same as starting from not-guiding at all.
        Guiding existing = GUIDING.get(npcId);
        if (existing != null && existing.target() == target && Objects.equals(existing.keyword(), keyword)) {
            return;
        }
        // Logged unconditionally (not just on a mode change) so a live test
        // can directly confirm the orchestrator's OFFER_GUIDE decision
        // actually reached this point, rather than inferring it indirectly
        // from spoken dialogue text alone.
        LOGGER.atInfo().log(npcId + " started guiding (target=" + target
                + (keyword != null ? ", keyword=" + keyword : "") + ")");
        GUIDING.put(npcId, new Guiding(target, keyword, System.currentTimeMillis()));
    }

    /**
     * Maps the model's own GUIDE_TARGET keyword (see llm_client.py's
     * SYSTEM_TEMPLATE rule and NpcAiBridge.SayHandler) to the right
     * startGuiding() call - shared by both real trigger sites
     * (TalkToAIAction/PlayerChatToAIListener) so the mapping only lives in
     * one place. "water" is special-cased to NEAREST_WATER rather than
     * treated as a generic NAMED search: NearbyLandmarks.closestWaterPosition()
     * already has its own wider WATER_SEARCH_RADIUS tuned specifically for
     * how sparse real coastline is (see its javadoc) - a generic NAMED
     * zone/prefab-name substring search for "water" wouldn't match Ocean/
     * Shore zone names at all and would silently fail. An empty/blank
     * keyword (missing tag, or the model's own "landmark" catch-all for "no
     * real destination in mind") falls back to the original NEAREST_LANDMARK
     * behavior - the pre-2026-07-22 default for every guide request.
     */
    public static void startGuidingFromKeyword(String npcId, String keyword) {
        String normalized = keyword == null ? "" : keyword.trim().toLowerCase();
        if (normalized.isEmpty() || normalized.equals("landmark")) {
            startGuiding(npcId, Target.NEAREST_LANDMARK);
        } else if (normalized.equals("water")) {
            startGuiding(npcId, Target.NEAREST_WATER);
        } else {
            startGuiding(npcId, Target.NAMED, normalized);
        }
    }

    public static void stopGuiding(String npcId) {
        GUIDING.remove(npcId);
    }

    public static boolean isGuiding(String npcId) {
        return GUIDING.containsKey(npcId);
    }

    /** The active guide mode for this NPC, or null if not currently guiding. */
    public static Target getTarget(String npcId) {
        Guiding g = GUIDING.get(npcId);
        return g != null ? g.target() : null;
    }

    /** The active guide's keyword (only meaningful when getTarget() ==
     * NAMED), or null. */
    public static String getKeyword(String npcId) {
        Guiding g = GUIDING.get(npcId);
        return g != null ? g.keyword() : null;
    }

    /** True once this NPC has been guiding longer than MAX_GUIDE_MILLIS
     * without arriving - SeekLandmarkSensor should give up regardless of
     * the reason (unreachable, too far, re-requested repeatedly). */
    public static boolean hasTimedOut(String npcId) {
        Guiding g = GUIDING.get(npcId);
        return g != null && (System.currentTimeMillis() - g.startedAtMillis()) > MAX_GUIDE_MILLIS;
    }
}
