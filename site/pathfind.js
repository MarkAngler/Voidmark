/* =======================================================================
   Voidmark — A* pathfinding engine (cross-floor, RLE walkability grid)
   ======================================================================= */

window.Pathfinder = (() => {
    "use strict";

    const FC_DOWN = 1;
    const FC_NORTH = 2;
    const FC_EAST = 4;
    const FC_SOUTH = 8;
    const FC_WEST = 16;

    const COST_CARDINAL = 10;
    const COST_DIAGONAL = 14;
    const COST_FLOOR_CHANGE = 10;
    const MAX_EXPANSIONS = 500000;

    /* cardinal + diagonal offsets: [dx, dy] */
    const DIRS = [
        [0, -1], [1, 0], [0, 1], [-1, 0],           // N E S W
        [1, -1], [1, 1], [-1, 1], [-1, -1],          // NE SE SW NW
    ];
    const DIR_COSTS = [
        COST_CARDINAL, COST_CARDINAL, COST_CARDINAL, COST_CARDINAL,
        COST_DIAGONAL, COST_DIAGONAL, COST_DIAGONAL, COST_DIAGONAL,
    ];

    /* ------------------------------------------------------------------ */
    /*  Binary min-heap                                                    */
    /* ------------------------------------------------------------------ */
    class MinHeap {
        constructor() {
            this._keys = [];   // node keys (packed ints)
            this._vals = [];   // f-scores
        }

        get size() { return this._keys.length; }

        push(key, val) {
            this._keys.push(key);
            this._vals.push(val);
            this._up(this._keys.length - 1);
        }

        pop() {
            const k = this._keys[0];
            const last = this._keys.length - 1;
            if (last > 0) {
                this._keys[0] = this._keys[last];
                this._vals[0] = this._vals[last];
            }
            this._keys.length = last;
            this._vals.length = last;
            if (last > 0) this._down(0);
            return k;
        }

        _up(i) {
            const keys = this._keys, vals = this._vals;
            while (i > 0) {
                const p = (i - 1) >> 1;
                if (vals[p] <= vals[i]) break;
                [keys[p], keys[i]] = [keys[i], keys[p]];
                [vals[p], vals[i]] = [vals[i], vals[p]];
                i = p;
            }
        }

        _down(i) {
            const keys = this._keys, vals = this._vals;
            const n = keys.length;
            while (true) {
                let best = i;
                const l = 2 * i + 1, r = 2 * i + 2;
                if (l < n && vals[l] < vals[best]) best = l;
                if (r < n && vals[r] < vals[best]) best = r;
                if (best === i) break;
                [keys[best], keys[i]] = [keys[i], keys[best]];
                [vals[best], vals[i]] = [vals[i], vals[best]];
                i = best;
            }
        }
    }

    /* ------------------------------------------------------------------ */
    /*  Walk grid — RLE-based walkability lookup                           */
    /* ------------------------------------------------------------------ */
    class WalkGrid {
        constructor(navData) {
            this._originX = navData.origin[0];
            this._originY = navData.origin[1];
            this._w = navData.size[0];
            this._h = navData.size[1];
            this._floors = {};

            for (const [z, floorData] of Object.entries(navData.floors)) {
                const rows = {};
                for (const [ly, runs] of Object.entries(floorData.rows)) {
                    rows[ly | 0] = runs;   // keep as [[start, len], ...]
                }
                this._floors[z | 0] = rows;
            }
        }

        isWalkable(wx, wy, z) {
            const lx = wx - this._originX;
            const ly = wy - this._originY;
            if (lx < 0 || lx >= this._w || ly < 0 || ly >= this._h || z < 0 || z > 15) {
                return false;
            }
            const floorRows = this._floors[z];
            if (!floorRows) return false;
            const runs = floorRows[ly];
            if (!runs) return false;

            // Binary search for the run containing lx
            let lo = 0, hi = runs.length - 1;
            while (lo <= hi) {
                const mid = (lo + hi) >> 1;
                const start = runs[mid][0];
                const end = start + runs[mid][1];
                if (lx < start) hi = mid - 1;
                else if (lx >= end) lo = mid + 1;
                else return true;
            }
            return false;
        }
    }

    /* ------------------------------------------------------------------ */
    /*  Floor-change map                                                   */
    /* ------------------------------------------------------------------ */
    class FloorChangeMap {
        constructor(fcList) {
            this._map = new Map();
            for (const entry of fcList) {
                const key = entry[0] + "," + entry[1] + "," + entry[2];
                this._map.set(key, entry[3]);
            }
        }

        get(wx, wy, z) {
            return this._map.get(wx + "," + wy + "," + z) || 0;
        }
    }

    /* ------------------------------------------------------------------ */
    /*  A* pathfinding                                                     */
    /* ------------------------------------------------------------------ */
    function findPath(grid, fcMap, sx, sy, sz, ex, ey, ez) {
        const W = grid._w;
        const H = grid._h;
        const ox = grid._originX;
        const oy = grid._originY;

        // Pack (wx, wy, z) into a unique integer key
        function packKey(wx, wy, z) {
            return z * W * H + (wy - oy) * W + (wx - ox);
        }

        // Chebyshev heuristic (admissible, ignoring z)
        function heuristic(wx, wy) {
            return Math.max(Math.abs(wx - ex), Math.abs(wy - ey)) * COST_CARDINAL;
        }

        if (!grid.isWalkable(sx, sy, sz) || !grid.isWalkable(ex, ey, ez)) {
            return null;
        }

        const startKey = packKey(sx, sy, sz);
        const endKey = packKey(ex, ey, ez);

        if (startKey === endKey) {
            return [{ x: sx, y: sy, z: sz }];
        }

        const open = new MinHeap();
        const gScore = new Map();
        const parent = new Map();
        const closed = new Set();

        gScore.set(startKey, 0);
        open.push(startKey, heuristic(sx, sy));

        let expansions = 0;

        while (open.size > 0) {
            const curKey = open.pop();

            if (curKey === endKey) {
                return _reconstructPath(parent, curKey, ox, oy, W, H);
            }

            if (closed.has(curKey)) continue;
            closed.add(curKey);

            if (++expansions > MAX_EXPANSIONS) return null;

            const curG = gScore.get(curKey);
            // Unpack key
            const cz = (curKey / (W * H)) | 0;
            const remainder = curKey - cz * W * H;
            const cly = (remainder / W) | 0;
            const clx = remainder - cly * W;
            const cwx = clx + ox;
            const cwy = cly + oy;

            // Same-floor neighbors (8 directions)
            for (let d = 0; d < 8; d++) {
                const nx = cwx + DIRS[d][0];
                const ny = cwy + DIRS[d][1];

                if (!grid.isWalkable(nx, ny, cz)) continue;

                // Diagonal corner-cut check
                if (d >= 4) {
                    if (!grid.isWalkable(cwx + DIRS[d][0], cwy, cz) ||
                        !grid.isWalkable(cwx, cwy + DIRS[d][1], cz)) {
                        continue;
                    }
                }

                const nKey = packKey(nx, ny, cz);
                if (closed.has(nKey)) continue;

                const tentG = curG + DIR_COSTS[d];
                const prevG = gScore.get(nKey);
                if (prevG !== undefined && tentG >= prevG) continue;

                gScore.set(nKey, tentG);
                parent.set(nKey, curKey);
                open.push(nKey, tentG + heuristic(nx, ny));
            }

            // Floor-change neighbors
            const fc = fcMap.get(cwx, cwy, cz);
            if (fc) {
                const fcTargets = [];
                if (fc & FC_DOWN)  fcTargets.push([cwx, cwy, cz + 1]);
                if (fc & FC_NORTH) fcTargets.push([cwx, cwy - 1, cz - 1]);
                if (fc & FC_EAST)  fcTargets.push([cwx + 1, cwy, cz - 1]);
                if (fc & FC_SOUTH) fcTargets.push([cwx, cwy + 1, cz - 1]);
                if (fc & FC_WEST)  fcTargets.push([cwx - 1, cwy, cz - 1]);

                for (const [fx, fy, fz] of fcTargets) {
                    if (fz < 0 || fz > 15) continue;
                    if (!grid.isWalkable(fx, fy, fz)) continue;

                    const fKey = packKey(fx, fy, fz);
                    if (closed.has(fKey)) continue;

                    const tentG = curG + COST_FLOOR_CHANGE;
                    const prevG = gScore.get(fKey);
                    if (prevG !== undefined && tentG >= prevG) continue;

                    gScore.set(fKey, tentG);
                    parent.set(fKey, curKey);
                    open.push(fKey, tentG + heuristic(fx, fy));
                }
            }
        }

        return null;  // No path found
    }

    function _reconstructPath(parent, endKey, ox, oy, W, H) {
        const path = [];
        let key = endKey;
        while (key !== undefined) {
            const z = (key / (W * H)) | 0;
            const rem = key - z * W * H;
            const ly = (rem / W) | 0;
            const lx = rem - ly * W;
            path.push({ x: lx + ox, y: ly + oy, z });
            key = parent.get(key);
        }
        path.reverse();
        return path;
    }

    /* ------------------------------------------------------------------ */
    /*  Segment path by floor for rendering                                */
    /* ------------------------------------------------------------------ */
    function segmentByFloor(path) {
        if (!path || path.length === 0) return [];
        const segments = [];
        let current = { z: path[0].z, points: [path[0]] };
        for (let i = 1; i < path.length; i++) {
            const pt = path[i];
            if (pt.z !== current.z) {
                segments.push(current);
                current = { z: pt.z, points: [pt] };
            } else {
                current.points.push(pt);
            }
        }
        segments.push(current);
        return segments;
    }

    /* ------------------------------------------------------------------ */
    /*  Simplify a same-floor polyline (Douglas-Peucker on axis-aligned)   */
    /* ------------------------------------------------------------------ */
    function simplifySegment(points) {
        if (points.length <= 2) return points;
        const result = [points[0]];
        let prevDx = 0, prevDy = 0;
        for (let i = 1; i < points.length; i++) {
            const dx = Math.sign(points[i].x - points[i - 1].x);
            const dy = Math.sign(points[i].y - points[i - 1].y);
            if (dx !== prevDx || dy !== prevDy) {
                if (result[result.length - 1] !== points[i - 1]) {
                    result.push(points[i - 1]);
                }
            }
            prevDx = dx;
            prevDy = dy;
        }
        result.push(points[points.length - 1]);
        return result;
    }

    /* ------------------------------------------------------------------ */
    /*  Public API                                                         */
    /* ------------------------------------------------------------------ */
    let _navPromise = null;
    let _grid = null;
    let _fcMap = null;

    function loadData(dataUrl) {
        if (!_navPromise) {
            _navPromise = fetch(dataUrl + "navdata.json")
                .then(r => r.json())
                .then(data => {
                    _grid = new WalkGrid(data);
                    _fcMap = new FloorChangeMap(data.fc);
                });
        }
        return _navPromise;
    }

    function isReady() {
        return _grid !== null && _fcMap !== null;
    }

    function route(sx, sy, sz, ex, ey, ez) {
        if (!_grid || !_fcMap) return null;
        return findPath(_grid, _fcMap, sx, sy, sz, ex, ey, ez);
    }

    function isWalkable(wx, wy, z) {
        if (!_grid) return false;
        return _grid.isWalkable(wx, wy, z);
    }

    function nearestWalkable(wx, wy, z, maxRadius) {
        if (!_grid) return null;
        if (_grid.isWalkable(wx, wy, z)) return { x: wx, y: wy, z };
        for (let r = 1; r <= (maxRadius || 5); r++) {
            for (let dx = -r; dx <= r; dx++) {
                for (let dy = -r; dy <= r; dy++) {
                    if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
                    if (_grid.isWalkable(wx + dx, wy + dy, z)) {
                        return { x: wx + dx, y: wy + dy, z };
                    }
                }
            }
        }
        return null;
    }

    return { loadData, isReady, route, segmentByFloor, simplifySegment, isWalkable, nearestWalkable };
})();
