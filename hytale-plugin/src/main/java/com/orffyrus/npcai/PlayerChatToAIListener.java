package com.orffyrus.npcai;

import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.Message;
import com.hypixel.hytale.server.core.event.events.player.PlayerChatEvent;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.Universe;

import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

/**
 * Continues an NPC conversation via ordinary chat, once TalkToAIAction has
 * started one. Registered via getEventRegistry().registerAsyncGlobal(
 * PlayerChatEvent.class, this::onChat) - PlayerChatEvent is IAsyncEvent, a
 * different registration shape than the Consumer-based registerGlobal used
 * for PlayerInteractEvent/TalkToAI.
 *
 * The dispatch/broadcast contract here was confirmed by disassembling
 * GamePacketHandler (which fires PlayerChatEvent and, in its own
 * whenComplete callback, checks event.isCancelled() before broadcasting to
 * event.getTargets()) - so setCancelled(true) here reliably suppresses the
 * normal chat broadcast, same as it does for any other listener.
 */
public class PlayerChatToAIListener {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    private static final Set<String> EXIT_WORDS = Set.of("bye", "goodbye", "exit", "leave", "stop");

    private final NpcAiBridge bridge;

    public PlayerChatToAIListener(NpcAiBridge bridge) {
        this.bridge = bridge;
    }

    public CompletableFuture<PlayerChatEvent> onChat(CompletableFuture<PlayerChatEvent> future) {
        return future.thenApply(this::handle);
    }

    private PlayerChatEvent handle(PlayerChatEvent event) {
        if (event.isCancelled()) {
            return event;
        }
        PlayerRef sender = event.getSender();
        if (sender == null) {
            return event;
        }

        UUID playerUuid = sender.getUuid();
        String content = event.getContent();
        NpcAiPlugin.Conversation conversation = NpcAiPlugin.ACTIVE_CONVERSATIONS.get(playerUuid);

        // Name-address Mori in ordinary chat ("Mori, help me") even without a
        // prior F-key TalkToAI interact — adventure companion mode.
        if (conversation == null && MoriChatRouter.addressesMori(content)) {
            conversation = new NpcAiPlugin.Conversation(
                    MoriAdventureSpawner.MORI_ROLE,
                    MoriAdventureSpawner.MORI_DISPLAY_NAME,
                    MoriAdventureSpawner.MORI_AI_ROLE,
                    "You are Mori, the player's adventure companion. You follow them, "
                            + "fight threats, and can see the map and world around you.",
                    System.currentTimeMillis());
            NpcAiPlugin.ACTIVE_CONVERSATIONS.put(playerUuid, conversation);
            CompanionState.markCompanion(MoriAdventureSpawner.MORI_ROLE, playerUuid);
            content = MoriChatRouter.stripAddress(content);
        }

        // Same pattern for Pest — a separate, independent companion whose
        // brain is a real openhands-sdk agent (orchestrator/pest_brain/),
        // routed via AIRole "pest" (see main.py's handle_pest_dialogue).
        // Checked only when Mori didn't already claim this message, so
        // "Pest, ..." never gets swallowed by Mori's own address check
        // (the two regexes don't overlap, but keeping this mutually
        // exclusive with the branch above avoids ever starting two
        // conversations from one line of chat).
        if (conversation == null && PestChatRouter.addressesPest(content)) {
            conversation = new NpcAiPlugin.Conversation(
                    PestAdventureSpawner.PEST_ROLE,
                    PestAdventureSpawner.PEST_DISPLAY_NAME,
                    PestAdventureSpawner.PEST_AI_ROLE,
                    "You are Pest, the player's adventure companion. You follow them, "
                            + "fight threats, and can see the map and world around you.",
                    System.currentTimeMillis());
            NpcAiPlugin.ACTIVE_CONVERSATIONS.put(playerUuid, conversation);
            CompanionState.markCompanion(PestAdventureSpawner.PEST_ROLE, playerUuid);
            content = PestChatRouter.stripAddress(content);
        }

        if (conversation == null) {
            return event;
        }
        if (System.currentTimeMillis() - conversation.lastActivityMillis() > NpcAiPlugin.CONVERSATION_TIMEOUT_MILLIS) {
            NpcAiPlugin.ACTIVE_CONVERSATIONS.remove(playerUuid);
            // If the timed-out line still addresses Mori, start a fresh turn.
            if (MoriChatRouter.addressesMori(content)) {
                conversation = new NpcAiPlugin.Conversation(
                        MoriAdventureSpawner.MORI_ROLE,
                        MoriAdventureSpawner.MORI_DISPLAY_NAME,
                        MoriAdventureSpawner.MORI_AI_ROLE,
                        "You are Mori, the player's adventure companion.",
                        System.currentTimeMillis());
                NpcAiPlugin.ACTIVE_CONVERSATIONS.put(playerUuid, conversation);
                CompanionState.markCompanion(MoriAdventureSpawner.MORI_ROLE, playerUuid);
                content = MoriChatRouter.stripAddress(content);
            } else if (PestChatRouter.addressesPest(content)) {
                // Same re-address-after-timeout handling for Pest.
                conversation = new NpcAiPlugin.Conversation(
                        PestAdventureSpawner.PEST_ROLE,
                        PestAdventureSpawner.PEST_DISPLAY_NAME,
                        PestAdventureSpawner.PEST_AI_ROLE,
                        "You are Pest, the player's adventure companion.",
                        System.currentTimeMillis());
                NpcAiPlugin.ACTIVE_CONVERSATIONS.put(playerUuid, conversation);
                CompanionState.markCompanion(PestAdventureSpawner.PEST_ROLE, playerUuid);
                content = PestChatRouter.stripAddress(content);
            } else {
                return event;
            }
        }

        // This player is in an active conversation - never let this message
        // hit normal server chat, whether or not we end up forwarding it.
        event.setCancelled(true);

        if (content == null || content.isBlank()) {
            return event;
        }

        if (EXIT_WORDS.contains(content.trim().toLowerCase())) {
            NpcAiPlugin.ACTIVE_CONVERSATIONS.remove(playerUuid);
            sender.sendMessage(Message.raw("(You end the conversation with " + conversation.npcName() + ")"));
            return event;
        }

        // If already talking to Mori/Pest and the line re-addresses them, strip name.
        if (MoriChatRouter.isMoriRole(conversation.npcId()) && MoriChatRouter.addressesMori(content)) {
            content = MoriChatRouter.stripAddress(content);
        } else if (PestChatRouter.isPestRole(conversation.npcId()) && PestChatRouter.addressesPest(content)) {
            content = PestChatRouter.stripAddress(content);
        }

        // Literal manual movement command ("walk forward", "jump", "walk
        // forward and jump") - a real, live-confirmed escape hatch for
        // companion pathfinding getting logically stuck (see
        // ManualMoveState's javadoc). Deliberately intercepted here, BEFORE
        // touching the orchestrator at all - same reasoning as EXIT_WORDS
        // above: this is a literal mechanical command, not something that
        // needs language understanding, and it must keep working even if
        // the orchestrator/GPU is slow or down, which is exactly when a
        // player is most likely to reach for it.
        ManualMoveState.Kind moveKind = ManualMoveChatParser.parse(content);
        if (moveKind != null) {
            NpcAiPlugin.ACTIVE_CONVERSATIONS.put(playerUuid, conversation.refreshed());
            ManualMoveState.request(conversation.npcId(), moveKind);
            String echo = switch (moveKind) {
                case FORWARD -> "walk forward";
                case JUMP -> "jump";
                case FORWARD_JUMP -> "walk forward and jump";
            };
            sender.sendMessage(Message.raw("(You tell " + conversation.npcName() + " to " + echo + ".)"));
            return event;
        }

        NpcAiPlugin.ACTIVE_CONVERSATIONS.put(playerUuid, conversation.refreshed());

        String npcName = conversation.npcName();
        // ThreatMemory is live (a threat can appear/disappear mid-conversation)
        // and re-checked fresh on every turn - only conversation.situation()
        // (static world geography) is safe to have cached once at conversation
        // start. No NPC entity access needed here: ThreatMemory is a plain
        // static cache keyed by npcId, kept updated by the NPC's own ongoing
        // tick-based Sensor regardless of who's asking.
        String threat = ThreatMemory.describe(conversation.npcId());
        String situation = conversation.situation();
        String fullSituation = threat.isEmpty() ? situation : situation + " " + threat;
        // 2026-07-22, "npc must sense its environment": same live,
        // no-NPC-entity-access-needed treatment as threat, above - kept
        // updated by the NPC's own ongoing tick-based NoteNearbyObjectsAction.
        String nearbyObjects = NearbyObjects.describe(conversation.npcId());
        if (!nearbyObjects.isEmpty()) {
            fullSituation += " " + nearbyObjects;
        }
        // Map sense: zones, prefabs, player markers within ~200 blocks (Mori + Pest).
        if (MoriChatRouter.isMoriRole(conversation.npcId()) || PestChatRouter.isPestRole(conversation.npcId())) {
            String mapSense = MapSense200.describeCached(conversation.npcId());
            if (!mapSense.isEmpty()) {
                fullSituation += " " + mapSense;
            }
        }

        // See TalkToAIAction's matching comment - shows the "thinking"
        // particle above this NPC's head until the callback below clears it.
        AwaitingReplyState.start(conversation.npcId());

        bridge.sendDialogue(
                conversation.npcId(),
                npcName,
                conversation.aiRole(),
                playerUuid.toString(),
                sender.getUsername(),
                content,
                fullSituation,
                (id, text, action, isCompanion, guideTarget, playAction, playTarget) -> {
                    // Same staleness concern as TalkToAIAction: this fires on
                    // the WebSocket thread after the real LLM round trip, so
                    // re-resolve a fresh PlayerRef from the UUID instead of
                    // reusing `sender`.
                    AwaitingReplyState.clear(id);
                    if (text.isEmpty()) {
                        // See TalkToAIAction's matching comment - the
                        // orchestrator legitimately has nothing to say this
                        // turn, stay silent rather than invent a line.
                        LOGGER.atFine().log(npcName + " had nothing to say this turn");
                        return;
                    }
                    LOGGER.atInfo().log("[" + npcName + "] " + text);
                    PlayerRef freshSender = Universe.get().getPlayer(playerUuid);
                    if (freshSender == null) {
                        LOGGER.atInfo().log("Player " + playerUuid + " no longer online, dropping reply from " + npcName);
                        return;
                    }
                    freshSender.sendMessage(Message.raw("[" + npcName + "] " + text));
                    // Resynced every reply from Postgres-backed taming truth -
                    // see the matching comment in TalkToAIAction for why this
                    // can't just ride on action=="accept_tame" alone.
                    if (isCompanion) {
                        CompanionState.markCompanion(id, playerUuid);
                    }
                    boolean guided = false;
                    if ("offer_guide".equals(action)) {
                        // 2026-07-22: uses the model's own GUIDE_TARGET
                        // keyword now (see GuideState.startGuidingFromKeyword's
                        // javadoc) instead of a hardcoded 9-word
                        // WATER_KEYWORDS substring match against the raw
                        // player text - the model already has to understand
                        // what the player asked for to decide OFFER_GUIDE
                        // in the first place, so it can extract a much
                        // richer keyword ("temple", "desert", "cave", ...)
                        // than a fixed water-or-not binary ever could.
                        GuideState.startGuidingFromKeyword(id, playerUuid, guideTarget);
                        guided = true;
                    }
                    PlayIntentState.applyFromOrchestrator(
                            id, playerUuid, playAction, playTarget, guided);
                });

        return event;
    }
}
