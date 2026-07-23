package com.orffyrus.npcai;

import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.math.vector.Rotation3f;
import com.hypixel.hytale.math.vector.Transform;
import com.hypixel.hytale.protocol.GameMode;
import com.hypixel.hytale.server.core.Message;
import com.hypixel.hytale.server.core.entity.entities.Player;
import com.hypixel.hytale.server.core.event.events.player.PlayerReadyEvent;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.Universe;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.NPCPlugin;
import org.joml.Vector3d;

import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

/**
 * Spawns companion NPC role {@value #PEST_ROLE} next to a player who joins
 * in Adventure (auto) or Creative (optional auto), marks them as that
 * player's companion, and tracks one active Pest per player so we do not
 * stack clones on every reconnect.
 *
 * Independent of {@link MoriAdventureSpawner} - a player can have both Mori
 * and Pest active at once, they are separate companions with separate
 * {@link CompanionState}/{@link GuideState}/{@link ThreatMemory} entries
 * (all keyed by npc role id, not hardcoded to either name).
 *
 * Pest's whole point is its brain: dialogue routes to a real openhands-sdk
 * agent (orchestrator/pest_brain/, AIRole "pest") instead of the fast
 * single-shot llama.cpp path Mori/Adventurer use - see Pest.json's
 * TalkToAI action and docs/PEST_OPENHANDS_BRAIN.md. Follow/combat/
 * perception (this class, Pest.json's behavior tree) are pure engine-side
 * AI, identical mechanism to Mori, zero LLM involved.
 *
 * Uses the real {@link NPCPlugin#spawnNPC} API confirmed on HytaleServer.jar
 * (same call MoriAdventureSpawner already validated live). Flock asset
 * argument is null (solo companion, not a flock).
 *
 * Also used by {@link PestCommand} ({@code /pest spawn}).
 */
public final class PestAdventureSpawner {

    public static final String PEST_ROLE = "Pest";
    public static final String PEST_DISPLAY_NAME = "Pest";
    public static final String PEST_AI_ROLE = "pest";

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    /** Players who already have an active Pest this session. */
    private static final Set<UUID> SPAWNED_FOR = ConcurrentHashMap.newKeySet();

    /**
     * When true, Creative-mode ready also auto-spawns Pest (handy for testing).
     * Adventure always auto-spawns. Override anytime with {@code /pest spawn}.
     */
    public static volatile boolean AUTO_SPAWN_IN_CREATIVE = true;

    public PestAdventureSpawner() { }

    public void onPlayerReady(PlayerReadyEvent event) {
        try {
            Player player = event.getPlayer();
            if (player == null) {
                return;
            }
            GameMode mode = player.getGameMode();
            if (mode != GameMode.Adventure
                    && !(AUTO_SPAWN_IN_CREATIVE && mode == GameMode.Creative)) {
                LOGGER.atFine().log(
                        "Skip Pest auto-spawn — mode=" + mode
                                + " (use /pest spawn or switch to Adventure)");
                return;
            }
            PlayerRef playerRef = player.getPlayerRef();
            if (playerRef == null) {
                return;
            }
            spawnForPlayer(playerRef, false, "auto (" + mode + ")", null);
        } catch (Exception e) {
            LOGGER.atWarning().log("PestAdventureSpawner.onPlayerReady failed: " + e);
        }
    }

    /**
     * Spawn (or re-mark) Pest for a player.
     *
     * @param force when true, clears the session spawn flag and spawns again
     * @param reason log / status reason
     * @param onDone optional callback on the player thread with a short status line
     */
    public static void spawnForPlayer(PlayerRef playerRef, boolean force, String reason,
                                      Consumer<String> onDone) {
        if (playerRef == null) {
            if (onDone != null) {
                onDone.accept("No player.");
            }
            return;
        }
        UUID playerUuid = playerRef.getUuid();
        if (force) {
            SPAWNED_FOR.remove(playerUuid);
        }
        if (!SPAWNED_FOR.add(playerUuid)) {
            CompanionState.markCompanion(PEST_ROLE, playerUuid);
            String msg = "Pest is already active for you this session. "
                    + "Use /pest spawn force to spawn another, or talk with: Pest, hi";
            if (onDone != null) {
                onDone.accept(msg);
            }
            return;
        }

        Transform transform = playerRef.getTransform();
        if (transform == null || transform.getPosition() == null) {
            SPAWNED_FOR.remove(playerUuid);
            if (onDone != null) {
                onDone.accept("Could not read your position.");
            }
            return;
        }
        Vector3d pos = transform.getPosition();

        World world = Universe.get().getWorld(playerRef.getWorldUuid());
        if (world == null) {
            world = Universe.get().getDefaultWorld();
        }
        if (world == null) {
            SPAWNED_FOR.remove(playerUuid);
            LOGGER.atWarning().log("No world available to spawn Pest for " + playerRef.getUsername());
            if (onDone != null) {
                onDone.accept("No world available.");
            }
            return;
        }

        final World spawnWorld = world;
        // Offset from Mori's own spawn offset (2, 0, 1) so the two companions
        // don't spawn stacked on top of each other when both are active.
        final Vector3d spawnAt = new Vector3d(pos.x - 2.0, pos.y, pos.z + 1.0);
        final Rotation3f rotation = new Rotation3f(0f, 0f, 0f);
        final String username = playerRef.getUsername();

        spawnWorld.execute(() -> {
            try {
                EntityStore entityStore = spawnWorld.getEntityStore();
                if (entityStore == null) {
                    LOGGER.atWarning().log("EntityStore null when spawning Pest");
                    SPAWNED_FOR.remove(playerUuid);
                    notify(playerUuid, onDone, "Entity store unavailable.");
                    return;
                }
                Store<EntityStore> store = entityStore.getStore();
                NPCPlugin npcPlugin = NPCPlugin.get();
                if (npcPlugin == null || !npcPlugin.hasRoleName(PEST_ROLE)) {
                    LOGGER.atWarning().log(
                            "Pest role not loaded yet (hasRoleName=false). "
                                    + "Is Server/NPC/Roles/Pest.json in the plugin pack?");
                    SPAWNED_FOR.remove(playerUuid);
                    notify(playerUuid, onDone,
                            "Pest role not loaded. Is NpcAiStack jar installed with IncludesAssetPack?");
                    return;
                }

                var pair = npcPlugin.spawnNPC(store, PEST_ROLE, null, spawnAt, rotation);
                if (pair == null) {
                    LOGGER.atWarning().log("spawnNPC returned null for Pest near " + username);
                    SPAWNED_FOR.remove(playerUuid);
                    notify(playerUuid, onDone, "spawnNPC failed (null). Try /npc spawn Pest");
                    return;
                }

                CompanionState.markCompanion(PEST_ROLE, playerUuid);
                LOGGER.atInfo().log(
                        "Spawned Pest companion for " + username
                                + " reason=" + reason + " at " + spawnAt);

                String ok = "[Pest] I'm here — say my name in chat and I'll answer. "
                        + "I'll follow and fight with you. (/pest status for details)";
                PlayerRef fresh = Universe.get().getPlayer(playerUuid);
                if (fresh != null) {
                    fresh.sendMessage(Message.raw(ok));
                }
                if (onDone != null) {
                    onDone.accept("Spawned Pest (" + reason + ").");
                }
            } catch (Exception e) {
                LOGGER.atWarning().log("Failed to spawn Pest for " + username + ": " + e);
                SPAWNED_FOR.remove(playerUuid);
                notify(playerUuid, onDone, "Spawn failed: " + e.getMessage());
            }
        });
    }

    private static void notify(UUID playerUuid, Consumer<String> onDone, String msg) {
        if (onDone != null) {
            onDone.accept(msg);
        }
        PlayerRef fresh = Universe.get().getPlayer(playerUuid);
        if (fresh != null && onDone == null) {
            fresh.sendMessage(Message.raw(msg));
        }
    }

    public static String statusLine(UUID playerUuid) {
        boolean flagged = SPAWNED_FOR.contains(playerUuid);
        boolean companion = CompanionState.isCompanion(PEST_ROLE)
                && playerUuid.equals(CompanionState.getOwner(PEST_ROLE));
        boolean bridge = NpcAiPlugin.BRIDGE != null;
        return "Pest status — sessionSpawn=" + flagged
                + " companionOwned=" + companion
                + " brainBridge=" + (bridge ? "connected-or-connecting" : "null")
                + " autoCreative=" + AUTO_SPAWN_IN_CREATIVE
                + " | chat: Pest, hello | /pest spawn [force]";
    }

    public static void clearSpawnFlag(UUID playerUuid) {
        SPAWNED_FOR.remove(playerUuid);
    }

    public static boolean hasSpawned(UUID playerUuid) {
        return SPAWNED_FOR.contains(playerUuid);
    }
}
