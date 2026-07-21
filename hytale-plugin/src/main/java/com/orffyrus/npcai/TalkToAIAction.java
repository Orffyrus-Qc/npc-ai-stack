package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.Message;
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
 *
 * npc_id sent to the orchestrator is the NPC ROLE's name (role.getRoleName(),
 * e.g. "Elder_Miri"), not the spawned entity's own UUID. Roles are spawned
 * fresh with a new random entity UUID every time ("/npc spawn" or a world
 * reload), so keying personality/memory on entity UUID would silently start
 * a brand-new "NPC" from the orchestrator's point of view on every respawn.
 * Keying on the stable role name instead means the same named character
 * keeps its trust/memory across respawns. Trade-off: two simultaneously
 * spawned entities of the same role would share one identity/conversation
 * slot - fine as long as each named character exists as a single instance
 * in the world, which is the intended setup here.
 */
public class TalkToAIAction extends ActionBase {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    private final String configuredDisplayName;
    private final String aiRole;

    public TalkToAIAction(TalkToAIActionBuilder builder, BuilderSupport support) {
        super(builder);
        this.configuredDisplayName = builder.displayName;
        this.aiRole = builder.aiRole;
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

        NpcAiBridge bridge = NpcAiPlugin.BRIDGE;
        if (bridge == null) {
            LOGGER.atWarning().log("TalkToAI fired but NpcAiBridge isn't ready yet");
            return false;
        }

        String npcId = role.getRoleName();
        String npcName = (configuredDisplayName != null && !configuredDisplayName.isEmpty())
                ? configuredDisplayName : npcId;

        UUID playerUuid = playerRef.getUuid();
        // Computed once here (Ref/Store are only available in this ECS
        // callback, not in PlayerChatToAIListener's async chat handling) and
        // cached on the Conversation so every later chat turn reuses it
        // rather than needing NPC entity access at all. Landmarks are static
        // (world geography never changes for a stationary NPC), so this is
        // safe to cache forever - unlike ThreatMemory below, which is live
        // and re-checked on every single turn, not cached on the Conversation.
        String situation = NearbyLandmarks.describe(npcId, ref, store);

        NpcAiPlugin.ACTIVE_CONVERSATIONS.put(
                playerUuid,
                new NpcAiPlugin.Conversation(npcId, npcName, aiRole, situation, System.currentTimeMillis()));

        bridge.registerNpc(npcId, (id, text, action) -> {
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
            if ("open_shop".equals(action)) {
                // Only queues the request (thread-safe, no Store access here) -
                // see PendingShopOpen's javadoc for why the actual PageManager
                // call happens later, on the tick thread, in
                // OpenShopIfRequestedAction instead of directly here.
                PendingShopOpen.request(playerUuid);
            } else if ("accept_tame".equals(action)) {
                // The orchestrator has already enforced the 1-tamed-NPC-
                // per-player rule server-side by the time this arrives - no
                // further validation needed here, just flip the flag
                // IsCompanionSensor checks every tick.
                CompanionState.markCompanion(npcId);
            }
        });

        String threat = ThreatMemory.describe(npcId);
        String fullSituation = threat.isEmpty() ? situation : situation + " " + threat;

        bridge.sendDialogue(
                npcId,
                npcName,
                aiRole,
                playerUuid.toString(),
                playerRef.getUsername(),
                "(the player approaches and interacts with you)",
                fullSituation);

        return true;
    }
}
