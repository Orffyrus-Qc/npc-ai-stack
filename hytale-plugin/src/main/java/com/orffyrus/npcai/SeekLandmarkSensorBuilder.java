package com.orffyrus.npcai;

import com.google.gson.JsonElement;
import com.hypixel.hytale.server.npc.asset.builder.BuilderDescriptorState;
import com.hypixel.hytale.server.npc.asset.builder.BuilderSupport;
import com.hypixel.hytale.server.npc.asset.builder.Feature;
import com.hypixel.hytale.server.npc.corecomponents.builders.BuilderSensorBase;
import com.hypixel.hytale.server.npc.instructions.Sensor;

/**
 * Builder for the "SeekLandmark" sensor - takes no config, just checks
 * GuideState.isGuiding(role name) and, if true, supplies
 * NearbyLandmarks.closestPosition(role name) as this sensor's target
 * position (via a plain PositionProvider - confirmed via disassembly to
 * itself implement InfoProvider, so it's usable directly as
 * Sensor.getSensorInfo()'s return value). A paired "Seek" BodyMotion in
 * the same Instructions node then walks the NPC toward that coordinate -
 * see SeekLandmarkSensor's javadoc for the full mechanism.
 */
public class SeekLandmarkSensorBuilder extends BuilderSensorBase {

    @Override
    public String getShortDescription() {
        return "True while npc-ai-stack is actively guiding this NPC to a landmark - supplies the target coordinate.";
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
    public SeekLandmarkSensorBuilder readConfig(JsonElement json) {
        // Declares to the engine's load-time validator that this Sensor can
        // supply a vector position (via getSensorInfo() -> PositionProvider),
        // satisfying the paired "Seek" BodyMotion's RequiresOneOfFeatures
        // check (Feature.Position) - confirmed via disassembly of the real
        // BuilderSensorReadPosition, which calls this same provideFeature()
        // in its own readConfig() for the same reason.
        provideFeature(Feature.Position);
        return this;
    }

    @Override
    public Sensor build(BuilderSupport support) {
        return new SeekLandmarkSensor(this, support);
    }
}
