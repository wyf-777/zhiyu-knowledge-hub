import type { Citation } from '../types';

interface CitationMarkerProps {
  citation: Citation;
  onOpen: (id: number) => void;
}

export function CitationMarker({ citation, onOpen }: CitationMarkerProps) {
  return (
    <button className="citation-marker" onClick={() => onOpen(citation.id)} aria-label={`查看来源 ${citation.id}`}>
      <sup>{citation.id}</sup>
      <span className="citation-tooltip">{citation.documentName}<br />{citation.excerpt.slice(0, 70)}…</span>
    </button>
  );
}
