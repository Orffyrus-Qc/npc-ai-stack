package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderSensorBase;
import com.hypixel.hytale.server.npc.instructions.Sensor;

/**
 * Builder for the "IsManualMove" sensor - see {@link IsManualMoveSensor}.
 * Takes one required JSON field, "Kind": "FORWARD"|"JUMP"|"FORWARD_JUMP",
 * matching {@link ManualMoveState.Kind}.
 */
public class IsManualMoveSensorBuilder extends BuilderSensorBase {

    ManualMoveState.Kind kind = ManualMoveState.Kind.FORWARD;

    @Override
    public String getShortDescription() {
        return "True once per pending player manual-move directive of the configured Kind.";
    }

    @Override
    public String getLongDescription() {
        return getShortDescription() + " See github.com/Orffyrus-Qc/npc-ai-stack.";
    }

    @Override
    public BuilderDescriptorState getBuilderDescriptorState() {
        return BuilderDescriptorState.Experimental;
    }

    @Override
    public IsManualMoveSensorBuilder readConfig(JsonElement json) {
        getString(json, "Kind", v -> kind = ManualMoveState.Kind.valueOf(v), "FORWARD", null,
                BuilderDescriptorState.Experimental,
                "FORWARD | JUMP | FORWARD_JUMP - which manual-move directive this node reacts to", null);
        return this;
    }

    @Override
    public Sensor build(BuilderSupport support) {
        return new IsManualMoveSensor(this, support);
    }
}
