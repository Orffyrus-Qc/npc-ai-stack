package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderSensorBase;
import com.hypixel.hytale.server.npc.instructions.Sensor;

/**
 * Builder for the "IsAwaitingReply" sensor - takes no config, just checks
 * AwaitingReplyState.isAwaiting(role name). Same zero-field shape as
 * IsCompanionSensorBuilder.
 */
public class IsAwaitingReplySensorBuilder extends BuilderSensorBase {

    @Override
    public String getShortDescription() {
        return "True while npc-ai-stack is waiting on a real orchestrator round trip for this NPC.";
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
    public IsAwaitingReplySensorBuilder readConfig(JsonElement json) {
        return this;
    }

    @Override
    public Sensor build(BuilderSupport support) {
        return new IsAwaitingReplySensor(this, support);
    }
}
