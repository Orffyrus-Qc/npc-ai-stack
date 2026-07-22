package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.math.vector.Rotation3f;
import com.hypixel.hytale.server.core.asset.type.blocktype.config.BlockType;
import com.hypixel.hytale.server.core.modules.entity.component.TransformComponent;
import com.hypixel.hytale.server.core.universe.PlayerRef;
import com.hypixel.hytale.server.core.universe.Universe;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.chunk.WorldChunk;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import org.joml.Vector3d;

import java.util.UUID;

/**
 * Resolves what a specific player is currently looking/pointing toward -
 * a real raycast along their actual view direction, used as a fallback
 * when the NPC's own ambient "look around him" scan
 * (NearbyObjects/NoteNearbyObjectsAction) doesn't find anything notable
 * nearby. 2026-07-22: "when I ask about map items the npc must look
 * around him and if it not enough precise he will take the item or
 * group of item i am pointing at."
 *
 * Real look-direction math, reverse-derived from disassembly rather than
 * guessed: the real, shipped Rotation3f.lookAt(Vector3d) computes a
 * rotation FROM a direction vector as yaw = atan2(-dx, -dz),
 * pitch = asin(dy / length) (confirmed by reading its actual bytecode).
 * Inverting that gives the direction vector FROM a rotation - see
 * computeLookDirection() below.
 *
 * No existing higher-level raycast utility was reachable for this: the
 * real, shipped RaycastSelector.selectTargetPosition() (used by the
 * game's own interaction/reticle system - the closest real match) needs
 * a CommandBuffer&lt;EntityStore&gt;, and Store.takeCommandBuffer() is
 * package-private - the exact same unreachable-package problem as
 * PrefabSearchUtil earlier this session. This steps along the ray
 * directly using the same World.getBlockType(x,y,z) mechanism
 * NoteNearbyObjectsAction already uses, checking only for "notable"
 * blocks (NearbyObjects.isNotableBlockId()) rather than doing a full
 * precise solid-block hit-test - sufficient for "what is the player
 * pointing roughly toward," not a precision aiming reticle.
 *
 * Only resolvable at conversation-START (TalkToAIAction has both ECS
 * access AND the specific requesting player's identity together;
 * PlayerChatToAIListener's ongoing chat turns have neither, same
 * constraint NearbyLandmarks' static candidate data already lives with) -
 * a real, accepted scoping limitation: if the player starts pointing at
 * something only mid-conversation, this won't pick it up until they
 * start a fresh conversation.
 */
public final class PlayerPointing {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    /** Real interaction-range-ish cap, blocks - matches the same rough
     * order of magnitude as typical block-interaction range in this
     * engine (not confirmed via a specific constant, a reasonable bound
     * for "pointing at something nearby" regardless). */
    private static final double MAX_DISTANCE = 6.0;
    private static final double STEP = 0.25;
    /** Rough standing-eye-height offset above a player's feet position -
     * not confirmed via a specific disassembled constant, a reasonable
     * approximation for a humanoid player model. */
    private static final double EYE_HEIGHT = 1.5;

    private PlayerPointing() { }

    /**
     * Returns the id of the closest notable block along the given
     * player's real, live look direction within MAX_DISTANCE, or null if
     * none (player offline, components unavailable, or nothing notable
     * along the ray). Never throws.
     */
    public static String findPointedAtNotableBlock(UUID playerId, Ref<EntityStore> npcRef, Store<EntityStore> store) {
        try {
            TransformComponent npcTc = store.getComponent(npcRef, TransformComponent.getComponentType());
            if (npcTc == null) return null;
            WorldChunk chunk = npcTc.getChunk();
            if (chunk == null) return null;
            World world = chunk.getWorld();
            if (world == null) return null;

            PlayerRef playerRef = Universe.get().getPlayer(playerId);
            if (playerRef == null) return null;
            Ref<EntityStore> playerEntityRef = playerRef.getReference();
            if (playerEntityRef == null) return null;
            TransformComponent playerTc = store.getComponent(playerEntityRef, TransformComponent.getComponentType());
            if (playerTc == null) return null;

            Vector3d playerPos = playerTc.getPosition();
            Vector3d eyePos = new Vector3d(playerPos.x, playerPos.y + EYE_HEIGHT, playerPos.z);
            Vector3d dir = computeLookDirection(playerTc.getRotation());

            for (double d = STEP; d <= MAX_DISTANCE; d += STEP) {
                int bx = (int) Math.floor(eyePos.x + dir.x * d);
                int by = (int) Math.floor(eyePos.y + dir.y * d);
                int bz = (int) Math.floor(eyePos.z + dir.z * d);
                BlockType type = world.getBlockType(bx, by, bz);
                if (type == null) continue;
                String id = type.getId();
                if (id != null && NearbyObjects.isNotableBlockId(id)) {
                    return id;
                }
            }
            return null;
        } catch (Exception e) {
            LOGGER.atWarning().log("PlayerPointing raycast failed for player " + playerId + ": " + e);
            return null;
        }
    }

    /**
     * Real look-direction unit vector from a player's real Rotation3f -
     * see class javadoc for the disassembly this was reverse-derived
     * from: yaw = atan2(-dx, -dz), pitch = asin(dy / length) inverts to
     * dx = -sin(yaw)*cos(pitch), dz = -cos(yaw)*cos(pitch),
     * dy = sin(pitch).
     */
    private static Vector3d computeLookDirection(Rotation3f rotation) {
        double yaw = rotation.yaw();
        double pitch = rotation.pitch();
        double cosPitch = Math.cos(pitch);
        return new Vector3d(-Math.sin(yaw) * cosPitch, Math.sin(pitch), -Math.cos(yaw) * cosPitch);
    }
}
