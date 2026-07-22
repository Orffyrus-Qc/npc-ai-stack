package com.orffyrus.npcai;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-NPC "am I a tamed companion" flag, set the moment the orchestrator's
 * reply carries action="accept_tame" (already enforced server-side against
 * the 1-tamed-NPC-per-player rule by then - see taming.py - so no further
 * validation is needed client-side). Read every tick by IsCompanionSensor
 * to gate the "Seek" BodyMotion that makes a companion follow the player -
 * see Adventurer.json's Watching state.
 *
 * Unlike PendingShopOpen (consume-once), this is a persistent flag: once a
 * companion, always a companion for the rest of that entity's lifetime.
 *
 * KNOWN SIMPLIFICATION (v1): this only tracks "is this NPC a companion at
 * all", not "whose companion is it" - the accompanying IsCompanion+Player
 * sensor combo makes a companion seek the NEAREST player, not specifically
 * its owner. Correct for solo/small-group testing (the only scenario this
 * has been tested in); a populated multiplayer world would need real
 * owner-specific entity filtering (a custom Sensor matching a particular
 * player UUID among iterated candidates) to behave correctly with
 * multiple players and/or multiple companions present at once.
 */
public final class CompanionState {

    private static final ConcurrentHashMap<String, Boolean> COMPANIONS = new ConcurrentHashMap<>();

    private CompanionState() { }

    public static void markCompanion(String npcId) {
        COMPANIONS.put(npcId, Boolean.TRUE);
    }

    public static boolean isCompanion(String npcId) {
        return COMPANIONS.containsKey(npcId);
    }
}
