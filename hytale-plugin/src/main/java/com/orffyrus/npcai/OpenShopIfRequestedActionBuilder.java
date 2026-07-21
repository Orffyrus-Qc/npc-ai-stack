package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderActionBase;
import com.hypixel.hytale.server.npc.instructions.Action;

/**
 * Builder for "OpenShopIfRequested" - takes one JSON field, "ShopId", naming
 * the real BarterShopAsset to open (e.g. "Klops_Merchant" - see
 * Server/BarterShops/*.json in the game's own Assets.zip for what exists).
 * Same "don't call readCommonConfig()" pattern as TalkToAIActionBuilder.
 */
public class OpenShopIfRequestedActionBuilder extends BuilderActionBase {

    String shopId = "";

    @Override
    public String getShortDescription() {
        return "Opens the real barter shop UI for the interacting player if npc-ai-stack's AI decided OPEN_SHOP.";
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
    public OpenShopIfRequestedActionBuilder readConfig(JsonElement json) {
        getString(json, "ShopId", v -> shopId = v, "", null,
                BuilderDescriptorState.Experimental,
                "The real BarterShopAsset id to open (see Server/BarterShops/*.json)", null);
        return this;
    }

    @Override
    public Action build(BuilderSupport support) {
        return new OpenShopIfRequestedAction(this, support);
    }
}
