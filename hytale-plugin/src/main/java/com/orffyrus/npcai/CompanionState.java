package com.orffyrus.npcai;

import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "am I a tamed companion, and whose" state, set the moment the
 * orchestrator's reply carries action="accept_tame" (already enforced
 * server-side against the 1-tamed-NPC-per-player rule by then - see
 * taming.py - so no further validation is needed client-side). Read every
 * tick by IsCompanionSensor to gate the "Seek" BodyMotion that makes a
 * companion follow the player, and by EntityFilterIsOwner to restrict who,
 * specifically, it follows/defends - see Adventurer.json's Watching state.
 *
 * This is a persistent flag: once a companion, always a companion for the
 * rest of that entity's lifetime.
 *
 * 2026-07-22: added real owner tracking (ownerId), replacing the earlier
 * companion-only-boolean version. Before this, the "Player" sensor paired
 * with IsCompanion matched the NEAREST player, not specifically the owner -
 * correct only when the owner happens to be the nearest player (true in all
 * solo testing so far), wrong the moment any other player is nearer. Since
 * taming.py already enforces exactly one owner per NPC at the DB level,
 * this is what actually makes "1 companion per player" behave like an
 * owned relationship rather than "1 companion total, followed
 * opportunistically." See EntityFilterIsOwner.java for the filter that
 * reads this.
 */
public final class CompanionState {

    private static final ConcurrentHashMap<String, UUID> OWNERS = new ConcurrentHashMap<>();

    private CompanionState() { }

    public static void markCompanion(String npcId, UUID ownerId) {
        OWNERS.put(npcId, ownerId);
    }

    public static boolean isCompanion(String npcId) {
        return OWNERS.containsKey(npcId);
    }

    public static UUID getOwner(String npcId) {
        return OWNERS.get(npcId);
    }
}
