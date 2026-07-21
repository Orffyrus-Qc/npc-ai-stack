package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.SensorBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

/**
 * Runtime "IsAwaitingReply" sensor - true while AwaitingReplyState has this
 * NPC's role name marked (see that class's javadoc). Combined with "Once":
 * true in the role JSON so the "thinking" particle fires once per
 * request instead of re-spawning every tick for the whole wait - same
 * "once"/enabled bookkeeping every sensor built this way gets from
 * SensorBase.matches(), see IsCompanionSensor's comment.
 */
public class IsAwaitingReplySensor extends SensorBase {

    public IsAwaitingReplySensor(IsAwaitingReplySensorBuilder builder, BuilderSupport support) {
        super(builder);
    }

    @Override
    public boolean matches(Ref<EntityStore> ref, Role role, double delta, Store<EntityStore> store) {
        if (!super.matches(ref, role, delta, store)) {
            return false;
        }
        return AwaitingReplyState.isAwaiting(role.getRoleName());
    }

    @Override
    public InfoProvider getSensorInfo() {
        return null;
    }
}
