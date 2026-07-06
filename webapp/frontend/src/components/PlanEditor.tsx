import { useMemo, useRef, useState } from "react";

/**
 * Интерактивный редактор плана этажа для мастера «Моделирование» (частный
 * дом). Работает на СТРУКТУРНОЙ геометрии из raw_floorplan (полигоны комнат,
 * оси стен, проёмы — всё в метрах), а не на готовом SVG: строит свой SVG с
 * ЕДИНЫМ масштабом px/м по обеим осям, поэтому 1 м стены всегда занимает
 * одинаковую длину на экране (жёсткое представление по масштабу — внизу
 * линейка-эталон). Пользователь может:
 *   • тянуть внутреннюю/наружную стену перпендикулярно её оси — это двигает
 *     общую координату «линии разреза» плана (все вершины комнат и концы
 *     стен на этой координате), т.е. изменяет размеры соседних комнат, не
 *     ломая прямоугольную топологию;
 *   • тянуть дверь/окно вдоль своей стены — меняется offset проёма.
 * Правки уходят на /api/house/rerender (перерисовка + перепроверка норм).
 */

const SNAP_M = 0.05;       // шаг привязки при перетаскивании (5 см)
const MIN_GAP_M = 0.6;     // мин. расстояние между соседними линиями разреза
const PAD_M = 1.2;         // поле вокруг плана (м)
const PX_PER_M = 44;       // ЕДИНЫЙ масштаб — один и тот же по X и Y
const HIT_M = 0.28;        // допуск «схватывания» стены курсором (м)

type Pt = [number, number];
interface RoomG { id: string; name: string; polygon: Pt[]; }
interface WallG { id: string; axis: [Pt, Pt]; type: string; thickness_m: number; }
interface OpeningG { wall: string; kind: string; offset_m: number; width_m: number; height_m: number; sill_m: number; }
interface StoreyG { level: number; elevation_m: number; rooms: RoomG[]; walls: WallG[]; openings: OpeningG[]; }

function snap(v: number): number { return Math.round(v / SNAP_M) * SNAP_M; }
function polyArea(p: Pt[]): number {
  let s = 0;
  for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i]; const [x2, y2] = p[(i + 1) % p.length]; s += x1 * y2 - x2 * y1; }
  return Math.abs(s) / 2;
}
function wallLen(w: WallG): number { const [[x1, y1], [x2, y2]] = w.axis; return Math.hypot(x2 - x1, y2 - y1); }

// Активное перетаскивание: либо линия разреза (стена), либо проём.
type Drag =
  | { kind: "line"; axis: "x" | "y"; coord: number; start: number }
  | { kind: "opening"; index: number };

export default function PlanEditor({
  program, floorplan, page, onCancel, onApplied,
}: {
  program: any;
  floorplan: any;
  page: number;
  onCancel: () => void;
  onApplied: (res: { floors: any[]; norms_issues: any[]; raw_program: any; raw_floorplan: any }) => void;
}) {
  // Рабочая копия всей планировки — правим только storeys[page], остальные
  // этажи сохраняем как есть, чтобы отдать rerender'у целиком.
  const [work, setWork] = useState<any>(() => JSON.parse(JSON.stringify(floorplan)));
  const [drag, setDrag] = useState<Drag | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const svgRef = useRef<SVGSVGElement>(null);

  const names: Record<string, string> = useMemo(() => {
    const m: Record<string, string> = {};
    for (const r of program?.building_program?.rooms || []) m[r.id] = r.name;
    return m;
  }, [program]);

  const storey: StoreyG = work.storeys[page];

  // Габариты плана и трансформация мир→px (единый масштаб по обеим осям).
  const box = useMemo(() => {
    const xs: number[] = [], ys: number[] = [];
    for (const r of storey.rooms) for (const [x, y] of r.polygon) { xs.push(x); ys.push(y); }
    for (const w of storey.walls) for (const [x, y] of w.axis) { xs.push(x); ys.push(y); }
    const minX = Math.min(...xs, 0) - PAD_M, minY = Math.min(...ys, 0) - PAD_M;
    const maxX = Math.max(...xs, 1) + PAD_M, maxY = Math.max(...ys, 1) + PAD_M;
    return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY };
  }, [storey]);

  const X = (x: number) => (x - box.minX) * PX_PER_M;
  const Y = (y: number) => (y - box.minY) * PX_PER_M;
  const svgW = box.w * PX_PER_M, svgH = box.h * PX_PER_M;

  function pointerWorld(e: React.PointerEvent): Pt {
    const rect = svgRef.current!.getBoundingClientRect();
    // width/height SVG равны viewBox → 1 ед. viewBox = 1 CSS px (без CSS-растяжения).
    const sx = svgW / rect.width, sy = svgH / rect.height;
    const px = (e.clientX - rect.left) * sx, py = (e.clientY - rect.top) * sy;
    return [px / PX_PER_M + box.minX, py / PX_PER_M + box.minY];
  }

  // Уникальные координаты стен → перетаскиваемые «линии разреза».
  const lines = useMemo(() => {
    const vx = new Map<number, [number, number]>();  // x → [ymin,ymax]
    const hy = new Map<number, [number, number]>();
    for (const w of storey.walls) {
      const [[x1, y1], [x2, y2]] = w.axis;
      if (Math.abs(x1 - x2) < 1e-3) {
        const k = Math.round(x1 * 100) / 100;
        const [a, b] = [Math.min(y1, y2), Math.max(y1, y2)];
        const cur = vx.get(k); vx.set(k, cur ? [Math.min(cur[0], a), Math.max(cur[1], b)] : [a, b]);
      } else if (Math.abs(y1 - y2) < 1e-3) {
        const k = Math.round(y1 * 100) / 100;
        const [a, b] = [Math.min(x1, x2), Math.max(x1, x2)];
        const cur = hy.get(k); hy.set(k, cur ? [Math.min(cur[0], a), Math.max(cur[1], b)] : [a, b]);
      }
    }
    return {
      v: [...vx.entries()].map(([coord, span]) => ({ axis: "x" as const, coord, span })),
      h: [...hy.entries()].map(([coord, span]) => ({ axis: "y" as const, coord, span })),
    };
  }, [storey]);

  // Сдвиг «линии разреза»: все вершины комнат и концы стен на координате
  // coord (±допуск) по оси axis → coord+delta (со снапом и клампом, чтобы не
  // пересечь соседние линии — комнаты не схлопывались).
  function moveLine(axis: "x" | "y", coord: number, target: number) {
    setWork((prev: any) => {
      const next = JSON.parse(JSON.stringify(prev));
      const st: StoreyG = next.storeys[page];
      const ai = axis === "x" ? 0 : 1;
      const others = (axis === "x" ? lines.v : lines.h)
        .map(l => l.coord).filter(c => Math.abs(c - coord) > 1e-3);
      // не даём линии пересечь соседние (иначе комнаты между ними схлопнутся/
      // вывернутся) — клампим в интервал (сосед снизу + gap, сосед сверху − gap).
      const hiBound = Math.min(...others.filter(c => c > coord), Number.POSITIVE_INFINITY);
      const loBound = Math.max(...others.filter(c => c < coord), Number.NEGATIVE_INFINITY);
      let nc = snap(target);
      if (Number.isFinite(hiBound)) nc = Math.min(nc, hiBound - MIN_GAP_M);
      if (Number.isFinite(loBound)) nc = Math.max(nc, loBound + MIN_GAP_M);
      const hit = (v: number) => Math.abs(v - coord) < HIT_M;
      for (const r of st.rooms) for (const p of r.polygon) if (hit(p[ai])) p[ai] = nc;
      for (const w of st.walls) for (const e of w.axis) if (hit(e[ai])) e[ai] = nc;
      // offset проёмов мог выйти за укоротившуюся стену — клампим.
      for (const op of st.openings) {
        const w = st.walls.find(ww => ww.id === op.wall);
        if (!w) continue;
        const L = Math.hypot(w.axis[1][0] - w.axis[0][0], w.axis[1][1] - w.axis[0][1]);
        op.offset_m = Math.max(0, Math.min(op.offset_m, Math.max(0, L - op.width_m)));
      }
      return next;
    });
  }

  function moveOpening(index: number, world: Pt) {
    setWork((prev: any) => {
      const next = JSON.parse(JSON.stringify(prev));
      const st: StoreyG = next.storeys[page];
      const op = st.openings[index];
      const w = st.walls.find(ww => ww.id === op.wall);
      if (!w) return next;
      const [[x1, y1], [x2, y2]] = w.axis;
      const dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy) || 1e-6;
      // проекция курсора на ось стены → положение центра проёма вдоль стены
      const t = ((world[0] - x1) * dx + (world[1] - y1) * dy) / (L * L);
      const center = Math.max(0, Math.min(1, t)) * L;
      op.offset_m = Math.max(0, Math.min(snap(center - op.width_m / 2), Math.max(0, L - op.width_m)));
      return next;
    });
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!drag) return;
    const w = pointerWorld(e);
    if (drag.kind === "line") moveLine(drag.axis, drag.coord, drag.axis === "x" ? w[0] : w[1]);
    else moveOpening(drag.index, w);
  }

  async function apply() {
    setSaving(true); setError("");
    try {
      const res = await fetch("http://localhost:8765/api/house/rerender", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ building_kind: "house", check_norms: true, program, floorplan: work }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Ошибка перерисовки");
      onApplied(d);
    } catch (e: any) { setError(e.message); }
    finally { setSaving(false); }
  }

  // позиция центра проёма (в мире) для отрисовки маркера
  function openingCenter(op: OpeningG): { c: Pt; horiz: boolean } | null {
    const w = storey.walls.find(ww => ww.id === op.wall);
    if (!w) return null;
    const [[x1, y1], [x2, y2]] = w.axis;
    const L = wallLen(w) || 1e-6;
    const t = (op.offset_m + op.width_m / 2) / L;
    return { c: [x1 + (x2 - x1) * t, y1 + (y2 - y1) * t], horiz: Math.abs(y1 - y2) < 1e-3 };
  }

  const ruler = Math.ceil(box.w);  // деления линейки — по метрам ширины

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>✏️ Редактирование · Этаж {storey.level}</span>
        <span style={{ fontSize: "0.72rem", color: "var(--text3)" }}>
          тяни стену — двигаешь её и меняешь размер комнат · тяни окно/дверь — вдоль стены · масштаб {PX_PER_M}px = 1 м
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="btn-gen" onClick={onCancel} disabled={saving} style={{ background: "var(--bg3)", color: "var(--text)" }}>Отмена</button>
          <button className="btn-gen" onClick={apply} disabled={saving}>{saving ? "⏳..." : "✅ Применить"}</button>
        </div>
      </div>
      {error && <div style={{ padding: "6px 10px", background: "rgba(255,69,58,0.1)", border: "1px solid rgba(255,69,58,0.25)", borderRadius: 6, fontSize: "0.78rem", color: "var(--danger)" }}>❌ {error}</div>}

      <div style={{ background: "#fff", borderRadius: 8, padding: 8, overflow: "auto", maxHeight: "60vh" }}>
        <svg
          ref={svgRef}
          width={svgW} height={svgH} viewBox={`0 0 ${svgW} ${svgH}`}
          style={{ touchAction: "none", userSelect: "none", display: "block" }}
          onPointerMove={onPointerMove}
          onPointerUp={() => setDrag(null)}
          onPointerLeave={() => setDrag(null)}
        >
          {/* сетка 1 м — эталон масштаба */}
          {Array.from({ length: Math.ceil(box.w) + 1 }, (_, i) => box.minX + i).map((gx, i) => (
            <line key={`gx${i}`} x1={X(gx)} y1={0} x2={X(gx)} y2={svgH} stroke="#eef2f6" strokeWidth={1} />
          ))}
          {Array.from({ length: Math.ceil(box.h) + 1 }, (_, i) => box.minY + i).map((gy, i) => (
            <line key={`gy${i}`} x1={0} y1={Y(gy)} x2={svgW} y2={Y(gy)} stroke="#eef2f6" strokeWidth={1} />
          ))}

          {/* комнаты */}
          {storey.rooms.map(r => {
            const pts = r.polygon.map(([x, y]) => `${X(x)},${Y(y)}`).join(" ");
            const cx = r.polygon.reduce((s, p) => s + X(p[0]), 0) / r.polygon.length;
            const cy = r.polygon.reduce((s, p) => s + Y(p[1]), 0) / r.polygon.length;
            return (
              <g key={r.id}>
                <polygon points={pts} fill="#f0f4f8" stroke="#cbd5e0" strokeWidth={1} />
                <text x={cx} y={cy - 6} textAnchor="middle" fontSize={11} fill="#2d3748">{names[r.id] || r.id}</text>
                <text x={cx} y={cy + 8} textAnchor="middle" fontSize={9} fill="#718096">{polyArea(r.polygon).toFixed(1)} м²</text>
              </g>
            );
          })}

          {/* стены — перетаскиваемые линии разреза */}
          {lines.v.map((l, i) => (
            <line
              key={`v${i}`} x1={X(l.coord)} y1={Y(l.span[0])} x2={X(l.coord)} y2={Y(l.span[1])}
              stroke={drag && drag.kind === "line" && drag.axis === "x" && Math.abs(drag.coord - l.coord) < 1e-3 ? "#0a84ff" : "#4a5568"}
              strokeWidth={5} strokeLinecap="round" style={{ cursor: "ew-resize" }}
              onPointerDown={e => { (e.target as Element).setPointerCapture(e.pointerId); setDrag({ kind: "line", axis: "x", coord: l.coord, start: l.coord }); }}
            />
          ))}
          {lines.h.map((l, i) => (
            <line
              key={`h${i}`} x1={X(l.span[0])} y1={Y(l.coord)} x2={X(l.span[1])} y2={Y(l.coord)}
              stroke={drag && drag.kind === "line" && drag.axis === "y" && Math.abs(drag.coord - l.coord) < 1e-3 ? "#0a84ff" : "#4a5568"}
              strokeWidth={5} strokeLinecap="round" style={{ cursor: "ns-resize" }}
              onPointerDown={e => { (e.target as Element).setPointerCapture(e.pointerId); setDrag({ kind: "line", axis: "y", coord: l.coord, start: l.coord }); }}
            />
          ))}

          {/* проёмы — перетаскиваемые вдоль стены */}
          {storey.openings.map((op, i) => {
            const oc = openingCenter(op);
            if (!oc) return null;
            const [wx, wy] = oc.c;
            const half = (op.width_m / 2) * PX_PER_M;
            const isDoor = op.kind === "door";
            const col = isDoor ? "#8b5cf6" : "#3182ce";
            const [x1, y1, x2, y2] = oc.horiz ? [X(wx) - half, Y(wy), X(wx) + half, Y(wy)] : [X(wx), Y(wy) - half, X(wx), Y(wy) + half];
            return (
              <g key={`op${i}`} style={{ cursor: "grab" }}
                 onPointerDown={e => { (e.target as Element).setPointerCapture(e.pointerId); setDrag({ kind: "opening", index: i }); }}>
                <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={col} strokeWidth={7} strokeLinecap="round" opacity={0.35} />
                <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={col} strokeWidth={3} strokeLinecap="round" />
                <circle cx={X(wx)} cy={Y(wy)} r={4} fill="#fff" stroke={col} strokeWidth={1.5} />
              </g>
            );
          })}

          {/* линейка-эталон масштаба внизу */}
          <g>
            <line x1={X(box.minX + PAD_M / 2)} y1={svgH - 10} x2={X(box.minX + PAD_M / 2 + ruler - 1)} y2={svgH - 10} stroke="#2d3748" strokeWidth={1.5} />
            {Array.from({ length: ruler }, (_, i) => (
              <g key={`rk${i}`}>
                <line x1={X(box.minX + PAD_M / 2 + i)} y1={svgH - 14} x2={X(box.minX + PAD_M / 2 + i)} y2={svgH - 6} stroke="#2d3748" strokeWidth={1} />
                <text x={X(box.minX + PAD_M / 2 + i)} y={svgH - 18} textAnchor="middle" fontSize={8} fill="#718096">{i}</text>
              </g>
            ))}
            <text x={X(box.minX + PAD_M / 2 + ruler)} y={svgH - 6} fontSize={8} fill="#718096">м</text>
          </g>
        </svg>
      </div>
    </div>
  );
}
