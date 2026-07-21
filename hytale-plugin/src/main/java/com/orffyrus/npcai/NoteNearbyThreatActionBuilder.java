package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderActionBase;
import com.hypixel.hytale.server.npc.instructions.Action;

/**
 * Builder for the "NoteNearbyThreat" action - takes no config, just records
 * whichever entity the enclosing Sensor matched (see Adventurer.json's
 * "Mob"+"Attitude" sensor) into ThreatMemory. Same "don't call
 * readCommonConfig()" pattern as TalkToAIActionBuilder - see that class's
 * comment for why (calling it broke NPC spawn validation the first time it
 * was tried, for the real, shipped BuilderActionOpenBarterShop reference
 * doesn't call it either).
 */
public class NoteNearbyThreatActionBuilder extends BuilderActionBase {

    @Override
    public String getShortDescription() {
        return "Notes the nearby hostile entity a preceding sensor matched, for the npc-ai-stack AI to react to.";
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
    public NoteNearbyThreatActionBuilder readConfig(JsonElement json) {
        return this;
    }

    @Override
    public Action build(BuilderSupport support) {
        return new NoteNearbyThreatAction(this, support);
    }
}
