package com.orffyrus.npcai;

import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.event.events.player.PlayerInteractEvent;
import com.hypixel.hytale.server.core.plugin.JavaPlugin;
import com.hypixel.hytale.server.core.plugin.JavaPluginInit;

import javax.annotation.Nonnull;

/**
 * Entry point. Connects to the npc-ai-stack orchestrator (see the repo root
 * docker-compose.yml) and wires NPC interactions to it.
 *
 * Pattern verified against the real template (github.com/realBritakee/
 * hytale-template-plugin) and this machine's installed HytaleServer.jar
 * (v0.5.7) - see NpcInteractListener's docstring for what's still unverified.
 */
public class NpcAiPlugin extends JavaPlugin {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    // TODO: move to plugin config once you've confirmed the Config API
    // pattern (PluginBase.withConfig(...) exists but wasn't explored here).
    private static final String ORCHESTRATOR_URL = "ws://localhost:8765";

    private NpcAiBridge bridge;

    public NpcAiPlugin(@Nonnull JavaPluginInit init) {
        super(init);
        LOGGER.atInfo().log("Loaded " + this.getName() + " v" + this.getManifest().getVersion());
    }

    @Override
    protected void setup() {
        LOGGER.atInfo().log("Connecting to npc-ai-stack orchestrator at " + ORCHESTRATOR_URL);
        bridge = new NpcAiBridge(ORCHESTRATOR_URL);
        bridge.connect();

        NpcInteractListener listener = new NpcInteractListener(bridge);
        this.getEventRegistry().registerGlobal(PlayerInteractEvent.class, listener::onPlayerInteract);
    }
}
