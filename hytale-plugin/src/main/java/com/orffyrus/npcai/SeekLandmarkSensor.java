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

import java.util.UUID;

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
 * Auto-stops guiding (returns false, clears GuideState) once the NPC has
 * lingered at the target for GuideState.ARRIVAL_LINGER_MILLIS (see below),
 * or if NearbyLandmarks never found anything to guide to in the first
 * place - so a stale/impossible guide request doesn't leave the NPC "stuck"
 * trying forever.
 *
 * 2026-07-22 rewrite: resolves a NAMED target via NearbyLandmarks.
 * resolveGuideTarget() - the same real candidate list (player's own map
 * markers, then real nearby zones/prefabs) already shown to the model as
 * situation text, so the destination the NPC actually walks to can never
 * diverge from what it said out loud. Also now: logs the resolved target
 * once (plus a periodic distance-remaining line while en route -
 * GuideState.shouldLogStuck()) and drops a real, native map marker at the
 * destination (NearbyLandmarks.createGuideMarker()/removeGuideMarker()) so
 * the player can see exactly where they're headed on their own map.
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

        TransformComponent tc = store.getComponent(ref, TransformComponent.getComponentType());
        if (tc == null) {
            return false;
        }
        Vector3d pos = tc.getPosition();

        if (GuideState.hasArrived(npcId)) {
            // Already reached the landmark on an earlier tick - don't
            // re-search for the target or recompute distance at all, just
            // stand here (own current position, so "Seek" is a no-op) until
            // the linger window (GuideState.ARRIVAL_LINGER_MILLIS) elapses.
            // 2026-07-22 real bug found live ("npc begin to lead but return
            // to his following duty"): this used to stopGuiding()
            // immediately on arrival, in the SAME tick companion-follow
            // would then take over (no Continue on either tier's
            // Instructions nodes) - a short/nearby guide (confirmed via the
            // real server log: both real guide requests that session
            // arrived in 4-5 real seconds) read as "began to lead but
            // immediately went back to following me," since the player
            // never got even a moment of "we're here" first.
            if (GuideState.isLingering(npcId)) {
                positionProvider.setTarget(pos.x, pos.y, pos.z);
                return true;
            }
            LOGGER.atInfo().log(npcId + " done showing the player the landmark, resuming normal behavior");
            NearbyLandmarks.removeGuideMarker(ref, store, GuideState.getCreatedMarkerId(npcId));
            GuideState.stopGuiding(npcId);
            return false;
        }

        // 2026-07-22 rewrite ("npc tell so many incoherent things... rewrite
        // it from the beginning"): a real session's log showed the model
        // inventing fictional destinations ("Steve's Fort", "Salt Lake" -
        // not real places) that then silently failed to resolve to
        // anything reachable. resolveGuideTarget() now checks the SAME
        // real candidate list (player's own map markers, then real nearby
        // zones/prefabs) already shown to the model as situation text - see
        // NearbyLandmarks' class javadoc - before falling back to the
        // older, looser world-gen search for anything still ungrounded.
        String keyword = GuideState.getKeyword(npcId);
        UUID playerId = GuideState.getPlayerId(npcId);
        // Whether the target hasn't been resolved yet this guide session -
        // gates both the one-time diagnostic log line below and guide-
        // marker creation, so neither repeats every tick.
        boolean firstResolution = GuideState.getCreatedMarkerId(npcId) == null;

        Vector3i target;
        if (mode == GuideState.Target.NEAREST_WATER) {
            target = NearbyLandmarks.closestWaterPosition(npcId, ref, store);
        } else if (mode == GuideState.Target.NAMED) {
            target = keyword != null
                    ? NearbyLandmarks.resolveGuideTarget(npcId, keyword, playerId, ref, store) : null;
            if (target == null) {
                // 2026-07-22 real bug found live: every NAMED search in a
                // real session failed ("keyword=flower", "keyword=wilderness"
                // - neither is a substring of any real zone/prefab name),
                // and giving up outright meant the companion just stood
                // there doing nothing for every one of those requests -
                // from the player's side, indistinguishable from "the guide
                // feature stopped working." A specific keyword not matching
                // anything is a real, expected limitation, so fall back to
                // the general nearest-landmark search instead of stopping
                // guiding entirely - "I don't know a place called that, but
                // here's somewhere I do know" beats doing nothing.
                target = NearbyLandmarks.closestPosition(npcId);
            }
        } else {
            target = NearbyLandmarks.closestPosition(npcId);
        }
        if (target == null) {
            LOGGER.atInfo().log(npcId + " couldn't find a " + mode + " to guide toward - giving up");
            NearbyLandmarks.removeGuideMarker(ref, store, GuideState.getCreatedMarkerId(npcId));
            GuideState.stopGuiding(npcId);
            return false;
        }

        double dx = target.x - pos.x, dz = target.z - pos.z;
        double distanceSq = dx * dx + dz * dz;

        if (firstResolution) {
            // One-time diagnostic line for exactly what this session's real
            // log was missing: the resolved coordinate, the NPC's current
            // position, and the real straight-line distance between them -
            // see GuideState.STUCK_LOG_INTERVAL_MILLIS's javadoc for why.
            LOGGER.atInfo().log(String.format(
                    "%s guide target resolved to (%d,%d,%d), currently at (%.0f,%.0f,%.0f), distance=%.0f blocks",
                    npcId, target.x, target.y, target.z, pos.x, pos.y, pos.z, Math.sqrt(distanceSq)));
            // Drop a real, native map marker at the destination - see
            // NearbyLandmarks.createGuideMarker()'s javadoc. Labeled with
            // the model's own keyword when available so the pin reads as
            // "Adventurer: fort" rather than a generic placeholder.
            String label = "Adventurer: " + (keyword != null ? keyword : "guide destination");
            String markerId = NearbyLandmarks.createGuideMarker(ref, store, playerId, label, target);
            GuideState.setCreatedMarkerId(npcId, markerId != null ? markerId : "");
        }

        // Reset the give-up clock whenever real progress happens - see
        // GuideState.MAX_STUCK_MILLIS's javadoc for why a flat
        // elapsed-since-start timeout wrongly penalized legitimate
        // waiting/fleeing pauses (and long-but-real walks) the same as
        // being genuinely stuck.
        GuideState.recordProgress(npcId, distanceSq);
        if (GuideState.hasTimedOut(npcId)) {
            LOGGER.atInfo().log(npcId + " gave up guiding (target=" + mode + ") - no progress for too long");
            NearbyLandmarks.removeGuideMarker(ref, store, GuideState.getCreatedMarkerId(npcId));
            GuideState.stopGuiding(npcId);
            return false;
        }
        if (GuideState.shouldLogStuck(npcId)) {
            LOGGER.atInfo().log(String.format(
                    "%s still guiding (target=%s), %.0f blocks from target", npcId, mode, Math.sqrt(distanceSq)));
        }

        if (Math.sqrt(distanceSq) < ARRIVED_DISTANCE) {
            LOGGER.atInfo().log(npcId + " arrived at the landmark it was guiding toward - pausing here a moment");
            GuideState.markArrived(npcId);
            positionProvider.setTarget(pos.x, pos.y, pos.z);
            return true;
        }

        positionProvider.setTarget((double) target.x, (double) target.y, (double) target.z);
        return true;
    }

    @Override
    public InfoProvider getSensorInfo() {
        return positionProvider;
    }
}
