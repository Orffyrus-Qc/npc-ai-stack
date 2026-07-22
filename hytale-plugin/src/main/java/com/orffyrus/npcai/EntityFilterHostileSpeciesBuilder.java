package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.IEntityFilter;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderEntityFilterBase;

/**
 * Builder for the "IsHostileSpecies" entity filter - takes no config, same
 * zero-field shape as IsAwaitingReplySensorBuilder/IsCompanionSensorBuilder.
 * See EntityFilterHostileSpecies's javadoc for why this exists.
 */
public class EntityFilterHostileSpeciesBuilder extends BuilderEntityFilterBase {

    @Override
    public String getShortDescription() {
        return "True if the candidate NPC's own species has DefaultPlayerAttitude: Hostile.";
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
    public EntityFilterHostileSpeciesBuilder readConfig(JsonElement json) {
        return this;
    }

    @Override
    public IEntityFilter build(BuilderSupport support) {
        return new EntityFilterHostileSpecies(this, support);
    }
}
