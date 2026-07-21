package com.orffyrus.npcai;

import com.hypixel.hytale.builtin.locate.SpiralSearchUtil;
import com.hypixel.hytale.component.Ref;
import com.hypixel.hytale.component.Store;
import com.hypixel.hytale.logger.HytaleLogger;
import com.hypixel.hytale.server.core.modules.entity.component.TransformComponent;
import com.hypixel.hytale.server.core.universe.world.World;
import com.hypixel.hytale.server.core.universe.world.chunk.WorldChunk;
import com.hypixel.hytale.server.core.universe.world.storage.EntityStore;
import com.hypixel.hytale.server.core.universe.world.worldgen.IWorldGen;
import com.hypixel.hytale.server.worldgen.chunk.ChunkGenerator;
import com.hypixel.hytale.server.worldgen.zone.Zone;
import com.hypixel.hytale.server.worldgen.zone.ZoneGeneratorResult;
import org.joml.Vector3d;
import org.joml.Vector3i;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Answers "where's the nearest X" using Hytale's own real, shipped
 * mechanism for it - the same ChunkGenerator.getZonePatternProvider() +
 * SpiralSearchUtil.search() combo that backs the built-in "/locate zone"
 * command (confirmed by disassembling HytaleServer.jar v0.5.7). Hytale has
 * no built-in "town" concept - it's procedural terrain, not authored
 * settlements - so the closest real equivalent is a named world-gen Zone
 * (a temple, a unique landmark, a biome region). This describes those, not
 * a fictional "town" system that doesn't exist in the engine.
 *
 * Zone-at-a-coordinate is a pure procedural/noise function backed by an
 * in-memory cache (ChunkGenerator routes through ChunkGeneratorCache,
 * confirmed via disassembly) - no chunk loading or disk I/O involved - so
 * this is safe to call synchronously from the game thread with a bounded
 * search radius. Results are still cached per NPC (keyed by the stable
 * role-name identity from TalkToAIAction) since our custom NPCs never
 * move: the answer for a given NPC's fixed position never changes, so
 * there's no reason to ever recompute it.
 */
public final class NearbyLandmarks {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    /** Blocks to search outward before giving up on a given zone type. */
    private static final int SEARCH_RADIUS = 400;
    /** Cap on distinct discoverable zone types we bother searching for per NPC. */
    private static final int MAX_ZONE_TYPES = 6;
    /** Cap on landmarks actually mentioned once found. */
    private static final int MAX_RESULTS = 2;

    private static final Map<String, String> CACHE = new ConcurrentHashMap<>();
    /** Closest landmark's real world coordinate per NPC - what GuideState-driven
     * SeekLandmarkSensor actually walks the NPC toward when asked to guide. */
    private static final Map<String, Vector3i> CLOSEST_POSITION = new ConcurrentHashMap<>();
    /** Closest water (Ocean/Shallow_Ocean/Shore zone) coordinate per NPC - see
     * closestWaterPosition()'s javadoc. Cached separately from CLOSEST_POSITION
     * since it's a distinct, on-demand query (GuideState.Target.NEAREST_WATER),
     * not the passive general-flavor-text search describe() always runs. A
     * sentinel (not null) marks "searched, found nothing" so a NPC that has
     * no reachable water within SEARCH_RADIUS doesn't re-run the search on
     * every single tick while guiding.
     */
    private static final Map<String, Vector3i> CLOSEST_WATER_POSITION = new ConcurrentHashMap<>();
    private static final Vector3i NO_WATER_FOUND = new Vector3i(Integer.MIN_VALUE, Integer.MIN_VALUE, Integer.MIN_VALUE);

    private NearbyLandmarks() { }

    /**
     * Returns a short situation-string fragment describing nearby known
     * landmarks, or "" if nothing could be determined. Defensive by
     * design - never throws, since this is flavor context for the AI
     * prompt, not something that should ever break a conversation.
     */
    public static String describe(String npcId, Ref<EntityStore> npcRef, Store<EntityStore> store) {
        return CACHE.computeIfAbsent(npcId, id -> {
            try {
                return compute(id, npcRef, store);
            } catch (Exception e) {
                LOGGER.atWarning().log("NearbyLandmarks failed for " + id + ": " + e);
                return "";
            }
        });
    }

    /**
     * The closest landmark's real coordinate, or null if describe() hasn't
     * been called yet for this NPC (or found nothing). describe() always
     * populates this as a side effect - see compute().
     */
    public static Vector3i closestPosition(String npcId) {
        return CLOSEST_POSITION.get(npcId);
    }

    /**
     * Nearest Ocean/Shallow_Ocean/Shore zone - the closest real
     * approximation to "lake"/"river"/"water" Hytale's worldgen actually
     * tracks. Confirmed via the real shipped Zone.json assets (Assets.zip,
     * Server/World/Default/Zones/*) that there is no discrete lake/river
     * zone type at all - only Oceans/Shallow_Ocean/Shore/biome-region/
     * Temple/Tier zones exist, so a request to "guide to the lake" has
     * nothing literal to walk toward; this is the nearest thing that's
     * actually water. Unlike describe()'s general search, this does NOT
     * gate on discoveryConfig().display() - Ocean/Shore zones are basic
     * biome types, not curated "landmarks", but this is a deliberate,
     * specific query rather than passive flavor-text discovery, so they're
     * searched directly by name instead.
     */
    public static Vector3i closestWaterPosition(String npcId, Ref<EntityStore> npcRef, Store<EntityStore> store) {
        Vector3i result = CLOSEST_WATER_POSITION.computeIfAbsent(npcId, id -> {
            try {
                Vector3i pos = computeWater(npcRef, store);
                return pos != null ? pos : NO_WATER_FOUND;
            } catch (Exception e) {
                LOGGER.atWarning().log("NearbyLandmarks water search failed for " + id + ": " + e);
                return NO_WATER_FOUND;
            }
        });
        return result == NO_WATER_FOUND ? null : result;
    }

    private static Vector3i computeWater(Ref<EntityStore> npcRef, Store<EntityStore> store) {
        TransformComponent tc = store.getComponent(npcRef, TransformComponent.getComponentType());
        if (tc == null) return null;
        WorldChunk chunk = tc.getChunk();
        if (chunk == null) return null;
        World world = chunk.getWorld();
        if (world == null) return null;
        IWorldGen gen = world.getChunkStore().getGenerator();
        if (!(gen instanceof ChunkGenerator cg)) return null;

        Vector3d pos = tc.getPosition();
        int x = (int) pos.x, z = (int) pos.z;
        int seed = (int) world.getWorldConfig().getSeed();

        Vector3i best = null;
        long bestDistSq = Long.MAX_VALUE;
        for (Zone zone : cg.getZonePatternProvider().getZones()) {
            String zoneName = zone.name();
            if (!zoneName.contains("Ocean") && !zoneName.contains("Shore")) {
                continue;
            }
            Vector3i hit = SpiralSearchUtil.search(cg, seed, x, z, SEARCH_RADIUS,
                    zbr -> {
                        ZoneGeneratorResult zr = zbr.getZoneResult();
                        return zr != null && zr.getZone() != null && zoneName.equals(zr.getZone().name());
                    });
            if (hit != null) {
                long dx = hit.x - x, dz = hit.z - z;
                long distSq = dx * dx + dz * dz;
                if (distSq < bestDistSq) {
                    bestDistSq = distSq;
                    best = new Vector3i(hit.x, cg.getHeight(seed, hit.x, hit.z), hit.z);
                }
            }
        }
        return best;
    }

    private static String compute(String npcId, Ref<EntityStore> npcRef, Store<EntityStore> store) {
        TransformComponent tc = store.getComponent(npcRef, TransformComponent.getComponentType());
        if (tc == null) return "";
        WorldChunk chunk = tc.getChunk();
        if (chunk == null) return "";
        World world = chunk.getWorld();
        if (world == null) return "";
        IWorldGen gen = world.getChunkStore().getGenerator();
        if (!(gen instanceof ChunkGenerator cg)) return "";

        Vector3d pos = tc.getPosition();
        int x = (int) pos.x, z = (int) pos.z;
        // SpiralSearchUtil.search()'s real signature (confirmed via
        // disassembly of the real, shipped LocateZoneCommand/
        // AbstractLocateSubcommand) is (ChunkGenerator, int seed, int x,
        // int z, int radius, Predicate) - a pure 2D search, NOT (x, y, z,
        // radius) as it's easy to assume from the call shape alone. Passing
        // the NPC's real Y in the "seed" slot and Y/Z as the search origin
        // (an earlier version of this method's bug) silently searched near
        // world origin (0, ~playerY, ~playerZ) instead of near the NPC,
        // producing wildly wrong "nearest landmark" coordinates that still
        // happened to sound plausible as flavor text.
        int seed = (int) world.getWorldConfig().getSeed();

        Zone[] zones = cg.getZonePatternProvider().getZones();
        List<String> found = new ArrayList<>();
        Map<String, Vector3i> hitByDescription = new java.util.HashMap<>();
        int checked = 0;
        for (Zone zone : zones) {
            if (zone.discoveryConfig() == null || !zone.discoveryConfig().display()) {
                continue; // skip plain biome noise, keep only real discoverable landmarks
            }
            if (checked++ >= MAX_ZONE_TYPES) break;
            // Search by the internal zone id (Zone.name(), e.g. "Zone1_Spawn")
            // - that's what ZoneGeneratorResult.getZone().name() compares
            // against - but DISPLAY the discovery config's own "ZoneName"
            // (e.g. "Emerald_Wilds"), a proper in-game place name distinct
            // from the internal id. Confirmed via the real asset JSON
            // (Server/World/Default/Zones/*/Zone.json's "Discovery" block).
            String zoneName = zone.name();
            String displayName = zone.discoveryConfig().zone();
            if (displayName == null || displayName.isEmpty()) {
                displayName = zoneName;
            }
            Vector3i hit = SpiralSearchUtil.search(cg, seed, x, z, SEARCH_RADIUS,
                    zbr -> {
                        ZoneGeneratorResult zr = zbr.getZoneResult();
                        return zr != null && zr.getZone() != null && zoneName.equals(zr.getZone().name());
                    });
            if (hit != null) {
                int dx = hit.x - x, dz = hit.z - z;
                int distance = (int) Math.round(Math.hypot(dx, dz));
                String key = distance + "|" + prettify(displayName) + " to the " + compassDirection(dx, dz)
                        + " (~" + distance + " blocks)";
                found.add(key);
                // search() always returns y=0 (it's a pure 2D X/Z search) -
                // resolve a real ground height for the coordinate we'll
                // actually walk the NPC toward, via the same ChunkGenerator/
                // seed convention.
                hitByDescription.put(key, new Vector3i(hit.x, cg.getHeight(seed, hit.x, hit.z), hit.z));
            }
        }
        if (found.isEmpty()) return "";
        found.sort((a, b) -> Integer.compare(
                Integer.parseInt(a.substring(0, a.indexOf('|'))),
                Integer.parseInt(b.substring(0, b.indexOf('|')))));
        CLOSEST_POSITION.put(npcId, hitByDescription.get(found.get(0)));
        StringBuilder sb = new StringBuilder("Nearby landmarks you know of: ");
        for (int i = 0; i < Math.min(MAX_RESULTS, found.size()); i++) {
            if (i > 0) sb.append("; ");
            sb.append(found.get(i).substring(found.get(i).indexOf('|') + 1));
        }
        sb.append(".");
        return sb.toString();
    }

    private static String prettify(String zoneName) {
        // Strip a leading "ZoneN_" style prefix and turn underscores into
        // spaces - cosmetic only, these are internal worldgen asset ids.
        String cleaned = zoneName.replaceFirst("^Zone\\d+_", "").replace('_', ' ');
        return cleaned.isEmpty() ? zoneName : cleaned;
    }

    private static String compassDirection(int dx, int dz) {
        // Simple 8-way compass. Not confirmed to match Hytale's own in-client
        // compass convention (which world axis is "north" wasn't verified) -
        // this is flavor text for an NPC's spoken line, not a navigational HUD.
        double angle = Math.toDegrees(Math.atan2(dx, -dz));
        if (angle < 0) angle += 360;
        String[] dirs = {"north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"};
        int idx = (int) Math.round(angle / 45.0) % 8;
        return dirs[idx];
    }
}
