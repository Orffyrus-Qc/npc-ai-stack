package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.modules.entity.component.TransformComponent;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.ActionBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.IPositionProvider;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;
import org.joml.Vector3d;

/**
 * Runtime "NoteNearbyThreat" action. Fires whenever the preceding Sensor
 * (a "Mob"+"Attitude" sensor prioritising Hostile, mirroring the real,
 * shipped Trork combat AI's own threat detection - see
 * Component_Trork_Instruction_Panic.json/Search.json, disassembled/read
 * from the game's own Assets.zip) matches a hostile entity nearby.
 *
 * The matched entity's position comes from InfoProvider.getPositionProvider()
 * (an IPositionProvider - confirmed via disassembly to expose getX/Y/Z() and
 * getTarget(), populated by whichever Sensor ran just before this Action in
 * the same Instructions branch). This is the same general mechanism the
 * "Target"/HeadMotion:Watch sensor already used elsewhere in this plugin's
 * role JSON relies on, just read explicitly here instead of implicitly by
 * the engine's own HeadMotion/BodyMotion handling.
 */
public class NoteNearbyThreatAction extends ActionBase {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    public NoteNearbyThreatAction(NoteNearbyThreatActionBuilder builder, BuilderSupport support) {
        super(builder);
    }

    @Override
    public boolean execute(Ref<EntityStore> ref, Role role, InfoProvider info,
                            double delta, Store<EntityStore> store) {
        super.execute(ref, role, info, delta, store);

        IPositionProvider target = info.getPositionProvider();
        if (target == null || !target.hasPosition()) {
            return false;
        }

        TransformComponent selfTransform = store.getComponent(ref, TransformComponent.getComponentType());
        if (selfTransform == null) {
            return false;
        }
        Vector3d selfPos = selfTransform.getPosition();

        double dx = target.getX() - selfPos.x;
        double dz = target.getZ() - selfPos.z;
        double distance = Math.sqrt(dx * dx + dz * dz);

        String npcId = role.getRoleName();
        ThreatMemory.record(npcId, distance);
        LOGGER.atFine().log(npcId + " noted a hostile ~" + Math.round(distance) + " blocks away");
        return true;
    }
}
