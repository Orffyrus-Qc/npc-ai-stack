package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderActionBase;
import com.hypixel.hytale.server.npc.instructions.Action;

/**
 * Builder for the "NoteAttackedByPlayer" action - see
 * {@link NoteAttackedByPlayerAction}. Takes one optional JSON field,
 * "AIRole", same meaning and default as TalkToAIActionBuilder's - kept
 * separately configurable here (not read from the sibling TalkToAI action
 * automatically) since a role's Instructions tree only guarantees the two
 * appear together by convention, not by construction.
 */
public class NoteAttackedByPlayerActionBuilder extends BuilderActionBase {

    String aiRole = "villager";

    @Override
    public String getShortDescription() {
        return "Reports this NPC being attacked by a player to the npc-ai-stack AI orchestrator as an outcome.";
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
    public NoteAttackedByPlayerActionBuilder readConfig(JsonElement json) {
        // Same "don't call readCommonConfig()" convention as
        // TalkToAIActionBuilder/NoteNearbyThreatActionBuilder - see the
        // former's comment for why.
        getString(json, "AIRole", v -> aiRole = v, "villager", null,
                BuilderDescriptorState.Experimental,
                "Occupation word fed into the AI orchestrator's personality baseline and prompt", null);
        return this;
    }

    @Override
    public Action build(BuilderSupport support) {
        return new NoteAttackedByPlayerAction(this, support);
    }
}
