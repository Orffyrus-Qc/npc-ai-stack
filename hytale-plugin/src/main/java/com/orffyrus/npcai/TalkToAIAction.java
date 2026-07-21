package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.Message;
import com.hypixel.hytale.server.core.entity.UUIDComponent;
import com.hypixel.hytale.server.core.entity.entities.Player;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.ActionBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

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
        Player player = store.getComponent(targetRef, Player.getComponentType());
        if (player == null) {
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
        String npcName = npcId;
        String playerId = player.getUuid().toString();

        bridge.registerNpc(npcId, (id, text) -> {
            // Fires on the WebSocket thread. PlayerRef.sendMessage() is used
            // elsewhere in the engine from async command handlers (e.g.
            // AbstractPlayerCommand.executeAsync), so it's reasonably assumed
            // safe to call cross-thread here too - unlike mutating this NPC's
            // own entity/world state, which would need a real thread-hop and
            // is NOT done here (no world-state touches in this callback).
            LOGGER.atInfo().log("[" + npcName + "] " + text);
            playerRef.sendMessage(Message.raw("[" + npcName + "] " + text));
        });

        bridge.sendDialogue(
                npcId,
                npcName != null ? npcName : npcId,
                "npc",
                playerId,
                "(the player interacts with you)",
                "");

        return true;
    }
}
