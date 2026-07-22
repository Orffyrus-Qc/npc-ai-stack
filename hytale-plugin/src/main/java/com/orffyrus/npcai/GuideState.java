package com.orffyrus.npcai;

import com.hypixel.hytale.logger.HytaleLogger;

import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "am I actively guiding someone somewhere right now, and to what
 * kind of destination" state, set the moment the orchestrator's reply
 * carries action="offer_guide" (the NPC's own in-character decision - see
 * llm_client.py's SYSTEM_TEMPLATE rules). Read every tick by
 * SeekLandmarkSensor, which also clears this automatically once the NPC
 * arrives and finishes lingering there - see that class's javadoc.
 *
 * This is persistent while active, same shape as CompanionState - it stays
 * set across many ticks until the NPC arrives + finishes lingering (or a
 * caller explicitly stops it).
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
 * why arrival never happened. See MAX_STUCK_MILLIS's javadoc for why this
 * is a no-progress timeout, not a flat one, and ARRIVAL_LINGER_MILLIS's
 * javadoc for the separate "don't instantly resume following the moment
 * you arrive" fix - both from the same 2026-07-22 live report ("npc begin
 * to lead but return to his following duty").
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
         * was actually requested. See Guiding's keyword field.
         *
         * Also checked FIRST against the requesting player's own real,
         * native Hytale map markers (NearbyLandmarks.closestPlayerMarkerPosition,
         * a player-placed/named pin on their in-game map - see that
         * method's javadoc) before falling back to the zone/prefab search
         * above - a player's own marker name is an exact, precise
         * destination they chose themselves ("home", "the mine"), strictly
         * better than a fuzzy match against Hytale's own internal asset
         * names whenever one exists. Added 2026-07-22 ("lead player through
         * the map to a precise location"). */
        NAMED
    }

    /** Give up and resume normal behavior (companion-follow, if applicable)
     * once this long has passed with NO real progress toward the target -
     * not a flat clock from when guiding started (the original 25s version
     * of this constant). A flat from-start clock counted time spent
     * legitimately paused - waiting for a lagging owner (see the "wait for
     * the player" node in the role JSONs), fleeing a nearby hostile - against
     * the exact same budget as actual walking, so any real guide request
     * that included even one such pause, or simply led somewhere more than
     * a couple hundred blocks away, blew through the timeout and reverted
     * to following before ever arriving. recordProgress()/hasTimedOut()
     * below track "closest distance reached so far" instead, so a guide
     * that's slowly-but-genuinely getting closer never times out no matter
     * how long it takes, while one that's truly stuck (unreachable,
     * bouncing off terrain) still gives up. */
    private static final long MAX_STUCK_MILLIS = 60_000;

    /** Once arrived, stand at the landmark this long before resuming normal
     * behavior (companion-follow) - see markArrived()/isLingering(). Without
     * this, arrival and resuming "seek toward owner" happened in the exact
     * same tick (no Continue on either the guide-tier or follow-tier
     * Instructions nodes), so a short guide read as "began to lead but
     * immediately went back to following me" - live-reported 2026-07-22,
     * confirmed via the real server log: both real guide requests that
     * session arrived within 4-5 real seconds, meaning the player never got
     * even one tick of "we're here" before follow resumed. */
    private static final long ARRIVAL_LINGER_MILLIS = 5_000;

    /** keyword is only meaningful (non-null) when target == NAMED. playerId
     * is whoever's real chat/interaction triggered this guide (NOT
     * necessarily this NPC's tamed owner - offer_guide isn't gated on
     * companion status, see TalkToAIAction/PlayerChatToAIListener) - needed
     * so a NAMED search can check THAT player's own map markers, not
     * arbitrarily the owner's.
     * Not a record: lastDistanceSq/lastProgressMillis/arrivedAtMillis are
     * updated in place every tick by a live SeekLandmarkSensor without
     * disturbing target/keyword/startedAtMillis or clobbering the "don't
     * reset if already guiding toward this same target" dedupe in
     * startGuiding() below. */
    private static final class Guiding {
        final Target target;
        final String keyword;
        final UUID playerId;
        final long startedAtMillis;
        volatile long lastProgressMillis;
        volatile double lastDistanceSq = Double.MAX_VALUE;
        volatile long arrivedAtMillis = 0;

        Guiding(Target target, String keyword, UUID playerId, long startedAtMillis) {
            this.target = target;
            this.keyword = keyword;
            this.playerId = playerId;
            this.startedAtMillis = startedAtMillis;
            this.lastProgressMillis = startedAtMillis;
        }
    }

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();
    private static final ConcurrentHashMap<String, Guiding> GUIDING = new ConcurrentHashMap<>();

    private GuideState() { }

    public static void startGuiding(String npcId, UUID playerId, Target target) {
        startGuiding(npcId, playerId, target, null);
    }

    public static void startGuiding(String npcId, UUID playerId, Target target, String keyword) {
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
        if (existing != null && existing.target == target && Objects.equals(existing.keyword, keyword)) {
            return;
        }
        // Logged unconditionally (not just on a mode change) so a live test
        // can directly confirm the orchestrator's OFFER_GUIDE decision
        // actually reached this point, rather than inferring it indirectly
        // from spoken dialogue text alone.
        LOGGER.atInfo().log(npcId + " started guiding (target=" + target
                + (keyword != null ? ", keyword=" + keyword : "") + ")");
        GUIDING.put(npcId, new Guiding(target, keyword, playerId, System.currentTimeMillis()));
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
    public static void startGuidingFromKeyword(String npcId, UUID playerId, String keyword) {
        String normalized = keyword == null ? "" : keyword.trim().toLowerCase();
        if (normalized.isEmpty() || normalized.equals("landmark")) {
            startGuiding(npcId, playerId, Target.NEAREST_LANDMARK);
        } else if (normalized.equals("water")) {
            startGuiding(npcId, playerId, Target.NEAREST_WATER);
        } else {
            startGuiding(npcId, playerId, Target.NAMED, normalized);
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
        return g != null ? g.target : null;
    }

    /** The active guide's keyword (only meaningful when getTarget() ==
     * NAMED), or null. */
    public static String getKeyword(String npcId) {
        Guiding g = GUIDING.get(npcId);
        return g != null ? g.keyword : null;
    }

    /** Whoever's chat/interaction started this guide - see Guiding's
     * playerId javadoc for why this isn't necessarily the tamed owner. */
    public static UUID getPlayerId(String npcId) {
        Guiding g = GUIDING.get(npcId);
        return g != null ? g.playerId : null;
    }

    /** Called every tick by SeekLandmarkSensor (while still en route, not
     * yet arrived) with the current squared 2D distance to the guide
     * target. Resets the no-progress clock whenever this tick's distance is
     * a real improvement over the closest distance seen so far - see
     * MAX_STUCK_MILLIS's javadoc for why a flat from-start clock was wrong.
     * No-op if this NPC isn't currently guiding. */
    public static void recordProgress(String npcId, double distanceSq) {
        Guiding g = GUIDING.get(npcId);
        if (g != null && distanceSq < g.lastDistanceSq) {
            g.lastDistanceSq = distanceSq;
            g.lastProgressMillis = System.currentTimeMillis();
        }
    }

    /** True once this NPC has gone MAX_STUCK_MILLIS without getting any
     * closer to its guide target - SeekLandmarkSensor should give up
     * regardless of the reason (unreachable, too far off the beaten path,
     * re-requested repeatedly). */
    public static boolean hasTimedOut(String npcId) {
        Guiding g = GUIDING.get(npcId);
        return g != null && (System.currentTimeMillis() - g.lastProgressMillis) > MAX_STUCK_MILLIS;
    }

    /** Marks the moment this NPC reached its guide target - see
     * isLingering()/ARRIVAL_LINGER_MILLIS. Idempotent: only the first call
     * after a fresh startGuiding() actually sets the timestamp. */
    public static void markArrived(String npcId) {
        Guiding g = GUIDING.get(npcId);
        if (g != null && g.arrivedAtMillis == 0) {
            g.arrivedAtMillis = System.currentTimeMillis();
        }
    }

    /** True once markArrived() has been called for this NPC (regardless of
     * whether the linger window is still open) - lets SeekLandmarkSensor
     * tell "still traveling" apart from "arrived (lingering or done)"
     * without recomputing/re-searching for the target again post-arrival. */
    public static boolean hasArrived(String npcId) {
        Guiding g = GUIDING.get(npcId);
        return g != null && g.arrivedAtMillis != 0;
    }

    /** True for ARRIVAL_LINGER_MILLIS after markArrived() - see that
     * constant's javadoc for why this exists. Only meaningful once
     * hasArrived() is true. */
    public static boolean isLingering(String npcId) {
        Guiding g = GUIDING.get(npcId);
        return g != null && g.arrivedAtMillis != 0
                && (System.currentTimeMillis() - g.arrivedAtMillis) < ARRIVAL_LINGER_MILLIS;
    }
}
