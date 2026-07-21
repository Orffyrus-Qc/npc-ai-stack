package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.Message;
import com.hypixel.hytale.server.core.entity.UUIDComponent;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.Universe;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.ActionBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

import java.util.UUID;

/**
 * Runtime "TalkToAI" action. Fires npc-ai-stack's dialogue request for
 * whichever player is currently interacting with this NPC.
 *
 * The player-lookup sequence here (Role.getStateSupport().
 * getInteractionIterationTarget() -> PlayerRef/Player components) is copied
 * from the real, shipped ActionOpenBarterShop.execute() (disassembled from
 * HytaleServer.jar v0.5.7) - that's the built-in barter-shop action, which
 * needs the exact same "who is interacting with me right now" lookup we do.
 */
public class TalkToAIAction extends ActionBase {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    public TalkToAIAction(TalkToAIActionBuilder builder, BuilderSupport support) {
        super(builder);
    }

    @Override
    public boolean execute(Ref<EntityStore> ref, Role role, InfoProvider info,
                            double delta, Store<EntityStore> store) {
        super.execute(ref, role, info, delta, store);

        Ref<EntityStore> targetRef = role.getStateSupport().getInteractionIterationTarget();
        if (targetRef == null) {
            return false;
        }
        PlayerRef playerRef = store.getComponent(targetRef, PlayerRef.getComponentType());
        if (playerRef == null) {
            return false;
        }

        UUIDComponent npcUuid = store.getComponent(ref, UUIDComponent.getComponentType());
        if (npcUuid == null) {
            return false;
        }

        NpcAiBridge bridge = NpcAiPlugin.BRIDGE;
        if (bridge == null) {
            LOGGER.atWarning().log("TalkToAI fired but NpcAiBridge isn't ready yet");
            return false;
        }

        String npcId = npcUuid.getUuid().toString();
        // getRoleName() ("AI_Talker") rather than the raw entity UUID - not
        // a per-NPC display name (Role has no such getter that was found),
        // but at least readable, matching the "visual return to identify
        // the npc" ask instead of showing a UUID in chat.
        String npcName = role.getRoleName();

        UUID playerUuid = playerRef.getUuid();

        NpcAiPlugin.ACTIVE_CONVERSATIONS.put(
                playerUuid,
                new NpcAiPlugin.Conversation(npcId, npcName, System.currentTimeMillis()));

        bridge.registerNpc(npcId, (id, text) -> {
            // Fires on the WebSocket thread, ~1-2s after this method returns
            // (real LLM round trip). The playerRef captured above may be
            // stale by then, so re-resolve a fresh one from the UUID via
            // Universe.get().getPlayer() rather than reusing it - the player
            // may also have disconnected, hence the null check.
            LOGGER.atInfo().log("[" + npcName + "] " + text);
            PlayerRef freshPlayerRef = Universe.get().getPlayer(playerUuid);
            if (freshPlayerRef == null) {
                LOGGER.atInfo().log("Player " + playerUuid + " no longer online, dropping reply from " + npcName);
                return;
            }
            freshPlayerRef.sendMessage(Message.raw("[" + npcName + "] " + text));
        });

        bridge.sendDialogue(
                npcId,
                npcName,
                "npc",
                playerRef.getUuid().toString(),
                "(the player approaches and interacts with you)",
                "");

        return true;
    }
}
