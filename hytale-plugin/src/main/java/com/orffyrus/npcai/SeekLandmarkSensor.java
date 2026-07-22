package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.modules.entity.component.TransformComponent;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.SensorBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;
import com.hypixel.hytale.server.npc.sensorinfo.PositionProvider;
import org.joml.Vector3d;
import org.joml.Vector3i;

/**
 * Runtime "SeekLandmark" sensor - true while GuideState.isGuiding(this NPC)
 * and NearbyLandmarks has a cached target for it, supplying that landmark's
 * real coordinate as the sensor's target position. A paired "Seek"
 * BodyMotion in the same Instructions node (see the role JSONs' Watching
 * state) reads this via InfoProvider.getPositionProvider() - the exact same
 * mechanism companion-follow and combat-chase already use, just fed a fixed
 * world coordinate instead of an entity's live position.
 *
 * PositionProvider (confirmed via disassembly to implement InfoProvider
 * itself, and to expose setTarget(double,double,double)) is reused directly
 * as this sensor's own getSensorInfo() - no separate wrapper needed.
 *
 * Auto-stops guiding (returns false, clears GuideState) once the NPC is
 * within ARRIVED_DISTANCE of the target, or if NearbyLandmarks never found
 * anything to guide to in the first place - so a stale/impossible guide
 * request doesn't leave the NPC "stuck" trying forever.
 */
public class SeekLandmarkSensor extends SensorBase {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();
    private static final double ARRIVED_DISTANCE = 6.0;

    private final PositionProvider positionProvider = new PositionProvider();

    public SeekLandmarkSensor(SeekLandmarkSensorBuilder builder, BuilderSupport support) {
        super(builder);
    }

    @Override
    public boolean matches(Ref<EntityStore> ref, Role role, double delta, Store<EntityStore> store) {
        if (!super.matches(ref, role, delta, store)) {
            return false;
        }
        String npcId = role.getRoleName();
        GuideState.Target mode = GuideState.getTarget(npcId);
        if (mode == null) {
            return false;
        }
        if (GuideState.hasTimedOut(npcId)) {
            // Guide has no Continue in Watching's Instructions, so it takes
            // full priority over companion-follow until this returns false -
            // without this check, an unreachable/very-far/repeatedly-
            // re-requested target would leave the NPC beelining toward a
            // fixed point forever instead of ever following the player again.
            LOGGER.atInfo().log(npcId + " gave up guiding (target=" + mode + ") after taking too long");
            GuideState.stopGuiding(npcId);
            return false;
        }
        Vector3i target;
        if (mode == GuideState.Target.NEAREST_WATER) {
            target = NearbyLandmarks.closestWaterPosition(npcId, ref, store);
        } else if (mode == GuideState.Target.NAMED) {
            String keyword = GuideState.getKeyword(npcId);
            target = keyword != null ? NearbyLandmarks.closestNamedPosition(npcId, keyword, ref, store) : null;
        } else {
            target = NearbyLandmarks.closestPosition(npcId);
        }
        if (target == null) {
            LOGGER.atInfo().log(npcId + " couldn't find a " + mode + " to guide toward - giving up");
            GuideState.stopGuiding(npcId);
            return false;
        }

        TransformComponent tc = store.getComponent(ref, TransformComponent.getComponentType());
        if (tc == null) {
            return false;
        }
        Vector3d pos = tc.getPosition();
        double dx = target.x - pos.x, dz = target.z - pos.z;
        if (Math.sqrt(dx * dx + dz * dz) < ARRIVED_DISTANCE) {
            LOGGER.atInfo().log(npcId + " arrived at the landmark it was guiding toward");
            GuideState.stopGuiding(npcId);
            return false;
        }

        positionProvider.setTarget((double) target.x, (double) target.y, (double) target.z);
        return true;
    }

    @Override
    public InfoProvider getSensorInfo() {
        return positionProvider;
    }
}
