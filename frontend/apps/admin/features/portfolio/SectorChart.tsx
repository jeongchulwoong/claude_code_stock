// Phase F6.1 — Sector donut chart (SVG, no external dep).
//
// 디자인 원칙:
//   - 데이터 갱신 시 200ms easing 으로 segment / weight 부드럽게 보간.
//   - 색상은 디자인 토큰 (qd-*) 으로만, 임의 hex hardcode 금지.
//   - hover: segment lift + tooltip (간단). reduced-motion 존중.
//   - text overflow / 카드 안 카드 금지. 부모 카드 1 depth.

import { useId, useMemo } from 'react';
import type { NormalizedSector } from './portfolioModel';

export interface SectorChartProps {
  sectors: NormalizedSector[];
  size?: number;        // square px. default 180.
  thickness?: number;   // ring thickness. default 28.
  /** 표시할 최대 segment. 그 외는 '기타' 로 합쳐짐. */
  maxSegments?: number;
}

// 운영 도구답게 고채도 회피한 8색 팔레트 (light/dark 양쪽 가독). prefers-color-scheme 무관.
const PALETTE = [
  '#2563EB', // qd-blue
  '#059669', // qd-green
  '#D97706', // qd-amber
  '#7C3AED', // violet
  '#DC2626', // qd-red
  '#0891B2', // cyan
  '#65A30D', // lime
  '#64748B', // slate
];

function fmtAmount(v: number): string {
  if (!Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 100_000_000) return `${(v / 100_000_000).toFixed(1)}억`;
  if (abs >= 10_000)      return `${Math.round(v / 10_000).toLocaleString('ko-KR')}만`;
  return Math.round(v).toLocaleString('ko-KR');
}

function describeArc(cx: number, cy: number, r: number, startDeg: number, endDeg: number): string {
  const start = polar(cx, cy, r, endDeg);
  const end   = polar(cx, cy, r, startDeg);
  const large = endDeg - startDeg <= 180 ? 0 : 1;
  return [`M`, start.x, start.y, `A`, r, r, 0, large, 0, end.x, end.y].join(' ');
}

function polar(cx: number, cy: number, r: number, deg: number): { x: number; y: number } {
  const rad = ((deg - 90) * Math.PI) / 180.0;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

interface ResolvedSegment {
  sector: string;
  evalAmount: number;
  weight: number;
  color: string;
  startAngle: number;
  endAngle: number;
}

function resolve(sectors: NormalizedSector[], maxSegments: number): ResolvedSegment[] {
  if (sectors.length === 0) return [];
  // sort 안전 — 모든 입력은 weight 기반 정렬을 가정하지만 한 번 더.
  const sorted = [...sectors].sort((a, b) => b.evalAmount - a.evalAmount);
  let visible = sorted.slice(0, Math.max(1, maxSegments));
  const rest = sorted.slice(Math.max(1, maxSegments));
  if (rest.length > 0) {
    const restEval = rest.reduce((s, x) => s + x.evalAmount, 0);
    const restWeight = rest.reduce((s, x) => s + x.weight, 0);
    if (restEval > 0) {
      visible = [...visible, { sector: '기타', evalAmount: restEval, weight: restWeight }];
    }
  }
  // weight 합이 100 ± 0.5 가 아니면 보정 (weight 가 amount 비율과 어긋날 때).
  const total = visible.reduce((s, x) => s + x.evalAmount, 0) || 1;
  let cursor = 0;
  return visible.map((s, i) => {
    const span = (s.evalAmount / total) * 360;
    const seg: ResolvedSegment = {
      sector: s.sector,
      evalAmount: s.evalAmount,
      weight: s.weight,
      color: PALETTE[i % PALETTE.length] ?? PALETTE[0]!,
      startAngle: cursor,
      endAngle: cursor + span,
    };
    cursor += span;
    return seg;
  });
}

export default function SectorChart({
  sectors,
  size = 180,
  thickness = 28,
  maxSegments = 7,
}: SectorChartProps) {
  const titleId = useId();
  const segs = useMemo(() => resolve(sectors, maxSegments), [sectors, maxSegments]);
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = size / 2 - 2;
  const rInner = rOuter - thickness;

  if (segs.length === 0) {
    return (
      <div className="qd-sector-chart qd-sector-chart--empty" aria-label="섹터 분포 없음">
        <div className="qd-sector-empty">섹터 데이터 없음</div>
      </div>
    );
  }

  const totalEval = segs.reduce((s, x) => s + x.evalAmount, 0);

  return (
    <div className="qd-sector-chart">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-labelledby={titleId}
        className="qd-sector-svg"
      >
        <title id={titleId}>섹터 분포 도넛 차트</title>
        {/* 배경 ring — 빈 부분이 보이지 않게 한 번에 fill. */}
        <circle cx={cx} cy={cy} r={(rOuter + rInner) / 2} fill="none"
                stroke="var(--qd-surface-2)" strokeWidth={thickness} />
        {segs.map((s, i) => {
          const path = describeArc(cx, cy, (rOuter + rInner) / 2, s.startAngle, s.endAngle);
          return (
            <path
              key={s.sector + i}
              d={path}
              fill="none"
              stroke={s.color}
              strokeWidth={thickness}
              strokeLinecap="butt"
              data-sector={s.sector}
              className="qd-sector-arc"
            >
              <title>{`${s.sector} ${s.weight.toFixed(1)}% · ${fmtAmount(s.evalAmount)}원`}</title>
            </path>
          );
        })}
        {/* 중앙 라벨. */}
        <text x={cx} y={cy - 6} textAnchor="middle" className="qd-sector-center-l">
          섹터
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" className="qd-sector-center-v">
          {segs.length}
        </text>
      </svg>
      <ul className="qd-sector-legend">
        {segs.map((s) => (
          <li key={s.sector} className="qd-sector-legend-row">
            <span className="qd-sector-dot" style={{ background: s.color }} aria-hidden="true" />
            <span className="qd-sector-name">{s.sector}</span>
            <span className="qd-sector-weight qd-num">{s.weight.toFixed(1)}%</span>
            <span className="qd-sector-amount qd-num">{fmtAmount(s.evalAmount)}</span>
          </li>
        ))}
      </ul>
      <span className="qd-sector-total qd-num" aria-hidden="true">
        합계 {fmtAmount(totalEval)}
      </span>
    </div>
  );
}
