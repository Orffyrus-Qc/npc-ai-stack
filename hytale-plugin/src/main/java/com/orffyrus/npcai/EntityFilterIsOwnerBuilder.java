package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.IEntityFilter;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderEntityFilterBase;

/**
 * Builder for the "IsOwner" entity filter - takes no config, same
 * zero-field shape as EntityFilterHostileSpeciesBuilder.
 * See EntityFilterIsOwner's javadoc for why this exists.
 */
public class EntityFilterIsOwnerBuilder extends BuilderEntityFilterBase {

    @Override
    public String getShortDescription() {
        return "True if the candidate player is this NPC's tamed owner (CompanionState).";
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
    public EntityFilterIsOwnerBuilder readConfig(JsonElement json) {
        return this;
    }

    @Override
    public IEntityFilter build(BuilderSupport support) {
        return new EntityFilterIsOwner(this, support);
    }
}
