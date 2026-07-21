package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderActionBase;
import com.hypixel.hytale.server.npc.instructions.Action;

/**
 * Parses the JSON "TalkToAI" action (no config beyond the common action
 * fields) and builds a {@link TalkToAIAction}. Registered by NpcAiPlugin via
 * NPCPlugin.get().registerCoreComponentType("TalkToAI", ...) - the same
 * pattern the built-in "OpenBarterShop" action uses, confirmed by
 * disassembling NPCShopPlugin.setup().
 */
public class TalkToAIActionBuilder extends BuilderActionBase {

    @Override
    public String getShortDescription() {
        return "Sends this NPC interaction to the npc-ai-stack AI orchestrator for a dialogue reply.";
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
    public TalkToAIActionBuilder readConfig(JsonElement json) {
        // Deliberately NOT calling readCommonConfig(json) here. The real,
        // shipped BuilderActionOpenBarterShop.readConfig() (disassembled
        // from HytaleServer.jar) does not call it either - only its own
        // shop-specific fields. Calling it ourselves produced mysterious
        // "FAIL: ... Once" / "FAIL: ... Enabled" validation errors at boot
        // that made the resulting NPC role unspawnable ("Can't find a
        // matching role builder"). The framework evidently invokes
        // readCommonConfig() separately as part of the builder pipeline.
        return this;
    }

    @Override
    public Action build(BuilderSupport support) {
        return new TalkToAIAction(this, support);
    }
}
