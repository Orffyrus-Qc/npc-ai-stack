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
    // See GuideState.Target.NEAREST_WATER's javadoc: Hytale has no discrete
    // "lake"/"river" zone, only Ocean/Shallow_Ocean/Shore - this just decides
    // which of the two NearbyLandmarks queries to point the guide at, based
    // on what the player actually asked for.
    private static final Set<String> WATER_KEYWORDS =
            Set.of("lake", "river", "pond", "sea", "ocean", "water", "swim", "shore", "beach");

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
        NpcAiPlugin.Conversation conversation = NpcAiPlugin.ACTIVE_CONVERSATIONS.get(playerUuid);
        if (conversation == null) {
            return event;
        }
        if (System.currentTimeMillis() - conversation.lastActivityMillis() > NpcAiPlugin.CONVERSATION_TIMEOUT_MILLIS) {
            NpcAiPlugin.ACTIVE_CONVERSATIONS.remove(playerUuid);
            return event;
        }

        String content = event.getContent();
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
                (id, text, action, isCompanion) -> {
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
                        CompanionState.markCompanion(id);
                    }
                    if ("open_shop".equals(action)) {
                        PendingShopOpen.request(playerUuid);
                    } else if ("offer_guide".equals(action)) {
                        GuideState.startGuiding(id, guideTargetFor(content));
                    }
                });

        return event;
    }

    private static GuideState.Target guideTargetFor(String playerText) {
        String lower = playerText.toLowerCase();
        for (String keyword : WATER_KEYWORDS) {
            if (lower.contains(keyword)) {
                return GuideState.Target.NEAREST_WATER;
            }
        }
        return GuideState.Target.NEAREST_LANDMARK;
    }
}
