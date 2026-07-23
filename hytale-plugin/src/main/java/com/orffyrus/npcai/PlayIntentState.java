package com.orffyrus.npcai;

import com.hypixel.hytale.logger.HytaleLogger;

import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Last OpenHands-brain "play like a player" intent the orchestrator sent
 * for an NPC ({@code play_action} / {@code play_target} on the say wire).
 *
 * Actionable movement intents are applied through {@link GuideState}
 * (see {@link #applyFromOrchestrator}); this map keeps the full intent
 * for logging, future sensors, and outcome reporting.
 *
 * Intent vocabulary (orchestrator agent_brain):
 * gather | craft | go_to | explore | fight | rest | mine | build | trade
 */
public final class PlayIntentState {

    public record Intent(String action, String target, UUID playerId, long setAtMillis) { }

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();
    private static final ConcurrentHashMap<String, Intent> INTENTS = new ConcurrentHashMap<>();

    private PlayIntentState() { }

    /**
     * Apply orchestrator play fields. Safe no-op when playAction is blank.
     * Movement-like actions also start guiding when guide was not already
     * started from action=="offer_guide" in the same reply.
     */
    public static void applyFromOrchestrator(String npcId, UUID playerId,
                                             String playAction, String playTarget,
                                             boolean guideAlreadyStarted) {
        if (playAction == null || playAction.isBlank()) {
            return;
        }
        String action = playAction.trim().toLowerCase();
        String target = playTarget != null ? playTarget.trim() : "";
        INTENTS.put(npcId, new Intent(action, target, playerId, System.currentTimeMillis()));
        LOGGER.atInfo().log("PlayIntent npc=" + npcId + " action=" + action
                + " target=" + target + " player=" + playerId);

        if (guideAlreadyStarted) {
            // offer_guide + guide_target already drove GuideState this tick
            return;
        }
        switch (action) {
            case "go_to", "gather", "mine", "craft", "trade", "build" -> {
                if (!target.isEmpty()) {
                    GuideState.startGuidingFromKeyword(npcId, playerId, target);
                } else {
                    GuideState.startGuiding(npcId, playerId, GuideState.Target.NEAREST_LANDMARK);
                }
            }
            case "explore" -> GuideState.startGuiding(
                    npcId, playerId, GuideState.Target.NEAREST_LANDMARK);
            case "rest" -> GuideState.stopGuiding(npcId);
            default -> {
                // fight / unknown: recorded only for now
            }
        }
    }

    public static Intent get(String npcId) {
        return INTENTS.get(npcId);
    }

    public static void clear(String npcId) {
        INTENTS.remove(npcId);
    }
}
