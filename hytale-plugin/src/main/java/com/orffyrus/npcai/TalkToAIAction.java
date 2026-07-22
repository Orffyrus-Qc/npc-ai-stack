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

        String threat = ThreatMemory.describe(npcId);
        String fullSituation = threat.isEmpty() ? situation : situation + " " + threat;

        // Marks this NPC as awaiting a reply so IsAwaitingReplySensor's
        // "thinking" particle shows above its head until the callback below
        // clears it - see AwaitingReplyState's javadoc.
        AwaitingReplyState.start(npcId);

        bridge.sendDialogue(
                npcId,
                npcName,
                aiRole,
                playerUuid.toString(),
                playerRef.getUsername(),
                "(the player approaches and interacts with you)",
                fullSituation,
                (id, text, action, isCompanion) -> {
                    // Fires on the WebSocket thread, ~1-2s after this method
                    // returns (real LLM round trip). The playerRef captured
                    // above may be stale by then, so re-resolve a fresh one
                    // from the UUID via Universe.get().getPlayer() rather
                    // than reusing it - the player may also have
                    // disconnected, hence the null check.
                    AwaitingReplyState.clear(id);
                    if (text.isEmpty()) {
                        // The orchestrator legitimately has nothing to say
                        // this turn (GPU busy/timeout - see
                        // priority_queue.py, BUSY_LINES removed 2026-07-21)
                        // - stay silent rather than invent a line, same as
                        // ambient already does for an empty reply.
                        LOGGER.atFine().log(npcName + " had nothing to say this turn");
                        return;
                    }
                    LOGGER.atInfo().log("[" + npcName + "] " + text);
                    PlayerRef freshPlayerRef = Universe.get().getPlayer(playerUuid);
                    if (freshPlayerRef == null) {
                        LOGGER.atInfo().log("Player " + playerUuid + " no longer online, dropping reply from " + npcName);
                        return;
                    }
                    freshPlayerRef.sendMessage(Message.raw("[" + npcName + "] " + text));
                    // Resynced from the orchestrator's Postgres-backed taming
                    // truth on EVERY reply, not just when action=="accept_tame" -
                    // the plugin's own CompanionState is a plain in-memory map
                    // that resets on every server restart, while Postgres
                    // doesn't, so a previously-tamed NPC would otherwise never
                    // follow again after a restart (the model has no reason to
                    // re-decide ACCEPT_TAME for a player it already considers a
                    // companion).
                    if (isCompanion) {
                        CompanionState.markCompanion(id);
                    }
                    if ("open_shop".equals(action)) {
                        // Only queues the request (thread-safe, no Store access
                        // here) - see PendingShopOpen's javadoc for why the
                        // actual PageManager call happens later, on the tick
                        // thread, in OpenShopIfRequestedAction instead of
                        // directly here.
                        PendingShopOpen.request(playerUuid);
                    } else if ("offer_guide".equals(action)) {
                        // SeekLandmarkSensor checks this every tick and walks
                        // toward the target NearbyLandmarks resolves,
                        // auto-stopping once arrived - see both classes'
                        // javadoc. No real player free-text is available here
                        // (this fires from a click, not chat - playerText
                        // above is a canned string), so there's nothing to
                        // keyword-match for a water request; always the
                        // generic nearest-landmark mode.
                        GuideState.startGuiding(id, GuideState.Target.NEAREST_LANDMARK);
                    }
                });

        return true;
    }
}
