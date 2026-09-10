import { useEffect, useRef, useState } from "react";
import type { TranscriptEvent } from "../types/messages";

interface Props {
  segments: TranscriptEvent[];
}

/** 最下部と見なす余裕(px)。1行ぶん程度のずれは「最下部にいる」として扱う。 */
const BOTTOM_THRESHOLD_PX = 32;

export function TranscriptPanel({ segments }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // 会議中は最新の発話を追いたいので既定は追従。ユーザーが上を読み始めたら止める。
  const [followLatest, setFollowLatest] = useState(true);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setFollowLatest(distanceFromBottom <= BOTTOM_THRESHOLD_PX);
  };

  useEffect(() => {
    if (!followLatest) return;
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [segments, followLatest]);

  // 画面リサイズや縦積みへの切替で枠の高さが変わると最下部からずれるため、
  // 追従中はサイズ変化にも追いつかせる。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      if (!followLatest) return;
      el.scrollTop = el.scrollHeight;
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [followLatest]);

  return (
    <section className="panel transcript-panel">
      <h2>文字起こし</h2>
      {/* 枠の高さは親グリッドが決め、あふれた分はこの中だけでスクロールさせる。 */}
      <div className="panel-scroll" ref={scrollRef} onScroll={handleScroll}>
        {segments.length === 0 ? (
          <p className="transcript-empty">セッションを開始すると、ここに文字起こしが表示されます。</p>
        ) : (
          segments.map((segment) => (
            <p
              key={segment.segment_id}
              className={segment.is_final ? "segment-final" : "segment-interim"}
            >
              {segment.speaker_id && <span className="speaker-tag">{segment.speaker_id}</span>}
              {segment.text}
            </p>
          ))
        )}
      </div>
      {!followLatest && (
        <button
          type="button"
          className="follow-latest"
          onClick={() => setFollowLatest(true)}
        >
          最新へ移動
        </button>
      )}
    </section>
  );
}
