package com.orffyrus.npcai;

import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.event.events.player.PlayerChatEvent;
import com.hypixel.hytale.server.core.event.events.player.PlayerInteractEvent;
import com.hypixel.hytale.server.core.plugin.JavaPlugin;
import com.hypixel.hytale.server.core.plugin.JavaPluginInit;
import com.hypixel.hytale.server.npc.NPCPlugin;

import javax.annotation.Nonnull;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Entry point. Connects to the npc-ai-stack orchestrator (see the repo root
 * docker-compose.yml) and wires NPC interactions to it.
 *
 * 2026-07-20 finding: PlayerInteractEvent (registered below) never actually
 * fires for NPC clicks in v0.5.7 - confirmed by live play testing, zero
 * dialogue requests ever reached the orchestrator. Real NPC interactions
 * (e.g. the built-in barter shops) run entirely through a JSON-driven
 * Interaction/Action system, not a Java event. The real hook is the
 * "TalkToAI" action type registered below, built by disassembling the
 * shipped "OpenBarterShop" action (NPCShopPlugin/ActionOpenBarterShop) as
 * a working reference - see TalkToAIAction.java. PlayerInteractEvent is
 * left registered in case it's ever useful for non-NPC interactions
 * (blocks, items) - it is NOT the NPC-talk hook.
 */
public class NpcAiPlugin extends JavaPlugin {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    // TODO: move to plugin config once you've confirmed the Config API
    // pattern (PluginBase.withConfig(...) exists but wasn't explored here).
    private static final String ORCHESTRATOR_URL = "ws://localhost:8765";

    /** Static so TalkToAIAction (built by the engine's own factory system,
     * not by us) can reach it - same singleton pattern NPCPlugin.get() uses. */
    static NpcAiBridge BRIDGE;

    /**
     * Which NPC each player is currently "in conversation" with, keyed by
     * PlayerRef UUID. Started by TalkToAIAction on interact; continued or
     * ended by PlayerChatToAIListener. Deliberately just an in-memory map,
     * not persisted - conversations don't need to survive a server restart.
     */
    static final Map<UUID, Conversation> ACTIVE_CONVERSATIONS = new ConcurrentHashMap<>();

    record Conversation(String npcId, String npcName, String aiRole, String situation,
                        long lastActivityMillis) {
        Conversation refreshed() {
            return new Conversation(npcId, npcName, aiRole, situation, System.currentTimeMillis());
        }
    }

    public NpcAiPlugin(@Nonnull JavaPluginInit init) {
        super(init);
        LOGGER.atInfo().log("Loaded " + this.getName() + " v" + this.getManifest().getVersion());
    }

    @Override
    protected void setup() {
        LOGGER.atInfo().log("Connecting to npc-ai-stack orchestrator at " + ORCHESTRATOR_URL);
        BRIDGE = new NpcAiBridge(ORCHESTRATOR_URL);
        BRIDGE.connect();

        NpcInteractListener listener = new NpcInteractListener(BRIDGE);
        this.getEventRegistry().registerGlobal(PlayerInteractEvent.class, listener::onPlayerInteract);

        NPCPlugin.get().registerCoreComponentType("TalkToAI", TalkToAIActionBuilder::new);
        LOGGER.atInfo().log("Registered TalkToAI NPC action type");

        NPCPlugin.get().registerCoreComponentType("NoteNearbyThreat", NoteNearbyThreatActionBuilder::new);
        LOGGER.atInfo().log("Registered NoteNearbyThreat NPC action type");

        NPCPlugin.get().registerCoreComponentType("OpenShopIfRequested", OpenShopIfRequestedActionBuilder::new);
        LOGGER.atInfo().log("Registered OpenShopIfRequested NPC action type");

        NPCPlugin.get().registerCoreComponentType("IsCompanion", IsCompanionSensorBuilder::new);
        LOGGER.atInfo().log("Registered IsCompanion NPC sensor type");

        PlayerChatToAIListener chatListener = new PlayerChatToAIListener(BRIDGE);
        this.getEventRegistry().registerAsyncGlobal(PlayerChatEvent.class, chatListener::onChat);
        LOGGER.atInfo().log("Registered PlayerChatEvent -> AI conversation listener");
    }
}
