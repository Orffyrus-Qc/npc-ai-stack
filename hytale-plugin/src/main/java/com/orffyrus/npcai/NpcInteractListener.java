package com.orffyrus.npcai;

import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.protocol.InteractionType;
import com.hypixel.hytale.server.core.entity.Entity;
import com.hypixel.hytale.server.core.entity.entities.Player;
import com.hypixel.hytale.server.core.event.events.player.PlayerInteractEvent;
import com.hypixel.hytale.server.core.universe.world.npc.INonPlayerCharacter;

/**
 * Bridges "player interacts with an NPC" to the AI stack.
 *
 * Verified against the real installed HytaleServer.jar (v0.5.7, 2026-07-20)
 * via bytecode inspection - not compiled or run in-game yet, no JDK 25 was
 * available in the environment this was written in. Re-verify against your
 * actual build before trusting this in a live world.
 *
 * PlayerInteractEvent IS marked @Deprecated in this build (no replacement
 * documented anywhere I could find), and NPCMarkerComponent - the more
 * "obvious" way to tag an NPC entity - is @Deprecated(forRemoval=true).
 * INonPlayerCharacter is NOT deprecated, so that's what's used here to
 * decide "is this entity an NPC" instead.
 *
 * Only handles a single click -> single reply today. A real conversation
 * (player types a follow-up in chat, NPC replies again) would need to also
 * hook PlayerChatEvent while the player is "in conversation" with an NPC -
 * that event is IAsyncEvent-based (a different, Function&lt;CompletableFuture&gt;
 * registration shape than the Consumer-based one used below) and wasn't
 * verified confidently enough here to ship; do that as a follow-up once you
 * can compile against the real API and check the exact contract.
 */
public class NpcInteractListener {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    private final NpcAiBridge bridge;

    public NpcInteractListener(NpcAiBridge bridge) {
        this.bridge = bridge;
    }

    public void onPlayerInteract(PlayerInteractEvent event) {
        if (event.getActionType() != InteractionType.Use) {
            return;
        }

        Entity target = event.getTargetEntity();
        if (!(target instanceof INonPlayerCharacter npc)) {
            return;
        }

        Player player = event.getPlayer();
        if (player == null) {
            return;
        }

        // getUuid() is marked deprecated-for-removal in this build (compiler
        // confirmed it via -Xlint:deprecation, no replacement was evident
        // from the bytecode alone - legacyUuid()/setLegacyUUID() exist too,
        // suggesting identity is moving toward something else, maybe the
        // Ref<EntityStore> used elsewhere in this same event). Still works
        // today and is the clearest stable ID available; revisit when it's
        // actually removed.
        String npcId = target.getUuid().toString();
        String npcTypeId = npc.getNPCTypeId();
        String playerId = player.getUuid().toString();

        // Lazily register this NPC's reply handler the first time anyone
        // interacts with it - avoids needing an upfront spawn-time registry.
        bridge.registerNpc(npcId, (id, text) -> {
            // TODO: this callback fires on the WebSocket thread. Before
            // touching world/entity state (chat bubble, dialogue UI, etc.)
            // you MUST hop back onto whatever thread owns this entity's
            // EntityStore - the exact API for that wasn't confirmed here
            // (TaskRegistry.registerTask() tracks task lifecycle for
            // cleanup, it isn't obviously a "run on tick thread" scheduler).
            // Verify against IEntityStore/world tick APIs once you can
            // compile and consult current docs/community before shipping.
            LOGGER.atInfo().log("[" + npcTypeId + "] " + text);
        });

        bridge.sendDialogue(
                npcId,
                npcTypeId,
                npcTypeId,
                playerId,
                "", // Player (entity) has no username getter found here, unlike
                    // PlayerRef.getUsername() used elsewhere - moot anyway since
                    // this listener is confirmed dead (PlayerInteractEvent never
                    // fires for NPCs), kept only as documented above.
                "(the player approaches and interacts with you)",
                "");
    }
}
