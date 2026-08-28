/* A small 3D renderer on a 2D canvas.

   Three.js would be 600KB to draw a few hundred points and a mesh, and the
   page is meant to work on a VPS with no CDN. What is actually needed is a
   rotation, a perspective divide and a painter's-algorithm sort, which is
   the code below.

   Five dimensions at once: three axes, plus colour and radius. That is the
   point of the thing - a fund that looks appealing on return and volatility
   often stops looking appealing once its correlation to what you already
   hold is the colour of the dot. */

const CUBE = (() => {
  const SIZE = 1.0;                       // model space is a unit cube
  const RAMP = ["#0A5C5F", "#0F7E82", "#4A9EA1", "#9BC7C8", "#C9B69B",
                "#C08A5A", "#B9002F"];

  function rampColour(t) {
    if (!isFinite(t)) return "#9AA4B2";
    const clamped = Math.max(0, Math.min(0.999, t));
    return RAMP[Math.floor(clamped * RAMP.length)];
  }

  class Scene {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.rotY = -0.7;            // radians, drag horizontally
      this.rotX = 0.45;            // drag vertically
      this.zoom = 1.35;
      this.points = [];
      this.surface = null;
      this.axes = { x: "X", y: "Y", z: "Z" };
      this.ranges = { x: [0, 1], y: [0, 1], z: [0, 1] };
      this.tickLabels = null;      // optional text ticks, e.g. dates on x
      this.hover = null;
      this.onHover = null;
      this._bind();
    }

    _bind() {
      let dragging = false, lastX = 0, lastY = 0;
      const canvas = this.canvas;

      canvas.addEventListener("pointerdown", ev => {
        dragging = true; lastX = ev.clientX; lastY = ev.clientY;
        canvas.setPointerCapture(ev.pointerId);
        canvas.style.cursor = "grabbing";
      });
      canvas.addEventListener("pointermove", ev => {
        if (dragging) {
          this.rotY += (ev.clientX - lastX) * 0.01;
          // Clamped so the cube cannot be rolled past vertical, where the
          // axis labels end up upside down and the shape stops reading.
          this.rotX = Math.max(-1.35, Math.min(1.35, this.rotX + (ev.clientY - lastY) * 0.01));
          lastX = ev.clientX; lastY = ev.clientY;
          this.draw();
        } else {
          this._pick(ev);
        }
      });
      const stop = ev => {
        dragging = false;
        canvas.style.cursor = "grab";
        if (ev.pointerId !== undefined && canvas.hasPointerCapture?.(ev.pointerId)) {
          canvas.releasePointerCapture(ev.pointerId);
        }
      };
      canvas.addEventListener("pointerup", stop);
      canvas.addEventListener("pointercancel", stop);
      canvas.addEventListener("pointerleave", () => {
        if (this.hover) { this.hover = null; this.onHover?.(null); this.draw(); }
      });
      canvas.addEventListener("wheel", ev => {
        ev.preventDefault();
        this.zoom = Math.max(0.55, Math.min(2.4, this.zoom * (ev.deltaY > 0 ? 0.92 : 1.08)));
        this.draw();
      }, { passive: false });
      canvas.style.cursor = "grab";
      canvas.style.touchAction = "none";
    }

    /* Rotate about Y then X, then divide by depth. Everything is centred on
       the middle of the unit cube so rotation happens about its centre
       rather than a corner. */
    project(x, y, z) {
      const cx = x - SIZE / 2, cy = y - SIZE / 2, cz = z - SIZE / 2;
      const cosY = Math.cos(this.rotY), sinY = Math.sin(this.rotY);
      const cosX = Math.cos(this.rotX), sinX = Math.sin(this.rotX);

      const x1 = cx * cosY - cz * sinY;
      const z1 = cx * sinY + cz * cosY;
      const y2 = cy * cosX - z1 * sinX;
      const z2 = cy * sinX + z1 * cosX;

      const distance = 3.2;
      const scale = (distance / (distance + z2)) * this.zoom;
      const { width, height } = this.canvas;
      const unit = Math.min(width, height) * 0.46;
      return {
        sx: width / 2 + x1 * unit * scale,
        // Y grows downward on a canvas and upward on a chart.
        sy: height / 2 - y2 * unit * scale,
        depth: z2, scale,
      };
    }

    _pick(ev) {
      const box = this.canvas.getBoundingClientRect();
      const px = (ev.clientX - box.left) * (this.canvas.width / box.width);
      const py = (ev.clientY - box.top) * (this.canvas.height / box.height);
      let best = null, bestDistance = 18 * (this.canvas.width / box.width);
      for (const point of this.points) {
        const p = this.project(point.x, point.y, point.z);
        const d = Math.hypot(p.sx - px, p.sy - py);
        if (d < bestDistance) { bestDistance = d; best = point; }
      }
      if (best !== this.hover) {
        this.hover = best;
        this.onHover?.(best, best ? this.project(best.x, best.y, best.z) : null);
        this.draw();
      }
    }

    _edges() {
      const c = [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1],
                 [0, 1, 0], [1, 1, 0], [1, 1, 1], [0, 1, 1]];
      const pairs = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4],
                     [0, 4], [1, 5], [2, 6], [3, 7]];
      return pairs.map(([a, b]) => [c[a], c[b]]);
    }

    _drawFrame() {
      const ctx = this.ctx;
      ctx.lineWidth = 1;
      for (const [a, b] of this._edges()) {
        const p1 = this.project(a[0] * SIZE, a[1] * SIZE, a[2] * SIZE);
        const p2 = this.project(b[0] * SIZE, b[1] * SIZE, b[2] * SIZE);
        // Far edges fade, which is most of what sells the depth.
        const far = (p1.depth + p2.depth) / 2 > 0;
        ctx.strokeStyle = far ? "rgba(90,90,98,.16)" : "rgba(90,90,98,.42)";
        ctx.beginPath();
        ctx.moveTo(p1.sx, p1.sy);
        ctx.lineTo(p2.sx, p2.sy);
        ctx.stroke();
      }

      // Floor grid, so height off the base plane is readable.
      ctx.strokeStyle = "rgba(90,90,98,.13)";
      for (let i = 1; i < 5; i++) {
        const t = i / 5;
        const a1 = this.project(t, 0, 0), a2 = this.project(t, 0, 1);
        const b1 = this.project(0, 0, t), b2 = this.project(1, 0, t);
        ctx.beginPath(); ctx.moveTo(a1.sx, a1.sy); ctx.lineTo(a2.sx, a2.sy); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(b1.sx, b1.sy); ctx.lineTo(b2.sx, b2.sy); ctx.stroke();
      }
    }

    _drawAxisLabels() {
      const ctx = this.ctx;
      ctx.font = "600 11px 'Segoe UI',-apple-system,Helvetica,Arial,sans-serif";
      ctx.fillStyle = "#5B5B62";
      ctx.textAlign = "center";

      const put = (x, y, z, text) => {
        const p = this.project(x, y, z);
        ctx.fillText(text, p.sx, p.sy);
      };
      // Axis names sit further out than the tick values, or the two
      // collide the moment the cube is rotated towards the viewer.
      put(0.5, -0.26, -0.06, this.axes.x);
      put(-0.3, 0.5, -0.06, this.axes.y);
      put(-0.08, -0.26, 0.5, this.axes.z);

      ctx.font = "10px 'Segoe UI',-apple-system,Helvetica,Arial,sans-serif";
      ctx.fillStyle = "#8A8A93";
      const fmt = v => Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
      const xTicks = this.tickLabels?.x;
      put(0, -0.1, -0.03, xTicks ? xTicks[0] : fmt(this.ranges.x[0]));
      put(1, -0.1, -0.03, xTicks ? xTicks[1] : fmt(this.ranges.x[1]));
      put(-0.12, 0, -0.03, fmt(this.ranges.y[0]));
      put(-0.12, 1, -0.03, fmt(this.ranges.y[1]));
      put(-0.06, -0.1, 1, fmt(this.ranges.z[1]));
    }

    _drawSurface() {
      if (!this.surface) return;
      const { grid, rows, cols, invert } = this.surface;
      const quads = [];
      for (let i = 0; i < rows - 1; i++) {
        for (let j = 0; j < cols - 1; j++) {
          const corners = [grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j]];
          const projected = corners.map(c => this.project(c.x, c.y, c.z));
          const depth = projected.reduce((a, p) => a + p.depth, 0) / 4;
          const height = corners.reduce((a, c) => a + c.y, 0) / 4;
          quads.push({ projected, depth, height });
        }
      }
      // Painter's algorithm: far quads first, so near ones cover them.
      quads.sort((a, b) => b.depth - a.depth);
      const ctx = this.ctx;
      for (const quad of quads) {
        ctx.beginPath();
        quad.projected.forEach((p, i) => i ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy));
        ctx.closePath();
        // Low is good on volatility, high is good on Sharpe or correlation,
        // so which end of the ramp means "bad" has to follow the metric.
        ctx.fillStyle = rampColour(invert ? quad.height : 1 - quad.height);
        ctx.globalAlpha = 0.92;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "rgba(255,255,255,.35)";
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }
    }

    _drawPoints() {
      const projected = this.points.map(point => ({
        point, p: this.project(point.x, point.y, point.z),
      }));
      projected.sort((a, b) => b.p.depth - a.p.depth);

      const ctx = this.ctx;
      for (const { point, p } of projected) {
        // Drop lines to the floor, without which a point's height is
        // genuinely ambiguous once the cube is rotated.
        const floor = this.project(point.x, 0, point.z);
        ctx.strokeStyle = "rgba(90,90,98,.22)";
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        ctx.moveTo(p.sx, p.sy);
        ctx.lineTo(floor.sx, floor.sy);
        ctx.stroke();

        const radius = Math.max(4, point.r * 13 * p.scale);
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, radius, 0, Math.PI * 2);
        ctx.fillStyle = point.colour;
        ctx.globalAlpha = point === this.hover ? 1 : 0.9;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.lineWidth = point.emphasis ? 2.5 : 1;
        ctx.strokeStyle = point.emphasis ? "#000" : "rgba(255,255,255,.85)";
        ctx.stroke();

        if (point.emphasis || point === this.hover) {
          ctx.font = "600 11px 'Segoe UI',-apple-system,Helvetica,Arial,sans-serif";
          ctx.fillStyle = "#1C1C1E";
          ctx.textAlign = "left";
          ctx.fillText(point.label, p.sx + radius + 5, p.sy + 4);
        }
      }
    }

    draw() {
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this._drawFrame();
      this._drawSurface();
      this._drawPoints();
      this._drawAxisLabels();
    }

    resize() {
      const box = this.canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.round(box.width * dpr);
      this.canvas.height = Math.round(Math.max(360, box.width * 0.6) * dpr);
      this.canvas.style.height = Math.round(Math.max(360, box.width * 0.6)) + "px";
      this.draw();
    }
  }

  return { Scene, rampColour, RAMP };
})();
