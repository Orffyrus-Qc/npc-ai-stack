package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderActionBase;
import com.hypixel.hytale.server.npc.instructions.Action;

/**
 * Builder for the "NoteNearbyObjects" action - takes no config, just
 * scans real nearby blocks into NearbyObjects. Same "don't call
 * readCommonConfig()" pattern as the other custom actions in this
 * plugin - see NoteNearbyThreatActionBuilder's comment for why.
 */
public class NoteNearbyObjectsActionBuilder extends BuilderActionBase {

    @Override
    public String getShortDescription() {
        return "Scans real nearby blocks (plants/flowers) for the npc-ai-stack AI to describe.";
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
    public NoteNearbyObjectsActionBuilder readConfig(JsonElement json) {
        return this;
    }

    @Override
    public Action build(BuilderSupport support) {
        return new NoteNearbyObjectsAction(this, support);
    }
}
