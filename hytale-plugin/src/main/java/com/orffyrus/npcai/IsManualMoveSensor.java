package com.orffyrus.npcai;

import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.SensorBase;
import com.hypixel.hytale.server.npc.role.Role;
import com.hypixel.hytale.server.npc.sensorinfo.InfoProvider;

/**
 * Runtime "IsManualMove" sensor - true exactly once per pending
 * ManualMoveState directive of this sensor's configured Kind, then
 * self-consumes (see ManualMoveState.consume()'s javadoc for why this is a
 * Java-side one-shot rather than the JSON "Once" modifier - same reasoning
 * IsAwaitingReplySensor already established for an equivalent problem).
 *
 * One Kind per sensor instance/JSON node (Mori.json/Pest.json declare three
 * separate ManualMove Instructions nodes - FORWARD/JUMP/FORWARD_JUMP - each
 * with its own Kind-specific BodyMotion:Teleport params) rather than one
 * generic sensor exposing the kind as runtime data, since BodyMotion
 * parameters are fixed per JSON node, not computed from live sensor state.
 */
public class IsManualMoveSensor extends SensorBase {

    private final ManualMoveState.Kind kind;

    public IsManualMoveSensor(IsManualMoveSensorBuilder builder, BuilderSupport support) {
        super(builder);
        this.kind = builder.kind;
    }

    @Override
    public boolean matches(Ref<EntityStore> ref, Role role, double delta, Store<EntityStore> store) {
        if (!super.matches(ref, role, delta, store)) {
            return false;
        }
        String npcId = role.getRoleName();
        if (!ManualMoveState.isPending(npcId, kind)) {
            return false;
        }
        ManualMoveState.consume(npcId);
        return true;
    }

    @Override
    public InfoProvider getSensorInfo() {
        return null;
    }
}
