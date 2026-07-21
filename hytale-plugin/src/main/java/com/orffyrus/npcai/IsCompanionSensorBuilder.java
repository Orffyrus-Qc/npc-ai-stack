package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderSensorBase;
import com.hypixel.hytale.server.npc.instructions.Sensor;

/**
 * Builder for the "IsCompanion" sensor - takes no config, just checks
 * CompanionState.isCompanion(role name). Same shape as the built-in
 * BuilderSensorCanInteract/BuilderSensorEntity (both extend BuilderSensorBase
 * too), just with zero fields of its own.
 */
public class IsCompanionSensorBuilder extends BuilderSensorBase {

    @Override
    public String getShortDescription() {
        return "True if npc-ai-stack has marked this NPC as a tamed companion.";
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
    public IsCompanionSensorBuilder readConfig(JsonElement json) {
        return this;
    }

    @Override
    public Sensor build(BuilderSupport support) {
        return new IsCompanionSensor(this, support);
    }
}
