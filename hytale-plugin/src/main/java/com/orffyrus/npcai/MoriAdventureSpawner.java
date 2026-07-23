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
 * Spawns companion NPC role {@value #MORI_ROLE} next to a player who joins
 * in Adventure (auto) or Creative (optional auto), marks them as that
 * player's companion, and tracks one active Mori per player so we do not
 * stack clones on every reconnect.
 *
 * Uses the real {@link NPCPlugin#spawnNPC} API confirmed on HytaleServer.jar.
 * Flock asset argument is null (solo companion, not a flock).
 *
 * Also used by {@link MoriCommand} ({@code /mori spawn}).
 */
public final class MoriAdventureSpawner {

    public static final String MORI_ROLE = "Mori";
    public static final String MORI_DISPLAY_NAME = "Mori";
    public static final String MORI_AI_ROLE = "mori";

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    /** Players who already have an active Mori this session. */
    private static final Set<UUID> SPAWNED_FOR = ConcurrentHashMap.newKeySet();

    /**
     * When true, Creative-mode ready also auto-spawns Mori (handy for testing).
     * Adventure always auto-spawns. Override anytime with {@code /mori spawn}.
     */
    public static volatile boolean AUTO_SPAWN_IN_CREATIVE = true;

    public MoriAdventureSpawner() { }

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
                        "Skip Mori auto-spawn — mode=" + mode
                                + " (use /mori spawn or switch to Adventure)");
                return;
            }
            PlayerRef playerRef = player.getPlayerRef();
            if (playerRef == null) {
                return;
            }
            spawnForPlayer(playerRef, false, "auto (" + mode + ")", null);
        } catch (Exception e) {
            LOGGER.atWarning().log("MoriAdventureSpawner.onPlayerReady failed: " + e);
        }
    }

    /**
     * Spawn (or re-mark) Mori for a player.
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
            CompanionState.markCompanion(MORI_ROLE, playerUuid);
            String msg = "Mori is already active for you this session. "
                    + "Use /mori spawn force to spawn another, or talk with: Mori, hi";
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
            LOGGER.atWarning().log("No world available to spawn Mori for " + playerRef.getUsername());
            if (onDone != null) {
                onDone.accept("No world available.");
            }
            return;
        }

        final World spawnWorld = world;
        final Vector3d spawnAt = new Vector3d(pos.x + 2.0, pos.y, pos.z + 1.0);
        final Rotation3f rotation = new Rotation3f(0f, 0f, 0f);
        final String username = playerRef.getUsername();

        spawnWorld.execute(() -> {
            try {
                EntityStore entityStore = spawnWorld.getEntityStore();
                if (entityStore == null) {
                    LOGGER.atWarning().log("EntityStore null when spawning Mori");
                    SPAWNED_FOR.remove(playerUuid);
                    notify(playerUuid, onDone, "Entity store unavailable.");
                    return;
                }
                Store<EntityStore> store = entityStore.getStore();
                NPCPlugin npcPlugin = NPCPlugin.get();
                if (npcPlugin == null || !npcPlugin.hasRoleName(MORI_ROLE)) {
                    LOGGER.atWarning().log(
                            "Mori role not loaded yet (hasRoleName=false). "
                                    + "Is Server/NPC/Roles/Mori.json in the plugin pack?");
                    SPAWNED_FOR.remove(playerUuid);
                    notify(playerUuid, onDone,
                            "Mori role not loaded. Is NpcAiStack jar installed with IncludesAssetPack?");
                    return;
                }

                var pair = npcPlugin.spawnNPC(store, MORI_ROLE, null, spawnAt, rotation);
                if (pair == null) {
                    LOGGER.atWarning().log("spawnNPC returned null for Mori near " + username);
                    SPAWNED_FOR.remove(playerUuid);
                    notify(playerUuid, onDone, "spawnNPC failed (null). Try /npc spawn Mori");
                    return;
                }

                CompanionState.markCompanion(MORI_ROLE, playerUuid);
                LOGGER.atInfo().log(
                        "Spawned Mori companion for " + username
                                + " reason=" + reason + " at " + spawnAt);

                String ok = "[Mori] I'm here — say my name in chat and I'll answer. "
                        + "I'll follow and fight with you. (/mori status for details)";
                PlayerRef fresh = Universe.get().getPlayer(playerUuid);
                if (fresh != null) {
                    fresh.sendMessage(Message.raw(ok));
                }
                if (onDone != null) {
                    onDone.accept("Spawned Mori (" + reason + ").");
                }
            } catch (Exception e) {
                LOGGER.atWarning().log("Failed to spawn Mori for " + username + ": " + e);
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
        boolean companion = CompanionState.isCompanion(MORI_ROLE)
                && playerUuid.equals(CompanionState.getOwner(MORI_ROLE));
        boolean bridge = NpcAiPlugin.BRIDGE != null;
        return "Mori status — sessionSpawn=" + flagged
                + " companionOwned=" + companion
                + " brainBridge=" + (bridge ? "connected-or-connecting" : "null")
                + " autoCreative=" + AUTO_SPAWN_IN_CREATIVE
                + " | chat: Mori, hello | /mori spawn [force]";
    }

    public static void clearSpawnFlag(UUID playerUuid) {
        SPAWNED_FOR.remove(playerUuid);
    }

    public static boolean hasSpawned(UUID playerUuid) {
        return SPAWNED_FOR.contains(playerUuid);
    }
}
