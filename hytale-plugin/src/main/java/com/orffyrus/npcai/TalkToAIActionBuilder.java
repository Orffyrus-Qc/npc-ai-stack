package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderActionBase;
import com.hypixel.hytale.server.npc.instructions.Action;

/**
 * Parses the JSON "TalkToAI" action and builds a {@link TalkToAIAction}.
 * Registered by NpcAiPlugin via
 * NPCPlugin.get().registerCoreComponentType("TalkToAI", ...) - the same
 * pattern the built-in "OpenBarterShop" action uses, confirmed by
 * disassembling NPCShopPlugin.setup().
 *
 * Two optional JSON fields let a role customize its AI identity without
 * touching Java: "DisplayName" (chat name/tag; falls back to the role's
 * own name) and "AIRole" (occupation word fed into the orchestrator's
 * personality-baseline lookup and system prompt, e.g. "elder", "merchant";
 * falls back to "villager").
 */
public class TalkToAIActionBuilder extends BuilderActionBase {

    String displayName;
    String aiRole = "villager";

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
        //
        // Use the engine's own getString() config-helper (same one
        // BuilderSensorCanInteract.readConfig() uses for its ViewSector
        // float field) rather than reading the JsonElement by hand - it
        // also registers the key as a known/valid field for the framework's
        // own JSON schema validator, avoiding a harmless but noisy "Unknown
        // JSON attribute" boot warning that a raw Gson read triggers.
        getString(json, "DisplayName", v -> displayName = v, "", null,
                BuilderDescriptorState.Experimental,
                "Chat display name for this NPC (falls back to the role's own name if unset)", null);
        getString(json, "AIRole", v -> aiRole = v, "villager", null,
                BuilderDescriptorState.Experimental,
                "Occupation word fed into the AI orchestrator's personality baseline and prompt", null);
        return this;
    }

    @Override
    public Action build(BuilderSupport support) {
        return new TalkToAIAction(this, support);
    }
}
