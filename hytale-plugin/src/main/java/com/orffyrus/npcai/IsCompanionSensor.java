package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.SensorBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

/**
 * Runtime "IsCompanion" sensor - true once CompanionState has this NPC's
 * role name marked (see CompanionState's javadoc for how/when that
 * happens). Used in an "And" combination with the built-in "Player" sensor
 * (see Adventurer.json etc.'s Watching state) so a tamed companion's
 * BodyMotion becomes "Seek" (follow) instead of "Nothing" (stand still)
 * once a player is nearby - the actual movement primitive is real,
 * shipped engine functionality (confirmed via disassembly and via the
 * real Template_Livestock.json's own "FollowItem" behavior, which uses
 * the same Seek/MaintainDistance BodyMotion types), not custom pathfinding
 * code.
 *
 * super.matches() is called first, matching the pattern the real, shipped
 * SensorCanInteract uses - SensorBase.matches() handles the "once"/enabled
 * bookkeeping common to all sensors built this way.
 */
public class IsCompanionSensor extends SensorBase {

    public IsCompanionSensor(IsCompanionSensorBuilder builder, BuilderSupport support) {
        super(builder);
    }

    @Override
    public boolean matches(Ref<EntityStore> ref, Role role, double delta, Store<EntityStore> store) {
        if (!super.matches(ref, role, delta, store)) {
            return false;
        }
        return CompanionState.isCompanion(role.getRoleName());
    }

    @Override
    public InfoProvider getSensorInfo() {
        return null;
    }
}
