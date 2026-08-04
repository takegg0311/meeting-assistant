import type { TranscriptEvent } from "../types/messages";

interface Props {
  segments: TranscriptEvent[];
}

export function TranscriptPanel({ segments }: Props) {
  if (segments.length === 0) {
    return <p className="transcript-empty">セッションを開始すると、ここに文字起こしが表示されます。</p>;
  }

  return (
    <div className="transcript-panel">
      {segments.map((segment) => (
        <p key={segment.segment_id} className={segment.is_final ? "segment-final" : "segment-interim"}>
          {segment.speaker_id && <span className="speaker-tag">{segment.speaker_id}</span>}
          {segment.text}
        </p>
      ))}
    </div>
  );
}
