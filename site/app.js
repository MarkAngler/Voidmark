/* =======================================================================
   Voidmark — Tibia map explorer frontend
   ======================================================================= */

(() => {
    "use strict";

    const DATA_URL = "data/";
    const TILES_URL = "tiles/";

    const state = {
        meta: null,
        monsters: null,
        spawns: null,
        map: null,
        floor: 7,
        floorLayers: {},   // z -> L.tileLayer
        markerLayer: null,
        heatLayer: null,
        selected: null,    // selected monster key
        sortBy: "name",
        markerMode: true,
        heatMode: false,
        showOtherZ: true,
        routeLayer: null,
        routeDest: null,   // {x, y, z}
        routeStart: null,  // {x, y, z}
        routePath: null,   // [{x, y, z}, ...]
        routeMode: false,  // waiting for start-point click
        huntingAreas: null,
        huntingMode: false,
        huntingLayer: null,
        huntingResults: null,
        huntingSort: "exp",
    };

    const FLOOR_LABELS = {
        0: "sky 7", 1: "sky 6", 2: "sky 5", 3: "sky 4",
        4: "sky 3", 5: "sky 2", 6: "sky 1", 7: "surface",
        8: "underground 1", 9: "underground 2", 10: "underground 3",
        11: "underground 4", 12: "underground 5", 13: "underground 6",
        14: "underground 7", 15: "underground 8",
    };

    /* ---------- bootstrap ---------- */
    async function boot() {
        try {
            const [meta, monsters, spawns, huntingAreas] = await Promise.all([
                fetch(DATA_URL + "world_meta.json").then(r => r.json()),
                fetch(DATA_URL + "monsters.json").then(r => r.json()),
                fetch(DATA_URL + "spawns.json").then(r => r.json()),
                fetch(DATA_URL + "hunting_areas.json").then(r => r.json()).catch(() => null),
            ]);
            state.meta = meta;
            state.monsters = monsters;
            state.spawns = spawns;
            state.huntingAreas = huntingAreas;
            initMap();
            initUI();
            restoreFromHash();
            document.getElementById("loading").classList.add("hide");
        } catch (err) {
            document.getElementById("loading").textContent =
                "Failed to load data: " + err.message;
            console.error(err);
        }
    }

    /* ---------- coord helpers ---------- */
    // Convert Tibia world (x, y) to pixel coords in the rendered image.
    function worldToPixel(x, y) {
        return [x - state.meta.origin.x, y - state.meta.origin.y];
    }
    function pixelToWorld(px, py) {
        return [px + state.meta.origin.x, py + state.meta.origin.y];
    }
    // Convert world (x, y) to Leaflet LatLng via the map's CRS at native zoom.
    function worldToLatLng(x, y) {
        const [px, py] = worldToPixel(x, y);
        return state.map.unproject([px, py], state.meta.max_native_zoom);
    }

    /* ---------- map init ---------- */
    function initMap() {
        const meta = state.meta;
        const W = meta.size.w;
        const H = meta.size.h;
        const maxZoom = meta.max_native_zoom;

        const map = L.map("map", {
            crs: L.CRS.Simple,
            minZoom: 0,
            maxZoom: maxZoom,
            zoomSnap: 0.5,
            zoomDelta: 1,
            attributionControl: true,
            zoomControl: true,
            preferCanvas: true,
        });
        state.map = map;

        const sw = map.unproject([0, H], maxZoom);
        const ne = map.unproject([W, 0], maxZoom);
        const bounds = L.latLngBounds(sw, ne);
        map.setMaxBounds(bounds.pad(0.25));
        map.fitBounds(bounds);

        // Pre-create one tile layer per floor. Only the active one is
        // attached to the map at a time.
        for (let z = 0; z <= 15; z++) {
            const layer = L.tileLayer(`${TILES_URL}z${z}/{z}/{x}_{y}.png`, {
                tileSize: meta.tile_size,
                minZoom: 0,
                maxZoom: maxZoom,
                maxNativeZoom: maxZoom,
                noWrap: true,
                bounds: bounds,
                errorTileUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORk5CYII=",
                attribution: "Voidmark · Tibia 7.72",
            });
            state.floorLayers[z] = layer;
        }

        state.markerLayer = L.featureGroup().addTo(map);
        state.routeLayer = L.featureGroup().addTo(map);

        // Coord display on mousemove.
        map.on("mousemove", (e) => {
            const p = map.project(e.latlng, maxZoom);
            const wx = Math.round(p.x + meta.origin.x);
            const wy = Math.round(p.y + meta.origin.y);
            document.getElementById("coord-xy").textContent = `x:${wx}  y:${wy}`;
            document.getElementById("coord-z").textContent = `z:${state.floor}`;
        });
        map.on("moveend", pushHash);
        map.on("zoomend", pushHash);

        setFloor(7);
    }

    /* ---------- floor switch ---------- */
    function setFloor(z) {
        if (z === state.floor && state.floorLayers[z]._map) {
            return;
        }
        if (state.floorLayers[state.floor]) {
            state.map.removeLayer(state.floorLayers[state.floor]);
        }
        state.floor = z;
        state.floorLayers[z].addTo(state.map);
        // Keep tile layer at the bottom under markers and route
        if (state.routeLayer) state.routeLayer.bringToFront();
        if (state.markerLayer) state.markerLayer.bringToFront();
        document.getElementById("floor-value").textContent = z;
        document.getElementById("floor-label").textContent = FLOOR_LABELS[z] || "";
        document.getElementById("floor-slider").value = z;
        document.getElementById("coord-z").textContent = `z:${z}`;
        if (state.selected) {
            renderSpawns(state.selected);
            renderDensity(state.selected);
        }
        updateRouteVisibility();
        if (state.huntingMode) renderHuntingMarkers();
        pushHash();
    }

    /* ---------- creature list ---------- */
    function initUI() {
        buildCreatureList();
        bindSearch();
        bindFloorSlider();
        bindModeToggles();
        bindSortToggle();
        bindRoute();
        initHunting();
    }

    function creatureRowHtml(name, count) {
        return `<li data-name="${name}"><span class="n">${name}</span><span class="count">${count}</span></li>`;
    }

    function buildCreatureList() {
        const ul = document.getElementById("creature-list");
        const stats = state.spawns.stats;
        const entries = Object.entries(stats).map(([name, s]) => [name, s.total]);
        if (state.sortBy === "count") {
            entries.sort((a, b) => b[1] - a[1]);
        } else {
            entries.sort((a, b) => a[0].localeCompare(b[0]));
        }
        ul.innerHTML = entries.map(([n, c]) => creatureRowHtml(n, c)).join("");
        ul.querySelectorAll("li").forEach(li => {
            li.addEventListener("click", () => selectCreature(li.dataset.name));
            if (li.dataset.name === state.selected) li.classList.add("active");
        });
    }

    function bindSortToggle() {
        document.querySelectorAll(".sort-toggle button").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".sort-toggle button").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                state.sortBy = btn.dataset.sort;
                buildCreatureList();
            });
        });
    }

    /* ---------- search ---------- */
    function bindSearch() {
        const input = document.getElementById("search");
        const results = document.getElementById("search-results");
        let activeIdx = -1;

        function render(query) {
            const q = query.trim().toLowerCase();
            const stats = state.spawns.stats;
            const all = Object.keys(stats);
            let matches;
            if (q === "") {
                matches = all.slice(0, 40);
            } else {
                matches = all
                    .filter(n => n.includes(q))
                    .sort((a, b) => {
                        const ap = a.startsWith(q) ? 0 : 1;
                        const bp = b.startsWith(q) ? 0 : 1;
                        if (ap !== bp) return ap - bp;
                        return stats[b].total - stats[a].total;
                    })
                    .slice(0, 40);
            }
            if (matches.length === 0) {
                results.innerHTML = `<div class="item"><span class="name">no matches</span></div>`;
                results.classList.add("open");
                return;
            }
            results.innerHTML = matches.map(n =>
                `<div class="item" data-name="${n}"><span class="name">${n}</span><span class="count">${stats[n].total}</span></div>`
            ).join("");
            results.classList.add("open");
            results.querySelectorAll(".item").forEach((el, i) => {
                el.addEventListener("mousedown", (e) => {
                    e.preventDefault();
                    selectCreature(el.dataset.name);
                    results.classList.remove("open");
                    input.value = el.dataset.name;
                });
                el.addEventListener("mouseenter", () => {
                    activeIdx = i;
                    highlightActive();
                });
            });
            activeIdx = 0;
            highlightActive();
        }

        function highlightActive() {
            results.querySelectorAll(".item").forEach((el, i) => {
                el.classList.toggle("active", i === activeIdx);
            });
        }

        input.addEventListener("focus", () => render(input.value));
        input.addEventListener("input", () => render(input.value));
        input.addEventListener("blur", () => {
            setTimeout(() => results.classList.remove("open"), 150);
        });
        input.addEventListener("keydown", (e) => {
            const items = results.querySelectorAll(".item[data-name]");
            if (e.key === "ArrowDown") {
                activeIdx = Math.min(items.length - 1, activeIdx + 1);
                highlightActive();
                e.preventDefault();
            } else if (e.key === "ArrowUp") {
                activeIdx = Math.max(0, activeIdx - 1);
                highlightActive();
                e.preventDefault();
            } else if (e.key === "Enter") {
                const el = items[activeIdx];
                if (el) {
                    selectCreature(el.dataset.name);
                    input.value = el.dataset.name;
                    results.classList.remove("open");
                    input.blur();
                }
                e.preventDefault();
            } else if (e.key === "Escape") {
                results.classList.remove("open");
                input.blur();
            }
        });
    }

    /* ---------- creature selection ---------- */
    function selectCreature(name) {
        if (!state.spawns.stats[name]) return;
        state.selected = name;
        document.querySelectorAll("#creature-list li").forEach(li => {
            li.classList.toggle("active", li.dataset.name === name);
        });
        renderSelectedInfo(name);
        renderDensity(name);
        renderSpawns(name);
        // Fly to the first on-floor spawn, else the first overall.
        const indices = state.spawns.byMonster[name] || [];
        let firstOnFloor = null;
        let firstAny = null;
        for (const i of indices) {
            const sp = state.spawns.spawns[i];
            if (firstAny === null) firstAny = sp;
            if (sp.cz === state.floor) { firstOnFloor = sp; break; }
        }
        const target = firstOnFloor || firstAny;
        if (target) {
            if (target.cz !== state.floor && firstOnFloor === null) {
                setFloor(target.cz);
            }
            const ll = worldToLatLng(target.cx, target.cy);
            state.map.panTo(ll, { animate: true });
        }
        pushHash();
    }

    function renderSelectedInfo(name) {
        const body = document.querySelector("#panel-selected .panel-body");
        body.classList.remove("empty");
        const m = state.monsters[name];
        if (!m) {
            body.innerHTML = `<div class="selected-card">
                <div class="name">${name}</div>
                <div class="desc">No metadata (orphan spawn)</div>
            </div>`;
            return;
        }
        body.innerHTML = `<div class="selected-card">
            <div class="name">${m.name || name}</div>
            <div class="desc">${m.description || ""}</div>
            <div class="k">HP</div><div class="v">${m.hp ?? "–"}</div>
            <div class="k">Exp</div><div class="v">${m.exp ?? "–"}</div>
            <div class="k">Speed</div><div class="v">${m.speed ?? "–"}</div>
            <div class="k">Race</div><div class="v">${m.race || "–"}</div>
            <div class="k">Hostile</div><div class="v">${m.hostile ? "yes" : "no"}</div>
        </div>`;
    }

    function renderDensity(name) {
        const stats = state.spawns.stats[name];
        document.querySelector(".density-total").textContent =
            `${stats.total} spawn${stats.total === 1 ? "" : "s"}`;
        const chart = document.getElementById("density-chart");
        const W = 320, H = 140;
        const floors = [];
        for (let z = 0; z <= 15; z++) {
            floors.push(stats.byFloor[z] || 0);
        }
        const maxV = Math.max(1, ...floors);
        const barW = W / 16;
        const parts = [];
        for (let z = 0; z <= 15; z++) {
            const v = floors[z];
            const h = v === 0 ? 0 : (v / maxV) * (H - 20);
            const x = z * barW + 1;
            const y = H - h - 14;
            const cls = z === state.floor ? "density-chart-bar active" : "density-chart-bar";
            parts.push(`<rect x="${x}" y="${y}" width="${barW - 2}" height="${h}" class="${cls}" rx="1"/>`);
            parts.push(`<text x="${x + barW / 2 - 1}" y="${H - 3}" fill="#8a91a4" font-size="8" text-anchor="middle">${z}</text>`);
            if (v > 0) {
                parts.push(`<text x="${x + barW / 2 - 1}" y="${y - 2}" fill="#dde2ee" font-size="9" text-anchor="middle">${v}</text>`);
            }
        }
        chart.innerHTML = parts.join("");
    }

    function renderSpawns(name) {
        state.markerLayer.clearLayers();
        if (state.heatLayer) {
            state.map.removeLayer(state.heatLayer);
            state.heatLayer = null;
        }
        const indices = state.spawns.byMonster[name] || [];
        if (indices.length === 0) return;

        if (state.markerMode) {
            for (const i of indices) {
                const sp = state.spawns.spawns[i];
                const onFloor = sp.cz === state.floor;
                if (!onFloor && !state.showOtherZ) continue;
                const ll = worldToLatLng(sp.cx, sp.cy);
                // Radius: scale spawn radius in world tiles to pixels at the native zoom.
                const r = Math.max(4, sp.r * 2.5);
                const c = L.circleMarker(ll, {
                    radius: r,
                    color: onFloor ? "#f2a33a" : "#555a70",
                    weight: onFloor ? 2 : 1,
                    fillColor: onFloor ? "#f2a33a" : "#2a2f42",
                    fillOpacity: onFloor ? 0.55 : 0.25,
                    opacity: onFloor ? 0.95 : 0.5,
                });
                c.bindPopup(buildPopup(sp, name));
                c.addTo(state.markerLayer);
            }
        }

        if (state.heatMode) {
            const points = [];
            for (const i of indices) {
                const sp = state.spawns.spawns[i];
                if (sp.cz !== state.floor && !state.showOtherZ) continue;
                const ll = worldToLatLng(sp.cx, sp.cy);
                const intensity = sp.cz === state.floor ? 1.0 : 0.35;
                // leaflet.heat takes [lat, lng, intensity]
                const count = sp.c.filter(c => c.n === name).length || 1;
                points.push([ll.lat, ll.lng, intensity * count]);
            }
            state.heatLayer = L.heatLayer(points, {
                radius: 22,
                blur: 18,
                maxZoom: state.meta.max_native_zoom,
                gradient: {0.2: "#222266", 0.4: "#4477dd", 0.6: "#cfc02a", 0.8: "#f2a33a", 1.0: "#d4554e"},
            }).addTo(state.map);
        }
    }

    function buildPopup(sp, selectedName) {
        const groupedByName = {};
        for (const c of sp.c) {
            groupedByName[c.n] = (groupedByName[c.n] || 0) + 1;
        }
        const items = Object.entries(groupedByName)
            .sort((a, b) => b[1] - a[1])
            .map(([n, c]) => `<li>${n} × ${c}${n === selectedName ? " ◂" : ""}</li>`)
            .join("");
        return `<div class="spawn-popup">
            <div class="title">Spawn #${sp.id}</div>
            <div class="meta">x:${sp.cx} y:${sp.cy} z:${sp.cz} · radius ${sp.r}</div>
            <ul class="list">${items}</ul>
            <button class="route-btn" data-cx="${sp.cx}" data-cy="${sp.cy}" data-cz="${sp.cz}">Route here</button>
        </div>`;
    }

    /* ---------- controls ---------- */
    function bindFloorSlider() {
        const slider = document.getElementById("floor-slider");
        slider.addEventListener("input", () => {
            setFloor(parseInt(slider.value, 10));
        });
    }

    function bindModeToggles() {
        document.getElementById("mode-markers").addEventListener("change", (e) => {
            state.markerMode = e.target.checked;
            if (state.selected) renderSpawns(state.selected);
        });
        document.getElementById("mode-heat").addEventListener("change", (e) => {
            state.heatMode = e.target.checked;
            if (state.selected) renderSpawns(state.selected);
        });
        document.getElementById("mode-otherz").addEventListener("change", (e) => {
            state.showOtherZ = e.target.checked;
            if (state.selected) renderSpawns(state.selected);
        });
    }

    /* ---------- route ---------- */
    function bindRoute() {
        // Delegate clicks on "Route here" buttons inside Leaflet popups
        document.addEventListener("click", (e) => {
            const btn = e.target.closest(".route-btn");
            if (!btn) return;
            const cx = parseInt(btn.dataset.cx, 10);
            const cy = parseInt(btn.dataset.cy, 10);
            const cz = parseInt(btn.dataset.cz, 10);
            enterRouteMode(cx, cy, cz);
            state.map.closePopup();
        });

        document.getElementById("route-clear").addEventListener("click", clearRoute);

        // Map click handler for setting start point
        state.map.on("click", (e) => {
            if (!state.routeMode) return;
            const p = state.map.project(e.latlng, state.meta.max_native_zoom);
            const wx = Math.round(p.x + state.meta.origin.x);
            const wy = Math.round(p.y + state.meta.origin.y);
            computeRoute(wx, wy, state.floor);
        });
    }

    function enterRouteMode(destX, destY, destZ) {
        state.routeDest = { x: destX, y: destY, z: destZ };
        state.routeMode = true;
        state.routePath = null;
        state.routeStart = null;
        state.routeLayer.clearLayers();

        document.getElementById("map").classList.add("route-mode");
        document.getElementById("panel-route").style.display = "";
        const status = document.getElementById("route-status");
        status.textContent = "Click the map to set your starting position.";
        status.className = "";
        document.getElementById("route-info").style.display = "none";

        // Show destination marker
        const ll = worldToLatLng(destX, destY);
        L.circleMarker(ll, {
            radius: 8, color: "#f2a33a", weight: 3,
            fillColor: "#f2a33a", fillOpacity: 0.7,
        }).addTo(state.routeLayer).bindTooltip("Destination", { permanent: false });
    }

    async function computeRoute(startX, startY, startZ) {
        state.routeStart = { x: startX, y: startY, z: startZ };
        state.routeMode = false;
        document.getElementById("map").classList.remove("route-mode");
        document.getElementById("panel-route").style.display = "";

        const status = document.getElementById("route-status");
        status.textContent = "Loading navigation data...";
        status.className = "computing";

        try {
            await Pathfinder.loadData(DATA_URL);
        } catch (err) {
            status.textContent = "Failed to load navigation data.";
            status.className = "error";
            console.error(err);
            return;
        }

        status.textContent = "Computing route...";

        // Defer to next frame so the UI updates
        await new Promise(r => requestAnimationFrame(r));

        // Snap start and end to nearest walkable tiles
        const snapStart = Pathfinder.nearestWalkable(startX, startY, startZ, 10);
        const dest = state.routeDest;
        const snapEnd = Pathfinder.nearestWalkable(dest.x, dest.y, dest.z, 10);

        if (!snapStart || !snapEnd) {
            status.textContent = "Start or destination is not near any walkable tile.";
            status.className = "error";
            state.routeMode = true;
            document.getElementById("map").classList.add("route-mode");
            return;
        }

        const path = Pathfinder.route(snapStart.x, snapStart.y, snapStart.z, snapEnd.x, snapEnd.y, snapEnd.z);

        if (!path) {
            status.textContent = "No path found. Try a different starting point.";
            status.className = "error";
            // Allow clicking again
            state.routeMode = true;
            document.getElementById("map").classList.add("route-mode");
            return;
        }

        state.routePath = path;
        drawRoute(path);
        showRouteInfo(path);
        pushHash();
    }

    function drawRoute(path) {
        state.routeLayer.clearLayers();
        const segments = Pathfinder.segmentByFloor(path);

        // Start marker
        const startPt = path[0];
        L.circleMarker(worldToLatLng(startPt.x, startPt.y), {
            radius: 8, color: "#57c28a", weight: 3,
            fillColor: "#57c28a", fillOpacity: 0.7,
        }).addTo(state.routeLayer).bindTooltip("Start", { permanent: false });

        // End marker
        const endPt = path[path.length - 1];
        L.circleMarker(worldToLatLng(endPt.x, endPt.y), {
            radius: 8, color: "#f2a33a", weight: 3,
            fillColor: "#f2a33a", fillOpacity: 0.7,
        }).addTo(state.routeLayer).bindTooltip("Destination", { permanent: false });

        // Polyline per floor segment
        for (const seg of segments) {
            const simplified = Pathfinder.simplifySegment(seg.points);
            const latlngs = simplified.map(p => worldToLatLng(p.x, p.y));
            const onFloor = seg.z === state.floor;
            const line = L.polyline(latlngs, {
                color: "#76b3ff",
                weight: onFloor ? 4 : 2,
                opacity: onFloor ? 0.9 : 0.25,
                dashArray: onFloor ? null : "6,4",
                _routeFloor: seg.z,
            });
            line.addTo(state.routeLayer);
        }

        // Floor-change markers
        for (let i = 1; i < path.length; i++) {
            if (path[i].z !== path[i - 1].z) {
                const pt = path[i - 1];
                const fromZ = path[i - 1].z;
                const toZ = path[i].z;
                const ll = worldToLatLng(pt.x, pt.y);
                const marker = L.circleMarker(ll, {
                    radius: 6, color: "#76b3ff", weight: 2,
                    fillColor: "#1c2130", fillOpacity: 0.9,
                    _routeFloor: fromZ,
                });
                marker.bindTooltip(`z${fromZ} → z${toZ}`, { permanent: false });
                marker.on("click", () => setFloor(toZ));
                marker.addTo(state.routeLayer);
            }
        }
    }

    function updateRouteVisibility() {
        if (!state.routeLayer) return;
        state.routeLayer.eachLayer((layer) => {
            if (layer.options && layer.options._routeFloor !== undefined) {
                const onFloor = layer.options._routeFloor === state.floor;
                if (layer.setStyle) {
                    layer.setStyle({
                        opacity: onFloor ? 0.9 : 0.25,
                        weight: onFloor ? (layer instanceof L.Polyline && !(layer instanceof L.CircleMarker) ? 4 : 2) : 2,
                        dashArray: onFloor ? null : "6,4",
                    });
                }
            }
        });
    }

    function showRouteInfo(path) {
        const status = document.getElementById("route-status");
        status.textContent = "Route found.";
        status.className = "";
        document.getElementById("route-info").style.display = "";

        document.getElementById("route-dist").textContent = `${path.length} tiles`;

        // Count floor changes
        const floorChanges = [];
        for (let i = 1; i < path.length; i++) {
            if (path[i].z !== path[i - 1].z) {
                floorChanges.push({ from: path[i - 1], to: path[i] });
            }
        }
        document.getElementById("route-fc-count").textContent = floorChanges.length;

        const wpList = document.getElementById("route-waypoints");
        if (floorChanges.length === 0) {
            wpList.innerHTML = "";
            return;
        }
        wpList.innerHTML = floorChanges.map((fc, i) => {
            const label = FLOOR_LABELS[fc.to.z] || `z${fc.to.z}`;
            return `<li data-idx="${i}" data-x="${fc.from.x}" data-y="${fc.from.y}" data-z="${fc.to.z}">
                <span>${fc.from.x}, ${fc.from.y}</span>
                <span class="fc-arrow">z${fc.from.z} → z${fc.to.z}</span>
            </li>`;
        }).join("");

        wpList.querySelectorAll("li").forEach(li => {
            li.addEventListener("click", () => {
                const x = parseInt(li.dataset.x, 10);
                const y = parseInt(li.dataset.y, 10);
                const z = parseInt(li.dataset.z, 10);
                setFloor(z);
                state.map.panTo(worldToLatLng(x, y), { animate: true });
            });
        });
    }

    function clearRoute() {
        state.routeMode = false;
        state.routeDest = null;
        state.routeStart = null;
        state.routePath = null;
        state.routeLayer.clearLayers();
        document.getElementById("map").classList.remove("route-mode");
        document.getElementById("panel-route").style.display = "none";
        pushHash();
    }

    /* ---------- hunting calculator ---------- */
    function initHunting() {
        if (!state.huntingAreas) return;

        state.huntingLayer = L.featureGroup().addTo(state.map);

        document.getElementById("hunting-toggle").addEventListener("click", toggleHunting);
        document.getElementById("hunting-close").addEventListener("click", toggleHunting);
        document.getElementById("hunt-calc").addEventListener("click", runHuntingCalc);

        document.getElementById("hunt-style").addEventListener("change", (e) => {
            document.getElementById("hunt-rune-row").style.display =
                e.target.value === "melee_rune" ? "" : "none";
        });

        document.querySelectorAll(".hunt-sort button").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".hunt-sort button").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                state.huntingSort = btn.dataset.sort;
                renderHuntingResults();
            });
        });
    }

    function toggleHunting() {
        state.huntingMode = !state.huntingMode;
        document.getElementById("panel-hunting").style.display = state.huntingMode ? "" : "none";
        document.getElementById("hunting-toggle").classList.toggle("active", state.huntingMode);
        if (!state.huntingMode) {
            state.huntingLayer.clearLayers();
            state.huntingResults = null;
        }
    }

    function getHuntingProfile() {
        return HuntingCalc.makeProfile({
            level: parseInt(document.getElementById("hunt-level").value, 10) || 50,
            weaponSkill: parseInt(document.getElementById("hunt-skill").value, 10) || 60,
            magicLevel: parseInt(document.getElementById("hunt-mlvl").value, 10) || 4,
            shieldSkill: parseInt(document.getElementById("hunt-shield").value, 10) || 50,
            weaponAttack: parseInt(document.getElementById("hunt-watk").value, 10) || 30,
            shieldDefense: parseInt(document.getElementById("hunt-sdef").value, 10) || 23,
            totalArmor: parseInt(document.getElementById("hunt-armor").value, 10) || 25,
            premium: document.getElementById("hunt-premium").checked,
            huntStyle: document.getElementById("hunt-style").value,
            runeType: document.getElementById("hunt-rune").value,
            healMethod: document.getElementById("hunt-heal").value,
        });
    }

    function runHuntingCalc() {
        if (!state.huntingAreas) return;
        const profile = getHuntingProfile();
        const areas = state.huntingAreas.areas;
        state.huntingResults = HuntingCalc.rankAreas(areas, profile, state.monsters);

        const viable = state.huntingResults.filter(r => r.eval.sustainable);
        const topExp = viable.length > 0 ? viable[0].eval.expHour : 0;
        const maxHP = HuntingCalc.KNIGHT_BASE_HP + profile.level * HuntingCalc.KNIGHT_HP_PER_LVL;
        const meleeDmg = HuntingCalc.calcMeleeAvg(profile.weaponSkill, profile.weaponAttack, 1.0);

        const summary = document.getElementById("hunt-summary");
        summary.style.display = "";
        summary.innerHTML = `
            <div class="hunt-stat"><span class="k">Max HP</span><span class="v">${maxHP}</span></div>
            <div class="hunt-stat"><span class="k">Avg melee hit</span><span class="v">${Math.round(meleeDmg)}</span></div>
            <div class="hunt-stat"><span class="k">Best exp/h</span><span class="v" style="color:var(--accent)">${topExp.toLocaleString()}</span></div>
            <div class="hunt-stat"><span class="k">Viable areas</span><span class="v">${viable.length} / ${state.huntingResults.length}</span></div>
        `;

        document.getElementById("hunt-sort-row").style.display = "";
        renderHuntingResults();
        renderHuntingMarkers();
    }

    function renderHuntingResults() {
        if (!state.huntingResults) return;
        const ul = document.getElementById("hunt-results");
        let results = [...state.huntingResults];

        if (state.huntingSort === "safety") {
            const order = { safe: 0, moderate: 1, dangerous: 2, lethal: 3 };
            results.sort((a, b) => {
                const d = order[a.eval.danger] - order[b.eval.danger];
                return d !== 0 ? d : b.eval.expHour - a.eval.expHour;
            });
        }

        // Show top 80 results
        results = results.slice(0, 80);

        ul.innerHTML = results.map((r, i) => {
            const creatures = Object.keys(r.area.creatures)
                .sort((a, b) => r.area.creatures[b] - r.area.creatures[a])
                .slice(0, 2)
                .join(", ");
            const expStr = r.eval.expHour >= 1000
                ? (r.eval.expHour / 1000).toFixed(1) + "k"
                : r.eval.expHour;
            const runeInfo = r.eval.runesHour > 0
                ? `${r.eval.runesHour} runes/h`
                : "";
            return `<li data-idx="${i}" data-area="${r.area.id}">
                <div class="hunt-result-top">
                    <span class="hunt-result-name">${creatures}</span>
                    <span class="hunt-result-exp">${expStr}/h</span>
                </div>
                <div class="hunt-result-meta">
                    <span class="hunt-badge ${r.eval.danger}">${r.eval.danger}</span>
                    <span>z${r.area.cz}</span>
                    ${runeInfo ? `<span>${runeInfo}</span>` : ""}
                </div>
            </li>`;
        }).join("");

        ul.querySelectorAll("li").forEach(li => {
            li.addEventListener("click", () => {
                const idx = parseInt(li.dataset.idx, 10);
                selectHuntingArea(results[idx]);
            });
        });
    }

    function renderHuntingMarkers() {
        state.huntingLayer.clearLayers();
        if (!state.huntingResults) return;

        const maxExp = state.huntingResults.length > 0
            ? state.huntingResults[0].eval.expHour
            : 1;

        for (const r of state.huntingResults) {
            if (r.eval.expHour <= 0) continue;
            const onFloor = r.area.cz === state.floor;
            if (!onFloor) continue;

            const ll = worldToLatLng(r.area.cx, r.area.cy);
            const sizeScale = Math.max(5, Math.min(18, 5 + (r.eval.expHour / maxExp) * 13));

            const colors = {
                safe: "#57c28a",
                moderate: "#f2c94c",
                dangerous: "#f2a33a",
                lethal: "#d4554e",
            };
            const color = colors[r.eval.danger] || "#555";

            const marker = L.circleMarker(ll, {
                radius: sizeScale,
                color: color,
                weight: 2,
                fillColor: color,
                fillOpacity: 0.4,
                opacity: 0.8,
            });

            const creatures = Object.entries(r.area.creatures)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 4)
                .map(([n, c]) => `${n} x${c}`)
                .join(", ");

            marker.bindTooltip(
                `<b>${r.eval.expHour.toLocaleString()} exp/h</b><br>${creatures}`,
                { direction: "top", opacity: 0.95 }
            );

            marker.addTo(state.huntingLayer);
        }
    }

    function selectHuntingArea(result) {
        const area = result.area;
        if (area.cz !== state.floor) {
            setFloor(area.cz);
        }
        const ll = worldToLatLng(area.cx, area.cy);
        state.map.setView(ll, 3, { animate: true });

        // Highlight in list
        document.querySelectorAll("#hunt-results li").forEach(li => {
            li.classList.toggle("active", parseInt(li.dataset.area, 10) === area.id);
        });
    }

    /* ---------- URL state ---------- */
    function pushHash() {
        if (!state.map) return;
        const c = state.map.getCenter();
        const p = state.map.project(c, state.meta.max_native_zoom);
        const wx = Math.round(p.x + state.meta.origin.x);
        const wy = Math.round(p.y + state.meta.origin.y);
        const zoom = state.map.getZoom();
        const parts = [`z=${state.floor}`, `x=${wx}`, `y=${wy}`, `zoom=${zoom}`];
        if (state.selected) parts.push(`m=${encodeURIComponent(state.selected)}`);
        if (state.routeStart) parts.push(`rs=${state.routeStart.x},${state.routeStart.y},${state.routeStart.z}`);
        if (state.routeDest) parts.push(`rd=${state.routeDest.x},${state.routeDest.y},${state.routeDest.z}`);
        history.replaceState(null, "", "#" + parts.join("&"));
    }

    function restoreFromHash() {
        const h = location.hash.replace(/^#/, "");
        if (!h) return;
        const params = Object.fromEntries(
            h.split("&").map(p => {
                const [k, v] = p.split("=");
                return [k, decodeURIComponent(v || "")];
            })
        );
        if (params.z !== undefined) setFloor(parseInt(params.z, 10));
        if (params.x !== undefined && params.y !== undefined) {
            const ll = worldToLatLng(parseInt(params.x, 10), parseInt(params.y, 10));
            const zoom = params.zoom !== undefined ? parseFloat(params.zoom) : 3;
            state.map.setView(ll, zoom);
        }
        if (params.m) {
            setTimeout(() => selectCreature(params.m), 50);
        }
        if (params.rs && params.rd) {
            const rsParts = params.rs.split(",").map(Number);
            const rdParts = params.rd.split(",").map(Number);
            if (rsParts.length === 3 && rdParts.length === 3) {
                state.routeDest = { x: rdParts[0], y: rdParts[1], z: rdParts[2] };
                setTimeout(() => computeRoute(rsParts[0], rsParts[1], rsParts[2]), 100);
            }
        }
    }

    /* ---------- go ---------- */
    document.addEventListener("DOMContentLoaded", boot);
})();
