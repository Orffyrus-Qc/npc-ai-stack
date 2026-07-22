package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.asset.type.blocktype.config.BlockType;
import com.hypixel.hytale.server.core.modules.entity.component.TransformComponent;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.chunk.WorldChunk;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.ActionBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;
import org.joml.Vector3d;

/**
 * Runtime "NoteNearbyObjects" action - a real, tick-based scan (throttled
 * via NearbyObjects.shouldRescan(), NOT every tick) of the actual world
 * BLOCKS around this NPC for anything "notable" (see
 * NearbyObjects.isNotableBlockId() for the current real-block-id filter -
 * flowers/plants/mushrooms/ore/soil/rock/wood), feeding NearbyObjects the
 * same way NoteNearbyThreatAction feeds ThreatMemory. Runs on a bare
 * "Any" sensor (see Adventurer.json) since it's not gated on any entity
 * being nearby - blocks aren't entities.
 *
 * Unlike every other npc-ai-stack feature so far (zones, prefabs, map
 * markers - all queried via existing high-level real engine utilities
 * like SpiralSearchUtil), this reads raw world BLOCKS directly:
 * World.getBlockType(x, y, z) (confirmed via disassembly - inherited via
 * ChunkAccessor from IChunkAccessorSync's default implementation, which
 * resolves the owning chunk internally via
 * ChunkUtil.indexChunkFromBlock(x, z) + getChunk(long) and only then
 * calls BlockAccessor.getBlock(x, y, z) on THAT chunk with the SAME
 * unmodified world-absolute x/y/z - no manual world-to-chunk-local
 * coordinate math needed on this plugin's side at all).
 *
 * Deliberately reports the raw block id (e.g. "Plant_Flower_Common_Blue")
 * rather than trying to parse a color/description out of it in Java -
 * see NearbyObjects' class javadoc for why that's left to the model.
 * Confirmed via the real, shipped Server/BlockTypeList/
 * PlantsAndTrees.json asset that color is baked directly into these
 * specific real block ids (e.g. "Plant_Flower_Bushy_Red",
 * "Plant_Flower_Common_Blue" - 9+ distinct real color-named flower
 * variants), not a separate queryable property - so the raw id alone is
 * already a meaningful, real fact, not something this plugin needs to
 * further interpret.
 */
public class NoteNearbyObjectsAction extends ActionBase {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    /** Horizontal search radius, blocks. Kept small - this is "what's on
     * the ground right around you," not a wide-area survey. */
    private static final int SCAN_RADIUS_XZ = 5;
    /** Vertical search range relative to the NPC's own feet - flowers/
     * plants sit at or just above ground level, never far above/below. */
    private static final int SCAN_Y_BELOW = 2;
    private static final int SCAN_Y_ABOVE = 1;

    public NoteNearbyObjectsAction(NoteNearbyObjectsActionBuilder builder, BuilderSupport support) {
        super(builder);
    }

    @Override
    public boolean execute(Ref<EntityStore> ref, Role role, InfoProvider info,
                            double delta, Store<EntityStore> store) {
        super.execute(ref, role, info, delta, store);
        String npcId = role.getRoleName();
        if (!NearbyObjects.shouldRescan(npcId)) {
            return true;
        }
        try {
            TransformComponent tc = store.getComponent(ref, TransformComponent.getComponentType());
            if (tc == null) return false;
            WorldChunk chunk = tc.getChunk();
            if (chunk == null) return false;
            World world = chunk.getWorld();
            if (world == null) return false;

            Vector3d pos = tc.getPosition();
            int cx = (int) Math.floor(pos.x);
            int cy = (int) Math.floor(pos.y);
            int cz = (int) Math.floor(pos.z);

            String bestId = null;
            double bestDistSq = Double.MAX_VALUE;
            for (int dx = -SCAN_RADIUS_XZ; dx <= SCAN_RADIUS_XZ; dx++) {
                for (int dz = -SCAN_RADIUS_XZ; dz <= SCAN_RADIUS_XZ; dz++) {
                    for (int dy = -SCAN_Y_BELOW; dy <= SCAN_Y_ABOVE; dy++) {
                        BlockType type = world.getBlockType(cx + dx, cy + dy, cz + dz);
                        if (type == null) continue;
                        String id = type.getId();
                        if (id == null || !NearbyObjects.isNotableBlockId(id)) continue;
                        double distSq = (double) dx * dx + (double) dy * dy + (double) dz * dz;
                        if (distSq < bestDistSq) {
                            bestDistSq = distSq;
                            bestId = id;
                        }
                    }
                }
            }
            NearbyObjects.record(npcId, bestId, Math.sqrt(bestDistSq));
            if (bestId != null) {
                LOGGER.atFine().log(npcId + " noticed " + bestId + " nearby");
            }
        } catch (Exception e) {
            LOGGER.atWarning().log("NoteNearbyObjects scan failed for " + npcId + ": " + e);
        }
        return true;
    }
}
