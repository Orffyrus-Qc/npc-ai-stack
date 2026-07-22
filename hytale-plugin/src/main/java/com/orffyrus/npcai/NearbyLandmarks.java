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
import com.hypixel.hytale.server.core.universe.world.worldmap.markers.user.UserMapMarker;
import com.hypixel.hytale.server.core.universe.world.worldmap.markers.worldstore.WorldMarkersResource;
import com.hypixel.hytale.server.worldgen.chunk.ChunkGenerator;
import com.hypixel.hytale.server.worldgen.container.UniquePrefabContainer;
import com.hypixel.hytale.server.worldgen.zone.Zone;
import com.hypixel.hytale.server.worldgen.zone.ZoneGeneratorResult;
import org.joml.Vector3d;
import org.joml.Vector3i;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Answers "where's the nearest X" using Hytale's own real, shipped
 * mechanisms - ChunkGenerator.getZonePatternProvider() + SpiralSearchUtil
 * (the same combo behind the built-in "/locate zone" command),
 * ChunkGenerator.getUniquePrefabs() (behind "/locate prefab"), and the
 * real player map-marker system (WorldMarkersResource/UserMapMarkersStore
 * - the same store the game's own map UI reads/writes when a player drops
 * a named pin). All confirmed via disassembly of HytaleServer.jar v0.5.7.
 *
 * 2026-07-22 rewrite ("npc tell so many incoherent things... rewrite it
 * from the beginning"): a real live session's server log showed the model
 * inventing entirely fictional destinations ("Steve's Fort", "Salt Lake" -
 * not real places) with fake backstories and fake distances, then the
 * guide system silently failing to reach them (60s no-progress timeout,
 * with no visibility into why). Root cause: the model was free to invent
 * a destination description with no connection to what's actually real,
 * and a keyword extracted from that invention was searched for
 * hopefully - there was no way for what the NPC SAID and where it
 * actually WALKED to ever be guaranteed to match.
 *
 * Fix: one shared "known candidates" source of truth (Candidate records
 * below) - real nearby zones, real nearby structures, and the requesting
 * player's own real map markers - feeds BOTH the situation text shown to
 * the model (describe()) AND the guide-target resolution
 * (resolveGuideTarget()). The model is now told (see llm_client.py's
 * GUIDE_TARGET rule) to reference one of these exact real names instead
 * of inventing one; resolveGuideTarget() checks the exact same list it
 * was just given, so a real destination the model mentions can never fail
 * to resolve, and an invented one is at least the same known-fallback
 * path as before (closestNamedPosition's fuzzy world-gen search), not
 * silent, undiagnosable failure.
 */
public final class NearbyLandmarks {

    private static final HytaleLogger LOGGER = HytaleLogger.forEnclosingClass();

    /** Blocks to search outward before giving up on a given zone type. */
    private static final int SEARCH_RADIUS = 400;
    /** Water (Ocean/Shallow_Ocean/Shore) search radius - much larger than
     * SEARCH_RADIUS because coastline is far sparser than biome-region
     * zones. Live testing showed 400 blocks reliably found nothing at all
     * ("couldn't find a NEAREST_WATER... giving up" on every single guide
     * request) for a player well inland - confirmed the search itself
     * worked correctly (see the seed/x/z fix), it just never had a chance
     * to reach real coastline at that radius. */
    private static final int WATER_SEARCH_RADIUS = 3000;
    /** Cap on distinct discoverable zone types we bother searching for per NPC. */
    private static final int MAX_ZONE_TYPES = 6;
    /** Cap on candidates actually mentioned per section in situation text. */
    private static final int MAX_RESULTS = 3;
    /** Radius within which a unique prefab counts as "known" for the
     * candidate list (no keyword to narrow the search here, unlike the
     * closestNamedPosition() fallback below, so an explicit cutoff keeps
     * this to genuinely nearby structures instead of every prefab in the
     * generated world). Same value as NAMED_SEARCH_RADIUS below - same
     * "sparser than biome zones" reasoning. */
    private static final int PREFAB_RADIUS = 2000;

    /** Closest water (Ocean/Shallow_Ocean/Shore zone) coordinate per NPC - see
     * closestWaterPosition()'s javadoc. A sentinel (not null) marks
     * "searched, found nothing" so a NPC that has no reachable water
     * within WATER_SEARCH_RADIUS doesn't re-run the search every tick. */
    private static final Map<String, Vector3i> CLOSEST_WATER_POSITION = new ConcurrentHashMap<>();
    private static final Vector3i NO_WATER_FOUND = new Vector3i(Integer.MIN_VALUE, Integer.MIN_VALUE, Integer.MIN_VALUE);

    /** Named/keyword search radius for the closestNamedPosition() fallback
     * below - wider than SEARCH_RADIUS since a specific keyword is sparser
     * than "any discoverable zone at all". */
    private static final int NAMED_SEARCH_RADIUS = 2000;
    private static final Map<String, Vector3i> CLOSEST_NAMED_POSITION = new ConcurrentHashMap<>();
    private static final Vector3i NO_NAMED_FOUND = new Vector3i(Integer.MIN_VALUE, Integer.MIN_VALUE, Integer.MIN_VALUE);

    /** Real, static (zone + prefab) candidates per NPC - cached forever,
     * same reasoning as before: a fixed NPC's position never changes, so
     * neither does the answer. Player map markers are NOT part of this
     * cache (see computeMarkerCandidates()) - they can change at any time. */
    private static final Map<String, List<Candidate>> STATIC_CANDIDATES = new ConcurrentHashMap<>();

    private NearbyLandmarks() { }

    /**
     * A real, nameable place near this NPC - either world-gen (a
     * discoverable zone or a unique prefab) or the requesting player's own
     * placed map marker. The SAME list of these feeds both the situation
     * text shown to the model and resolveGuideTarget()'s matching, so
     * what the NPC says and where it walks can never diverge.
     */
    public record Candidate(String matchName, Vector3i position, int distance, String describeText) { }

    /**
     * Situation-string fragment describing real nearby landmarks and (if
     * playerId is known) the player's own real map markers, or "" if
     * nothing could be determined. Defensive by design - never throws,
     * since this is flavor context for the AI prompt, not something that
     * should ever break a conversation. playerId may be null (e.g. no
     * player context available) - the marker section is simply omitted.
     */
    public static String describe(String npcId, UUID playerId, Ref<EntityStore> npcRef, Store<EntityStore> store) {
        try {
            List<Candidate> statics = staticCandidates(npcId, npcRef, store);
            List<Candidate> markers = playerId != null ? safeMarkerCandidates(npcRef, store, playerId) : List.of();
            StringBuilder sb = new StringBuilder();
            appendSection(sb, "Nearby landmarks you know of", statics);
            appendSection(sb, "Your own marked places", markers);
            return sb.toString().trim();
        } catch (Exception e) {
            LOGGER.atWarning().log("NearbyLandmarks describe failed for " + npcId + ": " + e);
            return "";
        }
    }

    private static void appendSection(StringBuilder sb, String label, List<Candidate> candidates) {
        if (candidates.isEmpty()) return;
        List<Candidate> sorted = candidates.stream()
                .sorted(Comparator.comparingInt(Candidate::distance))
                .limit(MAX_RESULTS)
                .toList();
        if (sb.length() > 0) sb.append(" ");
        sb.append(label).append(": ");
        for (int i = 0; i < sorted.size(); i++) {
            if (i > 0) sb.append("; ");
            sb.append(sorted.get(i).describeText());
        }
        sb.append(".");
    }

    /**
     * The single closest known static (zone/prefab) candidate's real
     * coordinate, or null if describe() hasn't been called yet for this
     * NPC (or found nothing) - GuideState.Target.NEAREST_LANDMARK and the
     * final give-up fallback both read this. describe() always populates
     * the underlying cache as a side effect (via staticCandidates()).
     */
    public static Vector3i closestPosition(String npcId) {
        List<Candidate> statics = STATIC_CANDIDATES.get(npcId);
        if (statics == null || statics.isEmpty()) return null;
        return statics.stream().min(Comparator.comparingInt(Candidate::distance)).map(Candidate::position).orElse(null);
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
     * searched directly by name instead. Untouched by the 2026-07-22
     * rewrite - this is a direct, deterministic query, never keyword-
     * matched against anything the model might invent.
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
            Vector3i hit = SpiralSearchUtil.search(cg, seed, x, z, WATER_SEARCH_RADIUS,
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

    /**
     * Resolves a free-text keyword the LLM extracted (see llm_client.py's
     * GUIDE_TARGET tag) against the SAME real candidate list describe()
     * just showed the model - a real nearby zone/prefab name, or (checked
     * first, since the player's own choice is more precise) one of the
     * requesting player's own real map markers. Only if nothing in that
     * curated list matches does this fall back to the broader, looser
     * closestNamedPosition() world-gen search below (today's previous
     * behavior) - handles the case where the model still invents
     * something ungrounded, as a degraded-but-not-silent last resort
     * rather than the common path.
     */
    public static Vector3i resolveGuideTarget(String npcId, String keyword, UUID playerId,
                                               Ref<EntityStore> npcRef, Store<EntityStore> store) {
        String needle = keyword.toLowerCase();
        if (playerId != null) {
            Candidate markerHit = closestMatch(safeMarkerCandidates(npcRef, store, playerId), needle);
            if (markerHit != null) return markerHit.position();
        }
        Candidate staticHit = closestMatch(staticCandidates(npcId, npcRef, store), needle);
        if (staticHit != null) return staticHit.position();
        return closestNamedPosition(npcId, keyword, npcRef, store);
    }

    private static Candidate closestMatch(List<Candidate> candidates, String needle) {
        Candidate best = null;
        int bestDist = Integer.MAX_VALUE;
        for (Candidate c : candidates) {
            if (c.matchName().toLowerCase().contains(needle) && c.distance() < bestDist) {
                bestDist = c.distance();
                best = c;
            }
        }
        return best;
    }

    private static List<Candidate> staticCandidates(String npcId, Ref<EntityStore> npcRef, Store<EntityStore> store) {
        return STATIC_CANDIDATES.computeIfAbsent(npcId, id -> {
            try {
                return computeStaticCandidates(npcRef, store);
            } catch (Exception e) {
                LOGGER.atWarning().log("NearbyLandmarks static candidate search failed for " + id + ": " + e);
                return List.of();
            }
        });
    }

    private static List<Candidate> safeMarkerCandidates(Ref<EntityStore> npcRef, Store<EntityStore> store, UUID playerId) {
        try {
            return computeMarkerCandidates(npcRef, store, playerId);
        } catch (Exception e) {
            LOGGER.atWarning().log("NearbyLandmarks marker candidate search failed for player " + playerId + ": " + e);
            return List.of();
        }
    }

    private static List<Candidate> computeStaticCandidates(Ref<EntityStore> npcRef, Store<EntityStore> store) {
        TransformComponent tc = store.getComponent(npcRef, TransformComponent.getComponentType());
        if (tc == null) return List.of();
        WorldChunk chunk = tc.getChunk();
        if (chunk == null) return List.of();
        World world = chunk.getWorld();
        if (world == null) return List.of();
        IWorldGen gen = world.getChunkStore().getGenerator();
        if (!(gen instanceof ChunkGenerator cg)) return List.of();

        Vector3d pos = tc.getPosition();
        int x = (int) pos.x, z = (int) pos.z;
        // SpiralSearchUtil.search()'s real signature (confirmed via
        // disassembly of the real, shipped LocateZoneCommand/
        // AbstractLocateSubcommand) is (ChunkGenerator, int seed, int x,
        // int z, int radius, Predicate) - a pure 2D search, NOT (x, y, z,
        // radius) as it's easy to assume from the call shape alone.
        int seed = (int) world.getWorldConfig().getSeed();

        List<Candidate> result = new ArrayList<>();

        int checked = 0;
        for (Zone zone : cg.getZonePatternProvider().getZones()) {
            if (zone.discoveryConfig() == null || !zone.discoveryConfig().display()) {
                continue; // skip plain biome noise, keep only real discoverable landmarks
            }
            if (checked++ >= MAX_ZONE_TYPES) break;
            // Search by the internal zone id (Zone.name(), e.g. "Zone1_Spawn")
            // - that's what ZoneGeneratorResult.getZone().name() compares
            // against - but DISPLAY the discovery config's own "ZoneName"
            // (e.g. "Emerald_Wilds"), a proper in-game place name distinct
            // from the internal id.
            String zoneName = zone.name();
            String displayName = zone.discoveryConfig().zone();
            if (displayName == null || displayName.isEmpty()) {
                displayName = zoneName;
            }
            String prettyName = prettify(displayName);
            Vector3i hit = SpiralSearchUtil.search(cg, seed, x, z, SEARCH_RADIUS,
                    zbr -> {
                        ZoneGeneratorResult zr = zbr.getZoneResult();
                        return zr != null && zr.getZone() != null && zoneName.equals(zr.getZone().name());
                    });
            if (hit != null) {
                int dx = hit.x - x, dz = hit.z - z;
                int distance = (int) Math.round(Math.hypot(dx, dz));
                Vector3i realPos = new Vector3i(hit.x, cg.getHeight(seed, hit.x, hit.z), hit.z);
                String describeText = prettyName + " to the " + compassDirection(dx, dz) + " (~" + distance + " blocks)";
                result.add(new Candidate(prettyName, realPos, distance, describeText));
            }
        }

        UniquePrefabContainer.UniquePrefabEntry[] prefabs = cg.getUniquePrefabs(seed);
        if (prefabs != null) {
            for (UniquePrefabContainer.UniquePrefabEntry entry : prefabs) {
                Vector3i epos = entry.getPosition();
                int dx = epos.x - x, dz = epos.z - z;
                int distance = (int) Math.round(Math.hypot(dx, dz));
                if (distance > PREFAB_RADIUS) continue;
                String name = prettify(entry.getName());
                String describeText = name + " to the " + compassDirection(dx, dz) + " (~" + distance + " blocks)";
                result.add(new Candidate(name, epos, distance, describeText));
            }
        }

        return result;
    }

    /**
     * The requesting PLAYER'S OWN real, native Hytale map markers - a
     * player-placed, player-named pin dropped via the game's own map UI
     * (client sends a real `CreateUserMarker` packet, handled by the real,
     * shipped `WorldMapManager.handleUserCreateMarker()`; confirmed via
     * disassembly). This plugin only reads what the player placed
     * themselves here - see createGuideMarker() below for the NPC's own
     * marker-creation (a distinct, separate concern).
     *
     * Access path: `WorldMarkersResource` implements the real
     * `UserMapMarkersStore` interface and is a per-world ECS `Resource`
     * (`Resource<ChunkStore>`) - fetched via `world.getChunkStore().
     * getStore().getResource(...)`, the same `ChunkStore`/`Store` pair
     * computeStaticCandidates()/computeWater() already use for
     * `getGenerator()`, just asking for a different resource type off the
     * same store. `getUserMapMarkers(UUID)` returns only that specific
     * player's own markers - never another player's.
     *
     * Deliberately NOT cached like the static zone/prefab candidates - a
     * player can add, rename, or remove their own markers at any time
     * (unlike world-gen, which never changes for a fixed NPC position),
     * and this is a cheap in-memory lookup + linear scan of one player's
     * own markers (never a spiral search over generated terrain), so
     * there's no performance reason to risk serving a stale answer.
     */
    private static List<Candidate> computeMarkerCandidates(Ref<EntityStore> npcRef, Store<EntityStore> store, UUID playerId) {
        TransformComponent tc = store.getComponent(npcRef, TransformComponent.getComponentType());
        if (tc == null) return List.of();
        WorldChunk chunk = tc.getChunk();
        if (chunk == null) return List.of();
        World world = chunk.getWorld();
        if (world == null) return List.of();

        WorldMarkersResource markers = world.getChunkStore().getStore().getResource(WorldMarkersResource.getResourceType());
        if (markers == null) return List.of();
        Collection<? extends UserMapMarker> playerMarkers = markers.getUserMapMarkers(playerId);
        if (playerMarkers == null || playerMarkers.isEmpty()) return List.of();

        IWorldGen gen = world.getChunkStore().getGenerator();
        if (!(gen instanceof ChunkGenerator cg)) return List.of();
        int seed = (int) world.getWorldConfig().getSeed();

        Vector3d pos = tc.getPosition();
        int x = (int) pos.x, z = (int) pos.z;

        List<Candidate> result = new ArrayList<>();
        for (UserMapMarker marker : playerMarkers) {
            String name = marker.getName();
            if (name == null || name.isBlank()) continue;
            int mx = (int) marker.getX(), mz = (int) marker.getZ();
            int dx = mx - x, dz = mz - z;
            int distance = (int) Math.round(Math.hypot(dx, dz));
            Vector3i realPos = new Vector3i(mx, cg.getHeight(seed, mx, mz), mz);
            String describeText = "your marker \"" + name + "\" to the " + compassDirection(dx, dz)
                    + " (~" + distance + " blocks)";
            result.add(new Candidate(name, realPos, distance, describeText));
        }
        return result;
    }

    /**
     * Fuzzy fallback world-gen search (zones + unique prefabs, substring-
     * matched against a free-text keyword) - the pre-2026-07-22 mechanism,
     * kept as resolveGuideTarget()'s last resort for a keyword that
     * doesn't match anything in the curated known-candidates list (e.g.
     * the model still invented something ungrounded). Known limitation,
     * stated plainly: this only ever finds ZONES and STRUCTURES, not
     * specific resources (e.g. "find me some iron") - there is no
     * equivalent lightweight "nearest X ore" search mechanism in this
     * engine to hook into.
     */
    public static Vector3i closestNamedPosition(String npcId, String keyword,
                                                 Ref<EntityStore> npcRef, Store<EntityStore> store) {
        String cacheKey = npcId + "|" + keyword.toLowerCase();
        Vector3i result = CLOSEST_NAMED_POSITION.computeIfAbsent(cacheKey, k -> {
            try {
                Vector3i pos = computeNamed(npcRef, store, keyword);
                return pos != null ? pos : NO_NAMED_FOUND;
            } catch (Exception e) {
                LOGGER.atWarning().log("NearbyLandmarks named search failed for " + npcId
                        + " (" + keyword + "): " + e);
                return NO_NAMED_FOUND;
            }
        });
        return result == NO_NAMED_FOUND ? null : result;
    }

    private static Vector3i computeNamed(Ref<EntityStore> npcRef, Store<EntityStore> store, String keyword) {
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
        String needle = keyword.toLowerCase();

        Vector3i best = null;
        long bestDistSq = Long.MAX_VALUE;

        for (Zone zone : cg.getZonePatternProvider().getZones()) {
            String zoneName = zone.name();
            String displayName = zone.discoveryConfig() != null ? zone.discoveryConfig().zone() : null;
            boolean matches = zoneName.toLowerCase().contains(needle)
                    || (displayName != null && displayName.toLowerCase().contains(needle));
            if (!matches) continue;
            Vector3i hit = SpiralSearchUtil.search(cg, seed, x, z, NAMED_SEARCH_RADIUS,
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

        UniquePrefabContainer.UniquePrefabEntry[] prefabs = cg.getUniquePrefabs(seed);
        if (prefabs != null) {
            for (UniquePrefabContainer.UniquePrefabEntry entry : prefabs) {
                if (!entry.getName().toLowerCase().contains(needle)) continue;
                Vector3i epos = entry.getPosition();
                long dx = epos.x - x, dz = epos.z - z;
                long distSq = dx * dx + dz * dz;
                if (distSq < bestDistSq) {
                    bestDistSq = distSq;
                    best = epos;
                }
            }
        }

        return best;
    }

    /**
     * Drops a real, native map marker at `target`, owned by `playerId` (so
     * it shows up alongside their own markers), created via the same real
     * `UserMapMarkersStore.addUserMapMarker()` API the game's own
     * `WorldMapManager.handleUserCreateMarker()` uses when a player places
     * one themselves (confirmed via disassembly - construct a
     * `UserMapMarker`, `setId`/`setPosition`/`setName`,
     * `withCreatedByUuid`/`withCreatedByName`, then `addUserMapMarker`).
     * Lets the player literally see where they're being led on their own
     * map instead of only inferring it from chat text. Returns the new
     * marker's id (for later removeGuideMarker()), or null if anything
     * failed - callers treat that as "no marker was created," not an
     * error worth surfacing to the player.
     *
     * Whether a server-added marker like this reaches the client
     * immediately (a push) or only the next time they open their map UI
     * isn't confirmed from disassembly alone - needs live verification.
     */
    public static String createGuideMarker(Ref<EntityStore> npcRef, Store<EntityStore> store,
                                            UUID playerId, String label, Vector3i target) {
        try {
            TransformComponent tc = store.getComponent(npcRef, TransformComponent.getComponentType());
            if (tc == null) return null;
            WorldChunk chunk = tc.getChunk();
            if (chunk == null) return null;
            World world = chunk.getWorld();
            if (world == null) return null;
            WorldMarkersResource markers = world.getChunkStore().getStore().getResource(WorldMarkersResource.getResourceType());
            if (markers == null) return null;

            UserMapMarker marker = new UserMapMarker();
            String id = "npcai-guide-" + UUID.randomUUID();
            marker.setId(id);
            marker.setPosition(target.x, target.z);
            marker.setName(label);
            marker.withCreatedByUuid(playerId);
            marker.withCreatedByName("Adventurer");
            markers.addUserMapMarker(marker);
            return id;
        } catch (Exception e) {
            LOGGER.atWarning().log("NearbyLandmarks couldn't create a guide marker: " + e);
            return null;
        }
    }

    /** Removes a marker previously created by createGuideMarker() - no-op
     * if markerId is null/blank (nothing was ever created) or removal
     * fails for any reason; a guide marker failing to clean up is cosmetic
     * map clutter, never worth surfacing as an error. */
    public static void removeGuideMarker(Ref<EntityStore> npcRef, Store<EntityStore> store, String markerId) {
        if (markerId == null || markerId.isEmpty()) return;
        try {
            TransformComponent tc = store.getComponent(npcRef, TransformComponent.getComponentType());
            if (tc == null) return;
            WorldChunk chunk = tc.getChunk();
            if (chunk == null) return;
            World world = chunk.getWorld();
            if (world == null) return;
            WorldMarkersResource markers = world.getChunkStore().getStore().getResource(WorldMarkersResource.getResourceType());
            if (markers == null) return;
            markers.removeUserMapMarker(markerId);
        } catch (Exception e) {
            LOGGER.atWarning().log("NearbyLandmarks couldn't remove guide marker " + markerId + ": " + e);
        }
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
